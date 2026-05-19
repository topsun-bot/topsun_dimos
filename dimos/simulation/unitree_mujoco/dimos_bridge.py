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

"""Go2 MuJoCo ↔ Unitree SDK2 DDS bridge (macOS-safe; no Linux timerfd / pygame)."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

import mujoco
import numpy as np
from unitree_sdk2py.core.channel import ChannelPublisher
from unitree_sdk2py.idl.default import unitree_go_msg_dds__LowState_
from unitree_sdk2py.idl.default import unitree_go_msg_dds__SportModeState_
from unitree_sdk2py.idl.unitree_go.msg.dds_ import LowState_
from unitree_sdk2py.idl.unitree_go.msg.dds_ import SportModeState_

if TYPE_CHECKING:
    from dimos.simulation.mujoco.input_controller import InputController
    from dimos.simulation.unitree_mujoco.go2_policy import UnitreeGo2OnnxController

TOPIC_LOWSTATE = "rt/lowstate"
TOPIC_HIGHSTATE = "rt/sportmodestate"
MOTOR_SENSOR_NUM = 3


class _PeriodicThread:
    """Replacement for unitree_sdk2py ``RecurrentThread`` (uses Linux timerfd)."""

    def __init__(self, interval: float, target: object, name: str) -> None:
        self._interval = interval
        self._target = target
        self._name = name
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def Start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
        self._thread.start()

    def Stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.is_set():
            t0 = time.perf_counter()
            self._target()  # type: ignore[operator]
            sleep_s = self._interval - (time.perf_counter() - t0)
            if sleep_s > 0:
                time.sleep(sleep_s)


class DimosUnitreeBridge:
    """Publish ``LowState`` / ``SportModeState``; drive joints from DimOS ONNX."""

    def __init__(
        self,
        mj_model: mujoco.MjModel,
        mj_data: mujoco.MjData,
        input_controller: InputController,
        policy_path: Path,
    ) -> None:
        self.mj_model = mj_model
        self.mj_data = mj_data
        self.num_motor = mj_model.nu
        self.dim_motor_sensor = MOTOR_SENSOR_NUM * self.num_motor
        self.have_frame_sensor_ = False

        for i in range(self.dim_motor_sensor, mj_model.nsensor):
            name = mujoco.mj_id2name(mj_model, mujoco.mjtObj.mjOBJ_SENSOR, i)
            if name == "frame_pos":
                self.have_frame_sensor_ = True

        dt = mj_model.opt.timestep
        self.low_state = unitree_go_msg_dds__LowState_()
        self.low_state_puber = ChannelPublisher(TOPIC_LOWSTATE, LowState_)
        self.low_state_puber.Init()
        self._low_state_thread = _PeriodicThread(dt, self.publish_low_state, "sim_lowstate")
        self._low_state_thread.Start()

        self.high_state = unitree_go_msg_dds__SportModeState_()
        self.high_state_puber = ChannelPublisher(TOPIC_HIGHSTATE, SportModeState_)
        self.high_state_puber.Init()
        self._high_state_thread = _PeriodicThread(dt, self.publish_high_state, "sim_highstate")
        self._high_state_thread.Start()

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

    def stop(self) -> None:
        self._low_state_thread.Stop()
        self._high_state_thread.Stop()

    def apply_locomotion(self) -> None:
        self._locomotion.get_control(self.mj_model, self.mj_data)

    def publish_low_state(self) -> None:
        for i in range(self.num_motor):
            self.low_state.motor_state[i].q = self.mj_data.sensordata[i]
            self.low_state.motor_state[i].dq = self.mj_data.sensordata[i + self.num_motor]
            self.low_state.motor_state[i].tau_est = self.mj_data.sensordata[i + 2 * self.num_motor]

        if self.have_frame_sensor_:
            base = self.dim_motor_sensor
            self.low_state.imu_state.quaternion[:] = self.mj_data.sensordata[base : base + 4]
            self.low_state.imu_state.gyroscope[:] = self.mj_data.sensordata[base + 4 : base + 7]
            self.low_state.imu_state.accelerometer[:] = self.mj_data.sensordata[
                base + 7 : base + 10
            ]

        self.low_state_puber.Write(self.low_state)

    def publish_high_state(self) -> None:
        if self.have_frame_sensor_:
            base = self.dim_motor_sensor + 10
            self.high_state.position[:] = self.mj_data.sensordata[base : base + 3]
            self.high_state.velocity[:] = self.mj_data.sensordata[base + 3 : base + 6]

        quat = self.mj_data.sensor("imu_quat").data
        w, x, y, z = quat
        siny_cosp = 2.0 * (w * z + x * y)
        cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
        self.high_state.imu_state.rpy[2] = float(np.arctan2(siny_cosp, cosy_cosp))
        self.high_state.imu_state.quaternion[:] = quat

        self.high_state_puber.Write(self.high_state)
