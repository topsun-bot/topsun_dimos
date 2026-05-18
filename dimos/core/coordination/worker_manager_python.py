# Copyright 2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

from collections.abc import Iterable, Mapping
import threading
import time
from typing import TYPE_CHECKING, Any, cast

from rpyc.utils.server import ThreadedServer

from dimos.core.coordination.python_worker import PythonWorker
from dimos.core.coordination.rpyc_services import WorkerRpycService
from dimos.core.global_config import GlobalConfig
from dimos.core.module import ModuleBase, ModuleSpec
from dimos.core.rpc_client import ModuleProxyProtocol, RPCClient
from dimos.utils.logging_config import setup_logger
from dimos.utils.safe_thread_map import safe_thread_map

if TYPE_CHECKING:
    from dimos.core.resource_monitor.monitor import StatsMonitor

logger = setup_logger()


class _LocalModuleActor:
    """Small actor shim for modules deployed in the coordinator process."""

    def __init__(
        self,
        module_id: int,
        instances: dict[int, ModuleBase],
        listen_host: str,
    ) -> None:
        self._module_id = module_id
        self._instances = instances
        self._listen_host = listen_host
        self._rpyc_server: ThreadedServer | None = None
        self._rpyc_thread: threading.Thread | None = None

    def start_rpyc(self) -> int:
        if self._rpyc_server is not None:
            return int(self._rpyc_server.port)

        class LocalWorkerRpycService(WorkerRpycService):  # type: ignore[misc]
            _instances = self._instances

        self._rpyc_server = ThreadedServer(
            LocalWorkerRpycService,
            port=0,
            hostname=self._listen_host,
            protocol_config={
                "allow_all_attrs": True,
                "allow_public_attrs": True,
                "allow_pickle": True,
            },
        )
        self._rpyc_thread = threading.Thread(target=self._rpyc_server.start, daemon=True)
        self._rpyc_thread.start()
        deadline = time.monotonic() + 5.0
        while not self._rpyc_server.active:
            if not self._rpyc_thread.is_alive():
                raise RuntimeError("local rpyc server thread died before listening")
            if time.monotonic() > deadline:
                raise RuntimeError("local rpyc server failed to start listening within 5s")
            time.sleep(0.001)
        return int(self._rpyc_server.port)

    def close(self) -> None:
        if self._rpyc_server is not None:
            self._rpyc_server.close()
            self._rpyc_server = None
        if self._rpyc_thread is not None:
            self._rpyc_thread.join(timeout=5)
            self._rpyc_thread = None


