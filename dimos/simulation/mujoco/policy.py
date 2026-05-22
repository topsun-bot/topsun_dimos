#!/usr/bin/env python3

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


from abc import ABC, abstractmethod
from typing import Any

import mujoco
import numpy as np
import onnxruntime as ort  # type: ignore[import-untyped]

from dimos.simulation.mujoco.input_controller import InputController
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


class OnnxController(ABC):
    def __init__(
        self,
        policy_path: str,
        default_angles: np.ndarray[Any, Any],
        n_substeps: int,
        action_scale: float,
        input_controller: InputController,
        ctrl_dt: float | None = None,
        drift_compensation: list[float] | None = None,
    ) -> None:
        self._output_names = ["continuous_actions"]
        providers = ort.get_available_providers()
        try:
            self._policy = ort.InferenceSession(policy_path, providers=providers)
        except RuntimeError:
            logger.warning("GPU providers failed, falling back to CPUExecutionProvider")
            self._policy = ort.InferenceSession(policy_path, providers=["CPUExecutionProvider"])
        logger.info(f"Loaded policy: {policy_path} with providers: {self._policy.get_providers()}")

        self._action_scale = action_scale
        self._default_angles = default_angles
        self._last_action = np.zeros_like(default_angles, dtype=np.float32)

        self._counter = 0
        self._n_substeps = n_substeps
        self._input_controller = input_controller

        self._drift_compensation = np.array(drift_compensation or [0.0, 0.0, 0.0], dtype=np.float32)

    @abstractmethod
    def get_obs(self, model: mujoco.MjModel, data: mujoco.MjData) -> np.ndarray[Any, Any]:
        pass

    def get_control(self, model: mujoco.MjModel, data: mujoco.MjData) -> None:
        self._counter += 1
        if self._counter % self._n_substeps == 0:
            obs = self.get_obs(model, data)
            onnx_input = {"obs": obs.reshape(1, -1)}
            onnx_pred = self._policy.run(self._output_names, onnx_input)[0][0]
            self._last_action = onnx_pred.copy()
            data.ctrl[:] = onnx_pred * self._action_scale + self._default_angles
            self._post_control_update()

    def _post_control_update(self) -> None:  # noqa: B027
        pass


