# Copyright 2025-2026 Dimensional Inc.
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

from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import queue
import re
import shutil
import threading
import time
from typing import Any, BinaryIO, Generic, TypeVar

import numpy as np
from numpy.typing import NDArray
import orjson

from dimos.core.global_config import GlobalConfig, NavigationTraceLevel, global_config
from dimos.navigation.diagnostics.schema import (
    NAVIGATION_TRACE_SCHEMA_VERSION,
    BlobKind,
    TraceProducer,
    trace_level_at_least,
)
from dimos.utils.logging_config import get_run_log_dir, setup_logger

logger = setup_logger()

_SENSITIVE_KEY_PARTS = (
    "account",
    "password",
    "passwd",
    "token",
    "username",
    "api_key",
    "apikey",
    "secret",
    "credential",
    "aes_128_key",
    "serial",
)
_COMMAND_SECRET_PATTERN = re.compile(
    r"(?P<prefix>(?:--)?(?:account|username|password|passwd|token|api[-_]?key|secret|"
    r"unitree-username|unitree-password|unitree-aes-128-key|unitree-serial)"
    r"(?:=|\s+))(?P<value>[^\s]+)",
    flags=re.IGNORECASE,
)
_REDACTED = "<redacted>"


@dataclass(slots=True)
class _ScalarItem:
    payload: dict[str, Any]
    estimated_bytes: int


@dataclass(slots=True)
class _JsonArtifactItem:
    relative_path: Path
    payload: Mapping[str, Any] | Callable[[], Mapping[str, Any]]
    index_payload: dict[str, Any]
    estimated_bytes: int
    redact_payload: bool


@dataclass(slots=True)
class _BlobItem:
    blob_kind: BlobKind
    array: NDArray[Any]
    metadata: dict[str, Any]
    stem: str
    nbytes: int


@dataclass(slots=True)
class _DropWindow:
    count: int = 0
    first_wall_ts: str | None = None
    last_wall_ts: str | None = None


_QueueItem = TypeVar("_QueueItem")


class _BoundedDeque(Generic[_QueueItem]):
    """Small non-blocking queue whose hot operations never wait on a mutex."""

    def __init__(self, maxsize: int) -> None:
        self._capacity = max(1, maxsize)
        self._items: deque[_QueueItem] = deque(maxlen=self._capacity)

    def put_nowait(self, item: _QueueItem) -> None:
        if len(self._items) >= self._capacity:
            raise queue.Full
        self._items.append(item)

    def get_nowait(self) -> _QueueItem:
        try:
            return self._items.popleft()
        except IndexError as exc:
            raise queue.Empty from exc

    def empty(self) -> bool:
        return not self._items


