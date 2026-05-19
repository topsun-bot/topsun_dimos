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

    def get_obs(self, model: mujoco.MjModel, data: mujoco.MjData) -> np.ndarray[Any, Any]:
        quat = data.sensor("imu_quat").data.astype(np.float32)
        rot = _quat_wxyz_to_rot(quat)
        linvel_world = data.sensor("frame_vel").data.astype(np.float32)
        linvel = rot.T @ linvel_world
        gyro = data.sensor("imu_gyro").data.astype(np.float32)
        imu_xmat = data.site_xmat[model.site("imu").id].reshape(3, 3)
        gravity = imu_xmat.T @ np.array([0, 0, -1], dtype=np.float32)
        joint_angles = data.qpos[7:] - self._default_angles
        joint_velocities = data.qvel[6:]
        command = self._input_controller.get_command().astype(np.float32, copy=True)
        gains = self._sport_gains()
        if gains is not None and gains.active:
            command[0] *= gains.command_gain
            if gains.cross_step:
                command[1] *= 1.15
        obs = np.hstack(
            [
                linvel,
                gyro,
                gravity,
                joint_angles,
                joint_velocities,
                self._last_action,
                command,
            ]
        )
        return obs.astype(np.float32)