class Go1OnnxController(OnnxController):
    # Actuator order: FR(0-2), FL(3-5), RR(6-8), RL(9-11).
    _FR_LEG = (0, 1, 2)
    _FL_LEG = (3, 4, 5)
    _RR_LEG = (6, 7, 8)
    _RL_LEG = (9, 10, 11)
    _ALL_THIGH_ACTUATORS = (1, 4, 7, 10)
    _REAR_HIP_ACTUATORS = (6, 9)
    # Torso-up: negative thigh ctrl raises COM on Go1; rear-hip pitch back.
    _THIGH_LIFT_PER_M = -0.42
    _REAR_HIP_PITCH_BACK_PER_M = -0.24
    _FRONT_CALF_RAISE_PER_M = -1.15
    _REAR_CALF_LIFT_PER_M = -0.28
    _HIP_REACH_PER_M = 0.26
    _LIFT_THIGH_EXTRA_PER_M = -0.32
    # Support front leg + diagonal rear (anti roll when the other front leg is lifted).
    _SUPPORT_CALF_PRESS_PER_M = 1.12
    _SUPPORT_THIGH_LOAD_PER_M = 0.62
    _DIAGONAL_REAR_CALF_PRESS_PER_M = 0.42
    _DIAGONAL_REAR_THIGH_LOAD_PER_M = 0.28

    def get_obs(self, model: mujoco.MjModel, data: mujoco.MjData) -> np.ndarray[Any, Any]:
        linvel = data.sensor("local_linvel").data
        gyro = data.sensor("gyro").data
        imu_xmat = data.site_xmat[model.site("imu").id].reshape(3, 3)
        gravity = imu_xmat.T @ np.array([0, 0, -1])
        joint_angles = data.qpos[7:] - self._default_angles
        joint_velocities = data.qvel[6:]
        obs = np.hstack(
            [
                linvel,
                gyro,
                gravity,
                joint_angles,
                joint_velocities,
                self._last_action,
                self._input_controller.get_command(),
            ]
        )
        return obs.astype(np.float32)

    def get_control(self, model: mujoco.MjModel, data: mujoco.MjData) -> None:
        super().get_control(model, data)
        foot_raise = getattr(self._input_controller, "foot_raise_height", 0.0)
        front_reach = getattr(self._input_controller, "front_reach_m", 0.0)
        leg_mask = int(getattr(self._input_controller, "front_leg_mask", 0))
        if foot_raise <= 0.0 and front_reach <= 0.0:
            return

        for idx in self._ALL_THIGH_ACTUATORS:
            data.ctrl[idx] += self._THIGH_LIFT_PER_M * foot_raise
        for idx in self._REAR_HIP_ACTUATORS:
            data.ctrl[idx] += self._REAR_HIP_PITCH_BACK_PER_M * foot_raise
        for idx in (8, 11):
            data.ctrl[idx] += self._REAR_CALF_LIFT_PER_M * foot_raise

        lift_legs: list[tuple[int, ...]] = []
        support_legs: list[tuple[int, ...]] = []
        diagonal_rear: tuple[int, ...] | None = None
        if leg_mask == 1:
            lift_legs = [self._FR_LEG]
            support_legs = [self._FL_LEG]
            diagonal_rear = self._RL_LEG
        elif leg_mask == 2:
            lift_legs = [self._FL_LEG]
            support_legs = [self._FR_LEG]
            diagonal_rear = self._RR_LEG

        for hip, thigh, calf in lift_legs:
            data.ctrl[calf] += self._FRONT_CALF_RAISE_PER_M * foot_raise
            data.ctrl[thigh] += self._LIFT_THIGH_EXTRA_PER_M * foot_raise
            data.ctrl[hip] += self._HIP_REACH_PER_M * front_reach

        for _hip, thigh, calf in support_legs:
            data.ctrl[calf] += self._SUPPORT_CALF_PRESS_PER_M * foot_raise
            data.ctrl[thigh] += self._SUPPORT_THIGH_LOAD_PER_M * foot_raise

        if diagonal_rear is not None:
            _dhip, dthigh, dcalf = diagonal_rear
            data.ctrl[dcalf] += self._DIAGONAL_REAR_CALF_PRESS_PER_M * foot_raise
            data.ctrl[dthigh] += self._DIAGONAL_REAR_THIGH_LOAD_PER_M * foot_raise


class G1OnnxController(OnnxController):
    def __init__(
        self,
        policy_path: str,
        default_angles: np.ndarray[Any, Any],
        ctrl_dt: float,
        n_substeps: int,
        action_scale: float,
        input_controller: InputController,
        drift_compensation: list[float] | None = None,
    ) -> None:
        super().__init__(
            policy_path,
            default_angles,
            n_substeps,
            action_scale,
            input_controller,
            ctrl_dt,
            drift_compensation,
        )

        self._phase = np.array([0.0, np.pi])
        self._gait_freq = 1.5
        self._phase_dt = 2 * np.pi * self._gait_freq * ctrl_dt

    def get_obs(self, model: mujoco.MjModel, data: mujoco.MjData) -> np.ndarray[Any, Any]:
        linvel = data.sensor("local_linvel_pelvis").data
        gyro = data.sensor("gyro_pelvis").data
        imu_xmat = data.site_xmat[model.site("imu_in_pelvis").id].reshape(3, 3)
        gravity = imu_xmat.T @ np.array([0, 0, -1])
        joint_angles = data.qpos[7:] - self._default_angles
        joint_velocities = data.qvel[6:]
        phase = np.concatenate([np.cos(self._phase), np.sin(self._phase)])
        command = self._input_controller.get_command()
        command[0] = command[0] * 2
        command[1] = command[1] * 2
        command[0] += self._drift_compensation[0]
        command[1] += self._drift_compensation[1]
        command[2] += self._drift_compensation[2]
        obs = np.hstack(
            [
                linvel,
                gyro,
                gravity,
                command,
                joint_angles,
                joint_velocities,
                self._last_action,
                phase,
            ]
        )
        return obs.astype(np.float32)

    def _post_control_update(self) -> None:
        phase_tp1 = self._phase + self._phase_dt
        self._phase = np.fmod(phase_tp1 + np.pi, 2 * np.pi) - np.pi
