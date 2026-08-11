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

import copy
from enum import Enum
from importlib import resources
import sys
from threading import Event, Thread
import time
from typing import Any, Protocol

from pydantic import Field
from reactivex import empty
from reactivex.disposable import Disposable
from reactivex.observable import Observable

from dimos.agents.annotation import skill
from dimos.constants import DEFAULT_THREAD_JOIN_TIMEOUT
from dimos.core.core import rpc
from dimos.core.global_config import GlobalConfig
from dimos.core.module import Module, ModuleConfig
from dimos.core.resource import CompositeResource
from dimos.core.stream import In, Out
from dimos.memory2.replay import Replay, ReplayStream, resolve_db_path
from dimos.memory2.store.sqlite import SqliteStore
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Quaternion import Quaternion
from dimos.msgs.geometry_msgs.Transform import Transform
from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.msgs.sensor_msgs.CameraInfo import CameraInfo
from dimos.msgs.sensor_msgs.Image import Image
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2
from dimos.msgs.tf2_msgs.TFMessage import TFMessage
from dimos.navigation.diagnostics.sink import TraceSink, isolate_trace_failure
from dimos.robot.unitree.connection import UnitreeWebRTCConnection
from dimos.robot.unitree.type.lowstate import LowStateMsg
from dimos.spec.perception import Camera, Pointcloud
from dimos.utils.decorators.decorators import cached_property, simple_mcache
from dimos.utils.logging_config import setup_logger

if sys.version_info < (3, 13):
    from typing_extensions import TypeVar
else:
    from typing import TypeVar

logger = setup_logger()


class Go2Mode(str, Enum):
    DEFAULT = "default"
    RAGE = "rage"


class ConnectionConfig(ModuleConfig):
    # Remote 用账号 + SN 做云端寻址, 所以 ip 可以为空; LocalSTA 仍要求 robot_ip.
    ip: str | None = Field(default_factory=lambda m: m["g"].robot_ip)
    mode: Go2Mode = Go2Mode.DEFAULT
    lidar: bool = True
    camera: bool = True
    velocity_api: bool = False
    # "mcf" for stair traversal, "normal" for basic, None to leave it as is
    motion_mode: str | None = None
    # Per-device AES-128 key (Go2 fw >=1.1.15); defaults from GlobalConfig.
    aes_128_key: str | None = Field(default_factory=lambda m: m["g"].unitree_aes_128_key)
    # TF parent frame of the internal odometry (odom_frame_id -> base_link).
    # Rename (e.g. "go2_odom") when another odom source owns the tree root
    odom_frame_id: str = "world"


class Go2ConnectionProtocol(Protocol):
    """Protocol defining the interface for Go2 robot connections."""

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def lidar_stream(self) -> Observable[PointCloud2]: ...
    def odom_stream(self) -> Observable[PoseStamped]: ...
    def video_stream(self) -> Observable[Image]: ...
    def lowstate_stream(self) -> Observable[LowStateMsg]: ...
    def move(self, twist: Twist, duration: float = 0.0) -> bool: ...
    def stop_movement(self) -> None: ...
    def standup(self) -> bool: ...
    def liedown(self) -> bool: ...
    def balance_stand(self) -> bool: ...
    def sport_command(self, api_id: int) -> bool: ...
    def set_obstacle_avoidance(self, enabled: bool = True) -> bool: ...
    def set_rage_mode(self, enable: bool) -> bool: ...
    def free_avoid(self, enabled: bool = True) -> bool: ...
    def set_light(self, level: int) -> bool: ...
    def switch_joystick(self, enable: bool = True) -> bool: ...
    def publish_request(self, topic: str, data: dict) -> dict: ...  # type: ignore[type-arg]


_FRONT_CAMERA_720_YAML = resources.files("dimos.robot.unitree.go2").joinpath(
    "front_camera_720.yaml"
)


def _camera_info_static() -> CameraInfo:
    with resources.as_file(_FRONT_CAMERA_720_YAML) as yaml_path:
        return CameraInfo.from_yaml(str(yaml_path))