class TraceSink:
    """Bounded, producer-local navigation trace writer.

    Call sites must guard record construction with ``if sink.enabled`` so
    trace-off hot paths do not allocate dictionaries or read clocks.
    """

    _WRITER_POLL_SEC = 0.02
    _WRITER_YIELD_SEC = 0.001
    _PRODUCER_QUIET_SEC = 0.01
    # Keep a usable partial trace if a worker is terminated before the
    # footer can be written.  This is deliberately independent of close(),
    # whose bounded join is part of the control-plane latency contract.
    _WRITER_FLUSH_SEC = 0.5
    _JOIN_TIMEOUT_SEC = 0.1

    def __init__(
        self,
        producer: TraceProducer,
        *,
        config: GlobalConfig = global_config,
        run_log_dir: Path | None = None,
    ) -> None:
        self._producer = producer
        requested_level = config.navigation_trace_level
        if requested_level not in ("off", "summary", "full", "forensic"):
            requested_level = "off"
        self._requested_level: NavigationTraceLevel = requested_level
        self._effective_level: NavigationTraceLevel = "off"
        self._enabled = False
        self._scalar_queue: _BoundedDeque[_ScalarItem | _JsonArtifactItem] | None = None
        self._blob_queue: _BoundedDeque[_BlobItem] | None = None
        self._stop_event: threading.Event | None = None
        self._writer_ready: threading.Event | None = None
        self._writer_thread: threading.Thread | None = None
        self._output_path: Path | None = None
        self._navigation_dir: Path | None = None
        self._scalar_queued_bytes = 0
        self._blob_queued_bytes = 0
        self._scalar_accepted_bytes = 0
        self._blob_accepted_bytes = 0
        self._producer_seq = 0
        self._blob_seq = 0
        self._last_enqueue_ns = 0
        self._written_events = 0
        self._written_blobs = 0
        self._writer_error: str | None = None
        self._drop_windows: dict[str, _DropWindow] = {}
        self._allow_pointcloud_blobs = False
        self._allow_costmap_blobs = False
        self._trace_settings: dict[str, Any] | None = None
        self._sensitive_values: tuple[str, ...] = ()

        if self._requested_level == "off":
            return
        if self._requested_level == "forensic" and not config.navigation_trace_forensic_ack:
            logger.error(
                "navigation forensic tracing refused: "
                "set navigation_trace_forensic_ack=true after completing safety gates"
            )
            self._writer_error = "forensic_ack_required"
            return

        resolved_run_dir = run_log_dir or get_run_log_dir()
        if resolved_run_dir is None:
            env_run_dir = os.environ.get("DIMOS_RUN_LOG_DIR")
            resolved_run_dir = Path(env_run_dir) if env_run_dir else None
        if resolved_run_dir is None:
            logger.error("navigation tracing disabled: current run log directory is unavailable")
            self._writer_error = "run_log_dir_unavailable"
            return

        try:
            navigation_dir = resolved_run_dir / "navigation"
            navigation_dir.mkdir(parents=True, exist_ok=True)
            (navigation_dir / "plans").mkdir(exist_ok=True)
            (navigation_dir / "blobs").mkdir(exist_ok=True)

            self._navigation_dir = navigation_dir
            self._output_path = navigation_dir / f"{producer}-{os.getpid()}.jsonl"
            self._scalar_queue = _BoundedDeque(
                maxsize=max(1, config.navigation_trace_scalar_queue_items)
            )
            self._blob_queue = _BoundedDeque(
                maxsize=max(1, config.navigation_trace_blob_queue_items)
            )
            self._stop_event = threading.Event()
            self._writer_ready = threading.Event()
            self._scalar_max_bytes = max(0, config.navigation_trace_scalar_max_bytes_per_producer)
            self._blob_max_bytes = max(0, config.navigation_trace_blob_max_bytes_per_producer)
            self._blob_max_item_bytes = max(0, config.navigation_trace_blob_max_item_bytes)
            self._min_free_disk_bytes = max(0, config.navigation_trace_min_free_disk_bytes)
            self._effective_level = self._requested_level
            self._allow_pointcloud_blobs = self._requested_level == "forensic"
            self._allow_costmap_blobs = trace_level_at_least(self._requested_level, "full")
            self._trace_settings = {
                key: value
                for key, value in config.model_dump(mode="json").items()
                if key.startswith("navigation_trace_")
            }
            self._sensitive_values = tuple(
                value
                for value in (
                    config.unitree_username,
                    config.unitree_password,
                    config.unitree_aes_128_key,
                )
                if isinstance(value, str) and value
            )
            self._enabled = True
            self._writer_thread = threading.Thread(
                target=self._writer_loop,
                name=f"nav-trace-{producer}",
                daemon=True,
            )
            self._writer_thread.start()
            if not self._writer_ready.wait(timeout=self._JOIN_TIMEOUT_SEC):
                self._writer_error = "writer_startup_timeout"
                self._enabled = False
                self._effective_level = "off"
                self._stop_event.set()
        except Exception as exc:
            self._disable_from_exception(exc)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def requested_level(self) -> NavigationTraceLevel:
        return self._requested_level

    @property
    def effective_level(self) -> NavigationTraceLevel:
        return self._effective_level

    @property
    def output_path(self) -> Path | None:
        return self._output_path

    @property
    def has_background_resources(self) -> bool:
        """Expose the off-mode invariant for tests and health reporting."""
        return any(
            resource is not None
            for resource in (
                self._scalar_queue,
                self._blob_queue,
                self._stop_event,
                self._writer_ready,
                self._writer_thread,
            )
        )

    @property
    def writer_error(self) -> str | None:
        return self._writer_error

    def accepts(self, minimum: NavigationTraceLevel) -> bool:
        """Return whether the current effective level includes minimum."""
        return self._enabled and trace_level_at_least(self._effective_level, minimum)

    def record(
        self,
        event: str,
        fields: Mapping[str, Any] | None = None,
        *,
        estimated_bytes: int = 512,
    ) -> bool:
        """Queue one scalar event without waiting for the writer."""
        if not self._enabled:
            return False
        try:
            scalar_queue = self._scalar_queue
            if scalar_queue is None:
                return False
            estimate = max(1, estimated_bytes)
            if self._scalar_accepted_bytes + estimate > self._scalar_max_bytes:
                self._register_drop("scalar_budget_exhausted")
                self._degrade_scalar()
                return False

            self._producer_seq += 1
            event_monotonic_ns = time.monotonic_ns()
            payload = {
                "schema_version": NAVIGATION_TRACE_SCHEMA_VERSION,
                "event": event,
                "producer": self._producer,
                "producer_seq": self._producer_seq,
                "_trace_wall_time_ns": time.time_ns(),
                "monotonic_ns": event_monotonic_ns,
            }
            if fields is not None:
                payload.update(fields)
            scalar_queue.put_nowait(_ScalarItem(payload=payload, estimated_bytes=estimate))
            self._last_enqueue_ns = event_monotonic_ns
            self._scalar_queued_bytes += estimate
            self._scalar_accepted_bytes += estimate
            return True
        except queue.Full:
            self._register_drop("scalar_queue_full")
            self._degrade_scalar()
            return False
        except Exception as exc:
            self._disable_from_exception(exc)
            return False

    def record_blob(
        self,
        blob_kind: BlobKind,
        array: NDArray[Any],
        metadata: Mapping[str, Any],
        *,
        stem: str,
    ) -> bool:
        """Queue a stable NumPy array for background NPY persistence.

        The method never copies, hashes, compresses, or converts the array.
        The caller must not mutate the array after a successful enqueue.
        """
        if not self._enabled:
            return False
        try:
            if blob_kind == "pointcloud" and not self._allow_pointcloud_blobs:
                self._register_drop("pointcloud_blob_disabled")
                return False
            if blob_kind == "costmap" and not self._allow_costmap_blobs:
                self._register_drop("costmap_blob_disabled")
                return False
            blob_queue = self._blob_queue
            if blob_queue is None:
                return False
            nbytes = int(array.nbytes)
            if nbytes > self._blob_max_item_bytes:
                self._register_drop("blob_item_too_large")
                self._degrade_blob(blob_kind)
                return False
            if self._blob_accepted_bytes + nbytes > self._blob_max_bytes:
                self._register_drop("blob_budget_exhausted")
                self._degrade_blob(blob_kind)
                return False

            blob_queue.put_nowait(
                _BlobItem(
                    blob_kind=blob_kind,
                    array=array,
                    metadata=dict(metadata),
                    stem=stem,
                    nbytes=nbytes,
                )
            )
            self._last_enqueue_ns = time.monotonic_ns()
            self._blob_queued_bytes += nbytes
            self._blob_accepted_bytes += nbytes
            return True
        except queue.Full:
            self._register_drop("blob_queue_full")
            self._degrade_blob(blob_kind)
            return False
        except Exception as exc:
            self._disable_from_exception(exc)
            return False

    def record_json_artifact(
        self,
        relative_path: Path,
        payload: Mapping[str, Any] | Callable[[], Mapping[str, Any]],
        index_fields: Mapping[str, Any],
        *,
        estimated_bytes: int,
        redact_payload: bool = True,
    ) -> bool:
        """Queue one JSON artifact for background construction and serialization."""
        if not self._enabled:
            return False
        try:
            scalar_queue = self._scalar_queue
            if scalar_queue is None:
                return False
            if (
                relative_path.is_absolute()
                or not relative_path.parts
                or relative_path.parts[0] != "plans"
                or ".." in relative_path.parts
            ):
                self._register_drop("invalid_artifact_path")
                return False
            estimate = max(1, estimated_bytes)
            if self._scalar_accepted_bytes + estimate > self._scalar_max_bytes:
                self._register_drop("scalar_budget_exhausted")
                self._degrade_scalar()
                return False

            self._producer_seq += 1
            event_monotonic_ns = time.monotonic_ns()
            index_payload = {
                "schema_version": NAVIGATION_TRACE_SCHEMA_VERSION,
                "event": "json_artifact_saved",
                "producer": self._producer,
                "producer_seq": self._producer_seq,
                "artifact_path": relative_path.as_posix(),
                "_trace_wall_time_ns": time.time_ns(),
                "monotonic_ns": event_monotonic_ns,
                **index_fields,
            }
            scalar_queue.put_nowait(
                _JsonArtifactItem(
                    relative_path=relative_path,
                    payload=payload if callable(payload) else dict(payload),
                    index_payload=index_payload,
                    estimated_bytes=estimate,
                    redact_payload=redact_payload,
                )
            )
            self._last_enqueue_ns = event_monotonic_ns
            self._scalar_queued_bytes += estimate
            self._scalar_accepted_bytes += estimate
            return True
        except queue.Full:
            self._register_drop("scalar_queue_full")
            self._degrade_scalar()
            return False
        except Exception as exc:
            self._disable_from_exception(exc)
            return False

    def close(self) -> None:
        """Request writer shutdown, waiting at most 100 ms."""
        stop_event = self._stop_event
        writer_thread = self._writer_thread
        if stop_event is None or writer_thread is None:
            return
        stop_event.set()
        writer_thread.join(timeout=self._JOIN_TIMEOUT_SEC)
        if writer_thread.is_alive():
            self._writer_error = self._writer_error or "writer_shutdown_timeout"
        self._enabled = False

    def disable(self, exc: Exception) -> None:
        """Fail closed after any producer-side diagnostic exception."""
        self._disable_from_exception(exc)

    def _writer_loop(self) -> None:
        output_path = self._output_path
        stop_event = self._stop_event
        if output_path is None or stop_event is None:
            return
        try:
            _lower_current_thread_priority()
            with output_path.open("ab") as stream:
                self._write_json_line(
                    stream,
                    {
                        "schema_version": NAVIGATION_TRACE_SCHEMA_VERSION,
                        "event": "trace_header",
                        "producer": self._producer,
                        "pid": os.getpid(),
                        "requested_trace_level": self._requested_level,
                        "effective_trace_level": self._effective_level,
                        "worker_startup_parameters": {
                            "trace": self._trace_settings,
                            "output_file": output_path.name,
                        },
                        "wall_ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                        "monotonic_ns": time.monotonic_ns(),
                    },
                )
                stream.flush()
                last_flush = time.monotonic()
                writer_ready = self._writer_ready
                if writer_ready is not None:
                    writer_ready.set()
                while (
                    not stop_event.is_set()
                    or self._queue_has_items(self._scalar_queue)
                    or self._queue_has_items(self._blob_queue)
                ):
                    now = time.monotonic()
                    if now - last_flush >= self._WRITER_FLUSH_SEC:
                        stream.flush()
                        last_flush = now
                    if not stop_event.is_set() and self._producer_is_busy():
                        stop_event.wait(self._PRODUCER_QUIET_SEC)
                        continue
                    worked = self._write_next_scalar(stream)
                    worked = self._write_next_blob(stream) or worked
                    if not worked:
                        stop_event.wait(self._WRITER_POLL_SEC)
                    elif not stop_event.is_set():
                        # JSON encoding is Python/GIL-heavy. Explicitly yield
                        # after each item so the diagnostic writer cannot hold
                        # a control callback behind the interpreter switch
                        # interval during a queue burst.
                        stop_event.wait(self._WRITER_YIELD_SEC)

                self._write_drop_summary(stream)
                self._write_json_line(
                    stream,
                    {
                        "schema_version": NAVIGATION_TRACE_SCHEMA_VERSION,
                        "event": "trace_footer",
                        "producer": self._producer,
                        "requested_trace_level": self._requested_level,
                        "effective_trace_level": self._effective_level,
                        "written_events": self._written_events,
                        "written_blobs": self._written_blobs,
                        "estimated_scalar_bytes": self._scalar_accepted_bytes,
                        "blob_bytes": self._blob_accepted_bytes,
                        "wall_ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                        "monotonic_ns": time.monotonic_ns(),
                    },
                )
                stream.flush()
        except Exception as exc:
            self._disable_from_exception(exc)
        finally:
            writer_ready = self._writer_ready
            if writer_ready is not None:
                writer_ready.set()

    def _write_next_scalar(self, stream: BinaryIO) -> bool:
        scalar_queue = self._scalar_queue
        if scalar_queue is None:
            return False
        try:
            item = scalar_queue.get_nowait()
        except queue.Empty:
            return False
        self._scalar_queued_bytes = max(0, self._scalar_queued_bytes - item.estimated_bytes)
        if isinstance(item, _JsonArtifactItem):
            navigation_dir = self._navigation_dir
            if navigation_dir is None:
                self._register_drop("navigation_dir_unavailable")
                return True
            artifact_path = navigation_dir / item.relative_path
            artifact_payload = item.payload() if callable(item.payload) else item.payload
            if item.redact_payload:
                artifact_payload = redact_sensitive(artifact_payload)
            with artifact_path.open("wb") as artifact_stream:
                artifact_stream.write(orjson.dumps(artifact_payload, default=_json_default))
                artifact_stream.write(b"\n")
            self._write_json_line(stream, item.index_payload)
        else:
            self._write_json_line(stream, item.payload)
        self._written_events += 1
        return True

    def _producer_is_busy(self) -> bool:
        last_enqueue_ns = self._last_enqueue_ns
        return last_enqueue_ns != 0 and time.monotonic_ns() - last_enqueue_ns < int(
            self._PRODUCER_QUIET_SEC * 1_000_000_000
        )

    def _write_next_blob(self, stream: BinaryIO) -> bool:
        blob_queue = self._blob_queue
        navigation_dir = self._navigation_dir
        if blob_queue is None or navigation_dir is None:
            return False
        try:
            item = blob_queue.get_nowait()
        except queue.Empty:
            return False
        self._blob_queued_bytes = max(0, self._blob_queued_bytes - item.nbytes)

        if shutil.disk_usage(navigation_dir).free < self._min_free_disk_bytes:
            self._register_drop("insufficient_free_disk")
            self._degrade_blob(item.blob_kind)
            return True

        array = item.array
        metadata = item.metadata
        if item.blob_kind == "pointcloud":
            array, preparation = _prepare_pointcloud_roi(array, metadata)
            metadata = {**metadata, **preparation}

        self._blob_seq += 1
        safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "-", item.stem).strip(".-") or "blob"
        file_name = f"{safe_stem}-{self._blob_seq:06d}.npy"
        relative_path = Path("blobs") / file_name
        np.save(navigation_dir / relative_path, array, allow_pickle=False)
        self._write_json_line(
            stream,
            {
                "schema_version": NAVIGATION_TRACE_SCHEMA_VERSION,
                "event": "blob_saved",
                "producer": self._producer,
                "blob_kind": item.blob_kind,
                "blob_path": relative_path.as_posix(),
                "shape": list(array.shape),
                "dtype": str(array.dtype),
                "nbytes": int(array.nbytes),
                "source_nbytes": item.nbytes,
                "metadata": metadata,
                "wall_ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "monotonic_ns": time.monotonic_ns(),
            },
        )
        self._written_blobs += 1
        return True

    def _write_drop_summary(self, stream: BinaryIO) -> None:
        if not self._drop_windows:
            return
        self._write_json_line(
            stream,
            {
                "schema_version": NAVIGATION_TRACE_SCHEMA_VERSION,
                "event": "trace_drop_summary",
                "producer": self._producer,
                "drops": {
                    reason: {
                        "count": window.count,
                        "first_wall_ts": window.first_wall_ts,
                        "last_wall_ts": window.last_wall_ts,
                    }
                    for reason, window in sorted(self._drop_windows.items())
                },
                "effective_trace_level": self._effective_level,
                "wall_ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "monotonic_ns": time.monotonic_ns(),
            },
        )

    def _register_drop(self, reason: str) -> None:
        timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        window = self._drop_windows.setdefault(reason, _DropWindow())
        window.count += 1
        if window.first_wall_ts is None:
            window.first_wall_ts = timestamp
        window.last_wall_ts = timestamp

    def _degrade_blob(self, blob_kind: BlobKind) -> None:
        if blob_kind == "pointcloud" and self._allow_pointcloud_blobs:
            self._allow_pointcloud_blobs = False
            self._effective_level = "full"
            return
        if self._allow_costmap_blobs:
            self._allow_costmap_blobs = False

    def _degrade_scalar(self) -> None:
        if self._effective_level in ("full", "forensic"):
            self._allow_pointcloud_blobs = False
            self._allow_costmap_blobs = False
            self._effective_level = "summary"
        elif self._effective_level == "summary":
            self._effective_level = "off"
            self._enabled = False
            stop_event = self._stop_event
            if stop_event is not None:
                stop_event.set()

    def _disable_from_exception(self, exc: Exception) -> None:
        self._writer_error = f"{type(exc).__name__}: {exc}"
        self._effective_level = "off"
        self._enabled = False
        stop_event = self._stop_event
        if stop_event is not None:
            stop_event.set()

    @staticmethod
    def _queue_has_items(items: _BoundedDeque[Any] | None) -> bool:
        return items is not None and not items.empty()

    def _write_json_line(self, stream: BinaryIO, payload: Mapping[str, Any]) -> None:
        prepared = dict(payload)
        wall_time_ns = prepared.pop("_trace_wall_time_ns", None)
        if wall_time_ns is not None:
            prepared["wall_ts"] = datetime.fromtimestamp(
                int(wall_time_ns) / 1_000_000_000,
                timezone.utc,
            ).isoformat(timespec="milliseconds")
        stream.write(
            orjson.dumps(
                redact_sensitive(prepared, secrets=self._sensitive_values),
                default=_json_default,
            )
        )
        stream.write(b"\n")