class WorkerManagerPython:
    deployment_identifier: str = "python"

    def __init__(self, g: GlobalConfig) -> None:
        self._cfg = g
        self._n_workers = g.n_workers
        self._workers: list[PythonWorker] = []
        self._local_modules: dict[int, ModuleBase] = {}
        self._local_actors: dict[int, _LocalModuleActor] = {}
        self._next_local_module_id = 1
        self._closed = False
        self._started = False
        self._stats_monitor: StatsMonitor | None = None

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        if self._n_workers == 0:
            logger.info("Worker pool disabled; deploying modules in-process.")
            return
        for _ in range(self._n_workers):
            worker = PythonWorker()
            worker.start_process()
            self._workers.append(worker)
        logger.info("Worker pool started.", n_workers=self._n_workers)

        if self._cfg.dtop:
            from dimos.core.resource_monitor.monitor import StatsMonitor

            self._stats_monitor = StatsMonitor(self)
            self._stats_monitor.start()

    def add_workers(self, n: int) -> None:
        """Spawn *n* additional worker processes into the pool."""
        if self._closed:
            raise RuntimeError("WorkerManager is closed")
        if not self._started:
            raise RuntimeError("WorkerManager not started; call start() first")
        for _ in range(n):
            worker = PythonWorker()
            worker.start_process()
            self._workers.append(worker)
        self._n_workers += n
        logger.info("Added workers to pool.", added=n, total=self._n_workers)

    def _select_worker(self) -> PythonWorker:
        return min(self._workers, key=lambda w: w.module_count)

    def deploy(
        self,
        module_class: type[ModuleBase],
        global_config: GlobalConfig,
        kwargs: dict[str, Any],
    ) -> ModuleProxyProtocol:
        if self._closed:
            raise RuntimeError("WorkerManager is closed")

        if not self._started:
            self.start()

        if self._n_workers == 0:
            kwargs = dict(kwargs)
            kwargs["g"] = global_config
            module = module_class(**kwargs)
            module_id = self._next_local_module_id
            self._next_local_module_id += 1
            local_actor = _LocalModuleActor(
                module_id, self._local_modules, global_config.listen_host
            )
            local_proxy = cast("Any", module)
            local_proxy.actor_instance = local_actor
            local_proxy.ref = local_actor
            self._local_modules[module_id] = module
            self._local_actors[module_id] = local_actor
            logger.info(
                "Deployed module in-process.",
                module=module_class.__name__,
                module_id=module_id,
            )
            return cast("ModuleProxyProtocol", module)

        worker = self._select_worker()
        remote_actor = worker.deploy_module(module_class, global_config, kwargs=kwargs)
        return RPCClient(remote_actor, module_class)

    def deploy_fresh(
        self,
        module_class: type[ModuleBase],
        global_config: GlobalConfig,
        kwargs: dict[str, Any],
    ) -> ModuleProxyProtocol:
        """Spawn a brand-new worker process and deploy *module_class* on it.

        Used by restart so the new module is imported by a Python process with
        a clean ``sys.modules`` — existing workers would reuse the old class
        object even after ``importlib.reload`` in the parent.
        """
        if self._closed:
            raise RuntimeError("WorkerManager is closed")
        if not self._started:
            self.start()

        if self._n_workers == 0:
            return self.deploy(module_class, global_config, kwargs)

        worker = PythonWorker()
        worker.start_process()
        self._workers.append(worker)
        self._n_workers += 1
        actor = worker.deploy_module(module_class, global_config, kwargs=kwargs)
        return RPCClient(actor, module_class)

    def undeploy(self, proxy: ModuleProxyProtocol) -> None:
        """Undeploy a module and shut down its worker if it is now empty."""
        actor = getattr(proxy, "actor_instance", None)
        if isinstance(actor, _LocalModuleActor):
            self._local_modules.pop(actor._module_id, None)
            self._local_actors.pop(actor._module_id, None)
            actor.close()
            return

        if actor is None:
            raise ValueError("Proxy has no actor_instance. Cannot undeploy.")

        module_id = actor._module_id
        target: PythonWorker | None = None
        for worker in self._workers:
            if module_id in worker._modules:
                target = worker
                break
        if target is None:
            raise ValueError(f"No worker holds module_id={module_id}")

        target.undeploy_module(module_id)

        if not target._modules:
            target.shutdown()
            self._workers.remove(target)
            self._n_workers = max(0, self._n_workers - 1)

    def deploy_parallel(
        self, specs: Iterable[ModuleSpec], blueprint_args: Mapping[str, Mapping[str, Any]]
    ) -> list[ModuleProxyProtocol]:
        if self._closed:
            raise RuntimeError("WorkerManager is closed")

        specs = list(specs)
        if len(specs) == 0:
            return []

        if not self._started:
            self.start()

        if self._n_workers == 0:
            modules: list[ModuleProxyProtocol] = []
            for module_class, _global_config, kwargs in specs:
                kwargs.update(blueprint_args.get(module_class.name, {}))
                modules.append(self.deploy(module_class, _global_config, kwargs))
            return modules

        # Pre-assign workers sequentially (so least-loaded accounting is
        # correct), then deploy concurrently via threads. The per-worker lock
        # serializes deploys that land on the same worker process.
        assignments: list[tuple[PythonWorker, type[ModuleBase], GlobalConfig, dict[str, Any]]] = []
        for module_class, global_config, kwargs in specs:
            worker = self._select_worker()
            worker.reserve_slot()
            kwargs.update(blueprint_args.get(module_class.name, {}))
            assignments.append((worker, module_class, global_config, kwargs))

        try:
            # item: (worker, module_class, global_config, kwargs)
            return safe_thread_map(
                assignments,
                lambda item: RPCClient(item[0].deploy_module(item[1], item[2], item[3]), item[1]),
            )
        except:
            self.stop()
            raise

    def health_check(self) -> bool:
        if self._n_workers == 0:
            return True
        if len(self._workers) == 0:
            logger.error("health_check: no workers found")
            return False
        for w in self._workers:
            if w.pid is None:
                logger.error("health_check: worker died", worker_id=w.worker_id)
                return False
        return True

    def suppress_console(self) -> None:
        for worker in self._workers:
            worker.suppress_console()

    @property
    def workers(self) -> list[PythonWorker]:
        return list(self._workers)

    def stop(self) -> None:
        if self._closed:
            return
        self._closed = True

        if self._stats_monitor is not None:
            self._stats_monitor.stop()
            self._stats_monitor = None

        logger.info("Shutting down all workers...")

        if self._n_workers == 0:
            for actor in reversed(list(self._local_actors.values())):
                actor.close()
            self._local_modules.clear()
            self._local_actors.clear()
            logger.info("All in-process modules released")
            return

        for worker in reversed(self._workers):
            try:
                worker.shutdown()
            except Exception as e:
                logger.error(f"Error shutting down worker: {e}", exc_info=True)

        self._workers.clear()

        logger.info("All workers shut down")