def _prefixed(prefix: str | None, name: str) -> str:
    """Apply a TF namespace prefix (ModuleConfig.frame_id_prefix) to a frame name."""
    if not prefix or not name:
        return name
    return f"{prefix}/{name}"


# Static camera mount chain: base_link -> camera_link -> camera_optical.
# TODO we need a standardized way to specify this for all cameras in dimos
BASE_TO_OPTICAL: Transform = Transform(
    translation=Vector3(0.3, 0.0, 0.0),
    rotation=Quaternion(0.0, 0.0, 0.0, 1.0),
    frame_id="base_link",
    child_frame_id="camera_link",
) + Transform(
    translation=Vector3(0.0, 0.0, 0.0),
    rotation=Quaternion(-0.5, 0.5, -0.5, 0.5),
    frame_id="camera_link",
    child_frame_id="camera_optical",
)


def make_connection(
    ip: str | None,
    cfg: GlobalConfig,
    aes_128_key: str | None = None,
    trace_sink: TraceSink | None = None,
    velocity_api: bool = False,
) -> Go2ConnectionProtocol:
    # 第一级先按 replay / simulator / 真机选择后端. 真机 WebRTC 再按
    # unitree_webrtc_method 分成 Remote 和 LocalSTA, 上层 GO2Connection
    # 因而不需要知道数据来自局域网还是 4G.
    connection_type = cfg.unitree_connection_type.lower()

    if ip in ("fake", "mock", "replay") or connection_type == "replay":
        dataset = cfg.replay_db
        return ReplayConnection(dataset=dataset)
    elif ip == "mujoco" or connection_type in ("mujoco", "true"):
        from dimos.robot.unitree.mujoco_connection import MujocoConnection

        return MujocoConnection(cfg)
    elif connection_type == "dimsim":
        from dimos.robot.unitree.dimsim_connection import DimSimConnection

        return DimSimConnection(cfg)
    elif connection_type == "webrtc":
        method = (cfg.unitree_webrtc_method or "local").strip().lower()
        has_trace_config = hasattr(cfg, "navigation_trace_roi_interval_sec")
        trace_roi_interval_sec = float(getattr(cfg, "navigation_trace_roi_interval_sec", 5.0))
        if method in ("remote", "4g", "sta-t"):
            # 4G / STA-T 走 Unitree 云信令. 这里不传 robot_ip, 也不会用
            # LocalSTA 的 AES key 做握手; region 决定中国区或海外区 API.
            if not has_trace_config and trace_sink is None:
                return UnitreeWebRTCConnection(
                    ip=None,
                    aes_128_key=aes_128_key,
                    velocity_api=velocity_api,
                    connection_method="remote",
                    username=cfg.unitree_username,
                    password=cfg.unitree_password,
                    serial_number=cfg.unitree_serial,
                    region=cfg.unitree_region or "cn",
                )
            return UnitreeWebRTCConnection(
                ip=None,
                aes_128_key=aes_128_key,
                velocity_api=velocity_api,
                connection_method="remote",
                username=cfg.unitree_username,
                password=cfg.unitree_password,
                serial_number=cfg.unitree_serial,
                region=cfg.unitree_region or "cn",
                trace_sink=trace_sink,
                trace_roi_interval_sec=trace_roi_interval_sec,
            )
        # 没有显式选择 Remote 就保持历史 LocalSTA 行为, 防止旧部署被改道.
        assert ip is not None, "IP address must be provided for LocalSTA WebRTC"
        if not has_trace_config and trace_sink is None:
            return UnitreeWebRTCConnection(ip, aes_128_key=aes_128_key, velocity_api=velocity_api)
        return UnitreeWebRTCConnection(
            ip,
            aes_128_key=aes_128_key,
            velocity_api=velocity_api,
            trace_sink=trace_sink,
            trace_roi_interval_sec=trace_roi_interval_sec,
        )
    else:
        raise ValueError(f"Unknown simulator {cfg.simulation!r}. Choose from: mujoco, dimsim")


