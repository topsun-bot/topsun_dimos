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
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._lift_phase = 0.0
        self._lift_phase_dt = 2 * np.pi * 2.5 * (kwargs.get("ctrl_dt") or 0.02)

    def _sport_gains(self) -> Any:
        getter = getattr(self._input_controller, "get_sport_gains", None)
        if getter is None:
            return None
        return getter()

    def get_obs(self, model: mujoco.MjModel, data: mujoco.MjData) -> np.ndarray[Any, Any]:
        linvel = data.sensor("local_linvel").data
        gyro = data.sensor("gyro").data
        imu_xmat = data.site_xmat[model.site("imu").id].reshape(3, 3)
        gravity = imu_xmat.T @ np.array([0, 0, -1])
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

    def _post_control_update(self) -> None:
        gains = self._sport_gains()
        if gains is not None and gains.active and gains.foot_raise_m > 0.05:
            self._lift_phase = float(
                np.fmod(self._lift_phase + self._lift_phase_dt + np.pi, 2 * np.pi) - np.pi
            )
        else:
            self._lift_phase = 0.0

    def get_control(self, model: mujoco.MjModel, data: mujoco.MjData) -> None:
        super().get_control(model, data)
        gains = self._sport_gains()
        if gains is None or not gains.active or gains.foot_raise_m <= 0.05:
            return
        lift = gains.foot_raise_m * 4.0 * np.sin(self._lift_phase)
        n_joints = len(self._default_angles)
        if n_joints >= 12:
            # Go1 leg order: FR(0-2), FL(3-5), RR(6-8), RL(9-11) — bias front thigh/calf.
            for idx in (1, 2, 4, 5):
                data.ctrl[idx] += lift


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
