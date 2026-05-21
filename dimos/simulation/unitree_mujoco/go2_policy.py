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

"""ONNX locomotion for Unitree's official Go2 MuJoCo model (sensor naming differs from menagerie)."""

from __future__ import annotations

from typing import Any

import mujoco
import numpy as np

from dimos.simulation.mujoco.policy import Go1OnnxController


def _quat_wxyz_to_rot(q: np.ndarray[Any, Any]) -> np.ndarray[Any, Any]:
    w, x, y, z = q
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
        ],
        dtype=np.float32,
    )


class UnitreeGo2OnnxController(Go1OnnxController):
    """Go1 policy with observations from ``unitree_robots/go2/go2.xml`` sensors."""

    def _policy_linvel(self, model: mujoco.MjModel, data: mujoco.MjData) -> np.ndarray[Any, Any]:
        del model
        quat = data.sensor("imu_quat").data.astype(np.float32)
        rot = _quat_wxyz_to_rot(quat)
        return rot.T @ data.sensor("frame_vel").data.astype(np.float32)

    def _policy_gyro(self, model: mujoco.MjModel, data: mujoco.MjData) -> np.ndarray[Any, Any]:
        del model
        return data.sensor("imu_gyro").data.astype(np.float32)

    def _policy_gravity(self, model: mujoco.MjModel, data: mujoco.MjData) -> np.ndarray[Any, Any]:
        imu_xmat = data.site_xmat[model.site("imu").id].reshape(3, 3)
        return imu_xmat.T @ np.array([0, 0, -1], dtype=np.float32)