class ReplayConnection(UnitreeWebRTCConnection, CompositeResource):
    def __init__(  # type: ignore[no-untyped-def]
        self,
        dataset: str = "go2_china_office",
        **kwargs,
    ) -> None:
        self.dataset = dataset
        self._loop = kwargs.get("loop", False)
        self._seek = kwargs.get("seek")
        self._duration = kwargs.get("duration")

    @cached_property
    def replay(self) -> Replay:
        # One shared store + Replay so lidar/odom/video advance against the
        # same wall-clock anchor on subscribe.
        store = self.register_disposable(
            SqliteStore(path=str(resolve_db_path(self.dataset)), must_exist=True)
        )
        store.start()
        return store.replay(loop=self._loop, seek=self._seek, duration=self._duration)

    def connect(self) -> None:
        pass

    def start(self) -> None:
        pass

    def stop(self) -> None:
        # GO2Connection must dispose its stream subscriptions before closing
        # the SQLite store.  UnitreeWebRTCConnection.stop() is not applicable:
        # replay intentionally has no WebRTC loop/data-channel members.
        pass

    def close_store(self) -> None:
        """Dispose the replay store after all scheduled streams are unsubscribed."""
        CompositeResource.stop(self)

    def standup(self) -> bool:
        return True

    def liedown(self) -> bool:
        return True

    def balance_stand(self) -> bool:
        return True

    def sport_command(self, api_id: int) -> bool:
        return True

    def stop_movement(self) -> None:
        # No webrtc deadman timer to cancel; the cmd_vel timeout covers replay.
        pass

    def set_obstacle_avoidance(self, enabled: bool = True) -> bool:
        return True

    def set_motion_mode(self, name: str) -> None:
        pass

    def set_rage_mode(self, enable: bool) -> bool:
        return True

    def free_avoid(self, enabled: bool = True) -> bool:
        return True

    def set_light(self, level: int) -> bool:
        return True

    def switch_joystick(self, enable: bool = True) -> bool:
        return True

    def _stream_name(self, *names: str) -> str:
        """Return the first of ``names`` present in the dataset (stream naming
        changed over time: mid360 recordings use go2_lidar/go2_odom, older ones
        lidar/odom)."""
        available = self.replay.list_streams()
        for name in names:
            if name in available:
                return name
        raise KeyError(f"None of {names!r} in dataset {self.dataset!r}; available: {available}")

    @simple_mcache
    def lidar_stream(self) -> Observable[PointCloud2]:
        stream: ReplayStream[PointCloud2] = self.replay.stream(
            self._stream_name("go2_lidar", "lidar")
        )
        return stream.observable()

    @simple_mcache
    def odom_stream(self) -> Observable[PoseStamped]:
        stream: ReplayStream[PoseStamped] = self.replay.stream(
            self._stream_name("go2_odom", "odom")
        )
        return stream.observable()

    @simple_mcache
    def video_stream(self) -> Observable[Image]:
        return self.replay.streams.color_image.observable()

    @simple_mcache
    def lowstate_stream(self) -> Observable:  # type: ignore[type-arg]
        # Replay datasets carry no low-level state (battery/IMU) — emit nothing.
        return empty()

    def move(self, twist: Twist, duration: float = 0.0) -> bool:
        return True

    def publish_request(self, topic: str, data: dict):  # type: ignore[no-untyped-def, type-arg]
        """Fake publish request for testing."""
        return {"status": "ok", "message": "Fake publish"}


_Config = TypeVar("_Config", bound=ConnectionConfig, default=ConnectionConfig)


