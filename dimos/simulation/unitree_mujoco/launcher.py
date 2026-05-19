#!/usr/bin/env python3
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

"""DimOS launcher for Unitree's official MuJoCo simulator (DDS + ONNX locomotion)."""

from __future__ import annotations

import base64
import json
import pickle
import signal
import sys
import threading
import time
from typing import Any

import mujoco
import mujoco.viewer
import numpy as np

from dimos.core.global_config import GlobalConfig
from dimos.simulation.mujoco.model import _get_data_dir
from dimos.simulation.mujoco.mujoco_process import MockController
from dimos.simulation.mujoco.shared_memory import ShmReader
from dimos.simulation.unitree_mujoco.constants import UNITREE_DDS_DOMAIN_ID, UNITREE_DDS_INTERFACE
from dimos.simulation.unitree_mujoco.dimos_bridge import DimosUnitreeBridge
from dimos.simulation.unitree_mujoco.scenes import resolve_robot_scene
from dimos.utils.data import get_data
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

locker = threading.Lock()


def _run(config: GlobalConfig, shm: ShmReader) -> None:
    get_data("mujoco_sim")
    scene_path = resolve_robot_scene(config)
    policy_path = _get_data_dir() / "unitree_go1_policy.onnx"
    if not policy_path.is_file():
        raise FileNotFoundError(f"Missing ONNX policy at {policy_path}")

    mj_model = mujoco.MjModel.from_xml_path(str(scene_path))
    mj_data = mujoco.MjData(mj_model)

    pos = config.mujoco_start_pos_float
    mj_data.qpos[0] = pos[0]
    mj_data.qpos[1] = pos[1]
    mj_data.qpos[2] = 0.27
    mujoco.mj_forward(mj_model, mj_data)

    controller = MockController(shm)

    from unitree_sdk2py.core.channel import ChannelFactoryInitialize

    ChannelFactoryInitialize(UNITREE_DDS_DOMAIN_ID, UNITREE_DDS_INTERFACE)

    bridge = DimosUnitreeBridge(mj_model, mj_data, controller, policy_path)

    viewer = mujoco.viewer.launch_passive(mj_model, mj_data)
    shm.signal_ready()

    def simulation_loop() -> None:
        while viewer.is_running() and not shm.should_stop():
            step_start = time.perf_counter()
            locker.acquire()
            bridge.apply_locomotion()
            mujoco.mj_step(mj_model, mj_data)
            pos_xyz = mj_data.qpos[0:3].copy()
            quat = mj_data.qpos[3:7].copy()
            shm.write_odom(pos_xyz, quat, time.time())
            locker.release()
            elapsed = time.perf_counter() - step_start
            sleep_t = mj_model.opt.timestep - elapsed
            if sleep_t > 0:
                time.sleep(sleep_t)

    def viewer_loop() -> None:
        while viewer.is_running() and not shm.should_stop():
            locker.acquire()
            viewer.sync()
            locker.release()
            time.sleep(0.02)

    sim_thread = threading.Thread(target=simulation_loop, name="unitree-mujoco-sim", daemon=True)
    view_thread = threading.Thread(target=viewer_loop, name="unitree-mujoco-viewer", daemon=True)
    sim_thread.start()
    view_thread.start()
    sim_thread.join()
    view_thread.join()


def main() -> None:
    if len(sys.argv) < 3:
        logger.error("Usage: launcher.py <config_b64> <shm_names_json>")
        sys.exit(1)

    config: GlobalConfig = pickle.loads(base64.b64decode(sys.argv[1]))  # noqa: S301
    shm_names = json.loads(sys.argv[2])
    shm = ShmReader(shm_names)

    def _stop(_signum: int, _frame: Any) -> None:
        shm.signal_stop()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    try:
        _run(config, shm)
    except Exception:
        logger.exception("Unitree MuJoCo launcher failed")
        raise
    finally:
        shm.signal_stop()


if __name__ == "__main__":
    main()