def _prepare_pointcloud_roi(
    points: NDArray[Any],
    metadata: Mapping[str, Any],
) -> tuple[NDArray[np.float32], dict[str, Any]]:
    """Crop and voxel-sample a point cloud in the writer thread."""
    source = np.asarray(points)
    if source.ndim != 2 or source.shape[1] < 3:
        raise ValueError("pointcloud blob must have shape (N, >=3)")
    xyz = source[:, :3]
    raw_bounds = metadata.get("roi_bounds_m", [-5.0, 5.0, -5.0, 5.0, -2.0, 2.0])
    if not isinstance(raw_bounds, (list, tuple)) or len(raw_bounds) != 6:
        raise ValueError("roi_bounds_m must contain xmin,xmax,ymin,ymax,zmin,zmax")
    bounds = tuple(float(value) for value in raw_bounds)
    finite = np.all(np.isfinite(xyz), axis=1)
    inside = (
        finite
        & (xyz[:, 0] >= bounds[0])
        & (xyz[:, 0] <= bounds[1])
        & (xyz[:, 1] >= bounds[2])
        & (xyz[:, 1] <= bounds[3])
        & (xyz[:, 2] >= bounds[4])
        & (xyz[:, 2] <= bounds[5])
    )
    cropped = xyz[inside]
    voxel_size = max(0.0, float(metadata.get("voxel_size_m", 0.1)))
    if voxel_size > 0.0 and len(cropped) > 1:
        voxel_keys = np.floor(cropped / voxel_size).astype(np.int64)
        _, indices = np.unique(voxel_keys, axis=0, return_index=True)
        cropped = cropped[np.sort(indices)]
    result = np.ascontiguousarray(cropped, dtype=np.float32)
    bounds_observed: list[list[float]] | None = None
    if len(result):
        bounds_observed = [
            result.min(axis=0).astype(float).tolist(),
            result.max(axis=0).astype(float).tolist(),
        ]
    return result, {
        "source_point_count": len(source),
        "finite_point_count": int(np.count_nonzero(finite)),
        "roi_point_count": len(result),
        "roi_observed_bounds_m": bounds_observed,
        "roi_processing_thread": "trace_writer",
    }