class GO2Connection(Module, Camera, Pointcloud):
    dedicated_worker = True

    config: ConnectionConfig
    cmd_vel: In[Twist]
    pointcloud: Out[PointCloud2]
    odom: Out[PoseStamped]
    lidar: Out[PointCloud2]
    color_image: Out[Image]
    camera_info: Out[CameraInfo]
    tf: Out[TFMessage]

    connection: Go2ConnectionProtocol
    camera_info_static: CameraInfo = _camera_info_static()
    _camera_info_thread: Thread | None = None
    _camera_info_stop: Event
    _latest_video_frame: Image | None = None
    _latest_lowstate: LowStateMsg | None = None
    _latest_lowstate_received_at: float | None = None
    _navigation_trace: TraceSink
    _trace_last_lowstate_ns: int
    _go2_stop_started: bool

    @classmethod
    def rerun_views(cls):  # type: ignore[no-untyped-def]
        import rerun.blueprint as rrb

        """Return Rerun view blueprints for GO2 camera visualization."""
        return [
            rrb.Spatial2DView(
                name="Camera",
                origin="world/robot/camera/rgb",
            ),
        ]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._camera_info_stop = Event()
        self._go2_stop_started = False
        self._navigation_trace = TraceSink("connection", config=self.config.g)
        self._trace_last_lowstate_ns = 0
        # 构造底层 Resource 时已经同步完成 WebRTC 建连和 DataChannel 验证.
        try:
            self.connection = make_connection(
                self.config.ip,
                self.config.g,
                aes_128_key=self.config.aes_128_key,
                trace_sink=self._navigation_trace,
                velocity_api=self.config.velocity_api,
            )
        except Exception:
            self._navigation_trace.close()
            raise

        if hasattr(self.connection, "camera_info_static"):
            self.camera_info_static = self.connection.camera_info_static

        if self.config.frame_id_prefix and self.camera_info_static.frame_id:
            # Copy so the class-level default is not mutated.
            self.camera_info_static = copy.copy(self.camera_info_static)
            self.camera_info_static.frame_id = _prefixed(
                self.config.frame_id_prefix, self.camera_info_static.frame_id
            )

    @rpc
    def start(self) -> None:
        super().start()
        if not hasattr(self, "connection"):
            return
        self._camera_info_stop.clear()
        self.connection.start()

        def onimage(image: Image) -> None:
            image.frame_id = _prefixed(self.config.frame_id_prefix, image.frame_id)
            self.color_image.publish(image)
            self._latest_video_frame = image

        # 下面的 Observable 接口对 Remote 和 LocalSTA 完全一致. 传输方式的
        # 差异已经封装在 UnitreeWebRTCConnection 内, 蓝图和导航模块无需改动.
        if self.config.lidar:
            self.register_disposable(self.connection.lidar_stream().subscribe(self.lidar.publish))
        self.register_disposable(self.connection.odom_stream().subscribe(self._publish_tf))
        self.register_disposable(self.connection.lowstate_stream().subscribe(self._on_lowstate))
        self.register_disposable(Disposable(self.cmd_vel.subscribe(self.move)))

        if self.config.camera:
            self.register_disposable(self.connection.video_stream().subscribe(onimage))
            self._camera_info_thread = Thread(
                target=self.publish_camera_info,
                daemon=True,
            )
            self._camera_info_thread.start()

        if self.config.motion_mode and isinstance(self.connection, UnitreeWebRTCConnection):
            self.connection.set_motion_mode(self.config.motion_mode)

        # 重要安全行为: unitree-go2 blueprint 启动后会自动站立并进入
        # BalanceStand, 不是只建立只读数据连接. 真机启动前必须清空周围空间.
        standup_result = self.standup()
        time.sleep(3)
        balance_stand_result = self.connection.balance_stand()

        rage_mode_result: bool | None = None
        if self.config.mode == Go2Mode.RAGE:
            rage_mode_result = self.connection.set_rage_mode(True)

        self.connection.set_obstacle_avoidance(self.config.g.obstacle_avoidance)
        self._trace_avoidance_configuration(
            "obstacles_avoid",
            self.config.g.obstacle_avoidance,
            request_completed=True,
            acknowledged_value=None,
        )
        # Toggle Unitree's hidden FreeAvoid sport-mode AI obstacle-avoidance
        # (SportClient::FreeAvoid, sport api_id=2048). See `free_avoid` on the
        # underlying connection for details.
        free_avoid_ack: bool | None = None
        free_avoid_error: Exception | None = None
        try:
            free_avoid_ack = self.connection.free_avoid(self.config.g.free_avoid)
            logger.info(
                "FreeAvoid (sport api_id=2048) "
                f"{'enabled' if self.config.g.free_avoid else 'disabled'}, "
                f"ack={free_avoid_ack}"
            )
            self._trace_avoidance_configuration(
                "free_avoid",
                self.config.g.free_avoid,
                request_completed=True,
                acknowledged_value=free_avoid_ack,
            )
        except Exception as e:
            free_avoid_error = e
            logger.warning(f"FreeAvoid toggle failed (non-fatal): {e}")
            self._trace_avoidance_configuration(
                "free_avoid",
                self.config.g.free_avoid,
                request_completed=False,
                acknowledged_value=None,
                error=e,
            )
        self._trace_robot_startup_state(
            standup_result=standup_result,
            balance_stand_result=balance_stand_result,
            rage_mode_result=rage_mode_result,
            free_avoid_ack=free_avoid_ack,
            free_avoid_error=free_avoid_error,
        )

    @rpc
    def stop(self) -> None:
        # ModuleCoordinator 会先 RPC stop，worker 退出时还可能再次调用 stop。
        # 第二次不能再向已经停止的 asyncio loop 提交 StandDown RPC。
        if getattr(self, "_go2_stop_started", False):
            return
        self._go2_stop_started = True

        # 每一步都独立 best-effort：云端可能先断开 DataChannel，此时
        # StandDown 会失败，但订阅、WebRTC、worker 和 trace 仍必须全部清理。
        try:
            self.liedown()
        except Exception as exc:
            logger.warning("Failed to send StandDown while stopping Go2: %s", exc)

        self._camera_info_stop.set()

        try:
            # 先释放视频/点云订阅，让 finally_action 能在 WebRTC loop
            # 仍存活时关闭媒体通道。
            super().stop()
        except Exception as exc:
            logger.warning("Failed to dispose Go2 module subscriptions: %s", exc)

        try:
            if self.connection:
                self.connection.stop()
        except Exception as exc:
            logger.warning("Failed to stop Go2 connection: %s", exc)

        if self._camera_info_thread and self._camera_info_thread.is_alive():
            self._camera_info_thread.join(timeout=DEFAULT_THREAD_JOIN_TIMEOUT)
            if self._camera_info_thread.is_alive():
                logger.warning("Go2 camera-info thread did not stop in time")

        if isinstance(self.connection, ReplayConnection):
            try:
                self.connection.close_store()
            except Exception as exc:
                logger.warning("Failed to close Go2 replay store: %s", exc)
        self._navigation_trace.close()

    @classmethod
    def _odom_to_tf(cls, odom: PoseStamped, prefix: str = "") -> list[Transform]:
        # The odom parent frame (odom.frame_id) stays unprefixed so namespaced
        # robots still hang off one shared tree root.
        camera_link = Transform(
            translation=Vector3(0.3, 0.0, 0.0),
            rotation=Quaternion(0.0, 0.0, 0.0, 1.0),
            frame_id=_prefixed(prefix, "base_link"),
            child_frame_id=_prefixed(prefix, "camera_link"),
            ts=odom.ts,
        )

        camera_optical = Transform(
            translation=Vector3(0.0, 0.0, 0.0),
            rotation=Quaternion(-0.5, 0.5, -0.5, 0.5),
            frame_id=_prefixed(prefix, "camera_link"),
            child_frame_id=_prefixed(prefix, "camera_optical"),
            ts=odom.ts,
        )

        return [
            Transform.from_pose(_prefixed(prefix, "base_link"), odom),
            camera_link,
            camera_optical,
        ]

    def _publish_tf(self, msg: PoseStamped) -> None:
        msg.frame_id = self.config.odom_frame_id
        transforms = self._odom_to_tf(msg, prefix=self.config.frame_id_prefix or "")
        self.tf.publish(TFMessage(*transforms))
        if self.odom.transport:
            self.odom.publish(msg)
        self._trace_published_odom(msg)

    def publish_camera_info(self) -> None:
        while not self._camera_info_stop.wait(timeout=1.0):
            self.camera_info.publish(self.camera_info_static)

    @rpc
    def move(self, twist: Twist, duration: float = 0.0) -> bool:
        """Send movement command to robot."""
        try:
            result = self.connection.move(twist, duration)
        except Exception as exc:
            self._trace_move_result(twist, duration, result=None, error=exc)
            raise
        self._trace_move_result(twist, duration, result=result)
        return result

    @rpc
    def standup(self) -> bool:
        """Make the robot stand up."""
        return self.connection.standup()

    @rpc
    def liedown(self) -> bool:
        """Make the robot lie down."""
        return self.connection.liedown()

    @rpc
    def balance_stand(self) -> bool:
        """Enter BalanceStand: neutral state for switching locomotion modes"""
        return self.connection.balance_stand()

    @rpc
    def set_rage_mode(self, enable: bool) -> bool:
        """Toggle Rage Mode on/off (~2.5 m/s envelope when on).
        On the WebRTC backend this re-establishes the BalanceStand
        precondition before toggling; sim backends are no-ops.
        """
        result = self.connection.set_rage_mode(enable)
        logger.info("Rage Mode", enabled=enable)
        return result

    @rpc
    def free_avoid(self, enabled: bool = True) -> bool:
        """Toggle SportClient's hidden AI obstacle-avoidance (FreeAvoid, sport
        api_id=2048) at runtime. Ensures BalanceStand precondition regardless
        of the current FSM state, then forwards to the underlying connection.
        """
        self.connection.balance_stand()
        time.sleep(0.3)
        result = self.connection.free_avoid(enabled)
        logger.info(f"FreeAvoid {'enabled' if enabled else 'disabled'}, ack={result}")
        self._trace_avoidance_configuration(
            "free_avoid",
            enabled,
            request_completed=True,
            acknowledged_value=result,
        )
        return result

    @rpc
    def sport_command(self, api_id: int) -> bool:
        """Send a parameterless SPORT_MOD command by api_id (Hello, Damp, ...)."""
        return self.connection.sport_command(api_id)

    @rpc
    def set_light(self, level: int) -> bool:
        """Head-LED brightness level 0-10 (0 = off)."""
        return self.connection.set_light(level)

    @rpc
    def set_obstacle_avoidance(self, enabled: bool = True) -> bool:
        """Toggle the onboard obstacle avoidance."""
        return self.connection.set_obstacle_avoidance(enabled)

    @rpc
    def switch_joystick(self, enable: bool = True) -> bool:
        """Firmware joystick listening on/off (WASD stick emulation needs it on)."""
        return self.connection.switch_joystick(enable)

    @rpc
    def stop_movement(self) -> None:
        """Zero the base immediately (webrtc deadman stop)."""
        self.connection.stop_movement()

    def _on_lowstate(self, msg: LowStateMsg) -> None:
        """Cache the latest low-level state push (battery, IMU, motors, etc.)."""
        self._latest_lowstate = msg
        self._latest_lowstate_received_at = time.monotonic()
        self._trace_lowstate(msg)

    def _trace_published_odom(self, msg: PoseStamped) -> None:
        if not self._navigation_trace.accepts("full"):
            return
        try:
            self._navigation_trace.record(
                "connection_odom_published",
                {
                    "host_assigned_ts": float(msg.ts),
                    "frame_id": msg.frame_id,
                    "pose": _pose_trace_fields(msg),
                    "ground_truth": False,
                    "estimate_kind": "unitree_lidar_odometry",
                    "tf_publish_completed": True,
                    "odom_publish_attempted": bool(self.odom.transport),
                },
                estimated_bytes=1024,
            )
        except Exception as exc:
            isolate_trace_failure(self._navigation_trace, exc)

    def _trace_move_result(
        self,
        twist: Twist,
        duration: float,
        *,
        result: bool | None,
        error: Exception | None = None,
    ) -> None:
        if not self._navigation_trace.accepts("full"):
            return
        try:
            fields: dict[str, object] = {
                "twist": _twist_trace_fields(twist),
                "duration_sec": duration,
                "connection_returned": result,
                "send_path_completed": result is not None,
                "robot_execution_ack": False,
                "robot_execution_observed": False,
            }
            if error is not None:
                fields["error_type"] = type(error).__name__
                fields["error_message"] = str(error)
            self._navigation_trace.record(
                "connection_move_completed",
                fields,
                estimated_bytes=896,
            )
        except Exception as exc:
            isolate_trace_failure(self._navigation_trace, exc)

    def _trace_avoidance_configuration(
        self,
        switch_name: str,
        requested_enabled: bool,
        *,
        request_completed: bool,
        acknowledged_value: bool | None,
        error: Exception | None = None,
    ) -> None:
        if not self._navigation_trace.accepts("summary"):
            return
        try:
            fields: dict[str, object] = {
                "switch_name": switch_name,
                "requested_enabled": requested_enabled,
                "request_completed": request_completed,
                "ack_observed": acknowledged_value is not None,
                "acknowledged_value": acknowledged_value,
            }
            if error is not None:
                fields["error_type"] = type(error).__name__
                fields["error_message"] = str(error)
            self._navigation_trace.record(
                "go2_avoidance_configuration",
                fields,
                estimated_bytes=768,
            )
        except Exception as exc:
            isolate_trace_failure(self._navigation_trace, exc)

    def _trace_robot_startup_state(
        self,
        *,
        standup_result: bool,
        balance_stand_result: bool,
        rage_mode_result: bool | None,
        free_avoid_ack: bool | None,
        free_avoid_error: Exception | None,
    ) -> None:
        if not self._navigation_trace.accepts("summary"):
            return
        try:
            network_status: dict[str, object] = {
                "connection_type": self.config.g.unitree_connection_type,
                "connection_method_requested": self.config.g.unitree_webrtc_method,
            }
            if isinstance(self.connection, UnitreeWebRTCConnection) and hasattr(
                self.connection, "_connection_method"
            ):
                network_status["connection_method_effective"] = getattr(
                    self.connection,
                    "_connection_method",
                    None,
                )
                if hasattr(self.connection, "_datachannel_state"):
                    network_status["datachannel"] = self.connection._datachannel_state()
            self._navigation_trace.record(
                "robot_startup_state",
                {
                    "standup": {
                        "request_completed": True,
                        "acknowledged_value": standup_result,
                    },
                    "balance_stand": {
                        "request_completed": True,
                        "acknowledged_value": balance_stand_result,
                    },
                    "motion_mode": {
                        "requested": self.config.motion_mode,
                        "ack_observed": False,
                    },
                    "rage_mode": {
                        "requested": self.config.mode == Go2Mode.RAGE,
                        "ack_observed": rage_mode_result is not None,
                        "acknowledged_value": rage_mode_result,
                    },
                    "obstacle_avoidance": {
                        "requested_enabled": self.config.g.obstacle_avoidance,
                        "request_completed": True,
                        "ack_observed": False,
                        "detail_event": "unitree_avoidance_switch_response",
                    },
                    "free_avoid": {
                        "requested_enabled": self.config.g.free_avoid,
                        "request_completed": free_avoid_error is None,
                        "ack_observed": free_avoid_ack is not None,
                        "acknowledged_value": free_avoid_ack,
                        "error_type": (
                            type(free_avoid_error).__name__
                            if free_avoid_error is not None
                            else None
                        ),
                    },
                    "network_status": network_status,
                },
                estimated_bytes=1536,
            )
        except Exception as exc:
            isolate_trace_failure(self._navigation_trace, exc)

    def _trace_lowstate(self, msg: LowStateMsg) -> None:
        if not self._navigation_trace.accepts("full"):
            return
        try:
            sample_hz = self.config.g.navigation_trace_lowstate_hz
            if sample_hz <= 0:
                return
            now_ns = time.monotonic_ns()
            interval_ns = max(1, int(1_000_000_000 / sample_hz))
            if now_ns - self._trace_last_lowstate_ns < interval_ns:
                return
            self._trace_last_lowstate_ns = now_ns
            self._navigation_trace.record(
                "go2_lowstate_summary",
                {
                    "host_rx_monotonic_ns": now_ns,
                    "summary": _lowstate_trace_fields(msg),
                },
                estimated_bytes=896,
            )
        except Exception as exc:
            isolate_trace_failure(self._navigation_trace, exc)

    @skill
    def get_battery_soc(self) -> int | None:
        """Returns the robot's battery state-of-charge as a percentage (0-100).

        Use this skill to answer battery / power / charge questions. Returns
        None if no low-level state has been received yet.
        """
        return self.battery_soc()

    @rpc
    def battery_soc(self) -> int | None:
        """Battery SOC 0-100 (or None until lowstate arrives). Plain RPC — no
        skill-log spam, for the hosted telemetry poll."""
        try:
            return int(self._latest_lowstate["data"]["bms_state"]["soc"])  # type: ignore[index]
        except (KeyError, TypeError, ValueError):
            return None

    @rpc
    def lowstate_age_s(self) -> float:
        """距最新 lowstate 的秒数; 回充控制器 image/lowstate 双新鲜度门限用."""
        if self._latest_lowstate_received_at is None:
            return float("inf")
        return max(0.0, time.monotonic() - self._latest_lowstate_received_at)

    @rpc
    def bms_current(self) -> float | None:
        """rt/lf/lowstate 里 bms_state.current 原值 (mA).

        回充充电判定见 recharge/charge_verify.py 双电流带标定.
        """
        try:
            return float(self._latest_lowstate["data"]["bms_state"]["current"])  # type: ignore[index]
        except (KeyError, TypeError, ValueError):
            return None

    @rpc
    def publish_request(self, topic: str, data: dict[str, Any]) -> dict[Any, Any]:
        """把通用 Unitree topic/API 请求转发到 WebRTC DataChannel.

        Remote 和 LocalSTA 在这里使用相同的请求格式; 区别只在更底层的
        ICE 选路和 SDP 信令方式.

        Args:
            topic: The RTC topic to publish to
            data: The data dictionary to publish
        Returns:
            The result of the publish request
        """
        return self.connection.publish_request(topic, data)

    @skill
    def observe(self) -> Image | None:
        """Returns the latest video frame from the robot camera. Use this skill for any visual world queries.

        This skill provides the current camera view for perception tasks.
        Returns None if no frame has been captured yet.
        """
        return self._latest_video_frame


