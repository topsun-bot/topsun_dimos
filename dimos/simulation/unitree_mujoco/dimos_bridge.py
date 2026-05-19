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

"""Unitree SDK2 MuJoCo bridge with DimOS ONNX locomotion (no gamepad / external LowCmd)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

import mujoco
import numpy as np

from dimos.simulation.unitree_mujoco.paths import unitree_simulate_python

if TYPE_CHECKING:
    from dimos.simulation.mujoco.input_controller import InputController
    from dimos.simulation.unitree_mujoco.go2_policy import UnitreeGo2OnnxController

_SIM_PY = unitree_simulate_python()
if str(_SIM_PY) not in sys.path:
    sys.path.insert(0, str(_SIM_PY))

from unitree_sdk2py_bridge import UnitreeSdk2Bridge  # noqa: E402


class DimosUnitreeBridge(UnitreeSdk2Bridge):
    """Publish ``LowState`` / ``SportModeState``; drive joints from DimOS ONNX, not ``rt/lowcmd``."""

    def __init__(
        self,
        mj_model: mujoco.MjModel,
        mj_data: mujoco.MjData,
        input_controller: InputController,
        policy_path: Path,
    ) -> None:
        super().__init__(mj_model, mj_data)

        # External LowCmd would fight the ONNX policy; DimOS uses shared-memory velocity instead.
        try:
            self.low_cmd_suber.Close()
        except Exception:
            pass

        try:
            self.WirelessControllerThread.Stop()
        except Exception:
            pass

        sim_dt = mj_model.opt.timestep
        ctrl_dt = 0.02
        n_substeps = max(1, round(ctrl_dt / sim_dt))
        default_angles = np.array(
            [0, 0.9, -1.8, 0, 0.9, -1.8, 0, 0.9, -1.8, 0, 0.9, -1.8],
            dtype=np.float32,
        )

        from dimos.simulation.unitree_mujoco.go2_policy import UnitreeGo2OnnxController

        self._locomotion: UnitreeGo2OnnxController = UnitreeGo2OnnxController(
            policy_path=str(policy_path),
            default_angles=default_angles,
            n_substeps=n_substeps,
            action_scale=0.5,
            input_controller=input_controller,
            ctrl_dt=ctrl_dt,
        )

    def apply_locomotion(self) -> None:
        if self.mj_data is not None:
            self._locomotion.get_control(self.mj_model, self.mj_data)

    def fill_sport_imu_yaw(self) -> None:
        """SportModeState in the official bridge omits IMU yaw; fill from MuJoCo orientation."""
        if self.mj_data is None:
            return
        quat = self.mj_data.sensor("imu_quat").data
        w, x, y, z = quat
        # yaw (Z) from quaternion (w, x, y, z)
        siny_cosp = 2.0 * (w * z + x * y)
        cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
        yaw = float(np.arctan2(siny_cosp, cosy_cosp))
        self.high_state.imu_state.rpy[2] = yaw
        self.high_state.imu_state.quaternion[:] = quat

    def PublishHighState(self) -> None:
        super().PublishHighState()
        self.fill_sport_imu_yaw()