def _lower_current_thread_priority() -> None:
    """Best-effort Linux niceness so diagnostics do not preempt control."""
    try:
        if hasattr(os, "SCHED_IDLE") and hasattr(os, "sched_setscheduler"):
            os.sched_setscheduler(0, os.SCHED_IDLE, os.sched_param(0))
        if hasattr(os, "setpriority") and hasattr(os, "PRIO_PROCESS"):
            os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)
    except OSError:
        pass


def redact_sensitive(
    value: Any,
    *,
    key: str | None = None,
    secrets: tuple[str, ...] = (),
) -> Any:
    """Return a JSON-safe structure with credentials removed."""
    if key is not None and any(part in key.lower() for part in _SENSITIVE_KEY_PARTS):
        return _REDACTED
    if isinstance(value, Mapping):
        return {
            str(child_key): redact_sensitive(
                child_value,
                key=str(child_key),
                secrets=secrets,
            )
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(item, secrets=secrets) for item in value]
    if isinstance(value, tuple):
        return [redact_sensitive(item, secrets=secrets) for item in value]
    if isinstance(value, str):
        redacted = _COMMAND_SECRET_PATTERN.sub(
            lambda match: f"{match.group('prefix')}{_REDACTED}",
            value,
        )
        for secret in sorted(secrets, key=len, reverse=True):
            redacted = redacted.replace(secret, _REDACTED)
        return redacted
    return value


def isolate_trace_failure(trace: Any, exc: Exception) -> None:
    """Disable a real sink without allowing diagnostic cleanup to escape."""
    try:
        disable = getattr(trace, "disable", None)
        if callable(disable):
            disable(exc)
    except Exception:
        pass


def _json_default(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")