def _twist_trace_fields(twist: Twist) -> dict[str, float]:
    return {
        "linear_x": float(twist.linear.x),
        "linear_y": float(twist.linear.y),
        "linear_z": float(twist.linear.z),
        "angular_x": float(twist.angular.x),
        "angular_y": float(twist.angular.y),
        "angular_z": float(twist.angular.z),
    }


def _pose_trace_fields(pose: PoseStamped) -> dict[str, object]:
    return {
        "position": {
            "x": float(pose.position.x),
            "y": float(pose.position.y),
            "z": float(pose.position.z),
        },
        "orientation_xyzw": [
            float(pose.orientation.x),
            float(pose.orientation.y),
            float(pose.orientation.z),
            float(pose.orientation.w),
        ],
    }


def _lowstate_trace_fields(msg: LowStateMsg) -> dict[str, object]:
    data = msg.get("data", {})
    bms = data.get("bms_state", {})
    imu = data.get("imu_state", {})
    return {
        "topic": msg.get("topic"),
        "battery_soc": bms.get("soc"),
        "battery_current": bms.get("current"),
        "power_v": data.get("power_v"),
        "temperature_ntc1": data.get("temperature_ntc1"),
        "imu_rpy": imu.get("rpy"),
        "foot_force": data.get("foot_force"),
    }
