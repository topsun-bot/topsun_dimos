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

import asyncio
from dataclasses import dataclass
import functools
import json
import threading
import time
from typing import Any, TypeAlias, TypeVar

import numpy as np
from numpy.typing import NDArray
from reactivex import operators as ops
from reactivex.observable import Observable
from reactivex.subject import Subject
from unitree_webrtc_connect.constants import (
    RTC_TOPIC,
    SPORT_CMD,
    VUI_COLOR,
)
from unitree_webrtc_connect.webrtc_driver import (
    UnitreeWebRTCConnection as LegionConnection,
    WebRTCConnectionMethod,
)

from dimos.constants import DEFAULT_THREAD_JOIN_TIMEOUT
from dimos.core.resource import Resource
from dimos.msgs.geometry_msgs.Pose import Pose
from dimos.msgs.geometry_msgs.Transform import Transform
from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.msgs.sensor_msgs.Image import Image, ImageFormat
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2
from dimos.robot.unitree.type.lidar import (
    RawLidarMsg,
    pointcloud2_from_webrtc_lidar,
)
from dimos.robot.unitree.type.lowstate import LowStateMsg
from dimos.robot.unitree.type.odometry import Odometry
from dimos.types.timestamped import Timestamped
from dimos.utils.decorators.decorators import simple_mcache
from dimos.utils.logging_config import setup_logger
from dimos.utils.reactive import backpressure, callback_to_observable

VideoMessage: TypeAlias = NDArray[np.uint8]  # Shape: (height, width, 3)

logger = setup_logger()

# 完整 Remote Offer 同时包含 audio, video 和 DataChannel 三条 SDP m-line.
# 实机发现 aiortc 为它们分别创建 ICE 凭据时, Unitree TURN 路径可能出现
# ICE completed 但 DTLS 仍失败. 因此 Remote 建连前在进程内安装一次补丁.
_SHARED_ICE_PATCHED = False


def _ensure_shared_ice_credentials() -> None:
    """让同一个进程内新建的 aioice.Connection 复用一组 ICE 凭据.

    这里修改的是 aioice.Connection.__init__, 所以必须在 aiortc 创建 Offer
    之前执行. 补丁只由 Remote 分支触发; LocalSTA 不调用本函数, 局域网
    连接行为保持不变.

    注意: 这是进程级 monkey patch. 当前 DimOS 的 Go2 连接通常是一个独立
    worker 中的一台机器人, 因而影响范围可控; 若同一进程创建多个独立
    PeerConnection, 它们也会使用这组 ufrag / pwd.
    """
    global _SHARED_ICE_PATCHED
    if _SHARED_ICE_PATCHED:
        return
    import aioice
    import aioice.utils

    # 每个 worker 启动时随机生成一次, 不是写死的固定凭据.
    shared_ufrag = aioice.utils.random_string(4)
    shared_pwd = aioice.utils.random_string(22)
    orig_init = aioice.Connection.__init__

    def _patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        # 覆盖 aiortc 为各条 m-line 分别传入的值, 使 bundled m-lines 共用凭据.
        kwargs["local_username"] = shared_ufrag
        kwargs["local_password"] = shared_pwd
        orig_init(self, *args, **kwargs)

    aioice.Connection.__init__ = _patched_init  # type: ignore[method-assign]
    _SHARED_ICE_PATCHED = True
    logger.info("Applied shared ICE credentials for Unitree Remote WebRTC")


_T = TypeVar("_T", bound=Timestamped)


def time_is_now(x: _T) -> _T:
    x.ts = time.time()
    return x


@dataclass
class SerializableVideoFrame:
    """Pickleable wrapper for av.VideoFrame with all metadata"""

    data: np.ndarray
    pts: int | None = None
    time: float | None = None
    dts: int | None = None
    width: int | None = None
    height: int | None = None
    format: str | None = None

    @classmethod
    def from_av_frame(cls, frame):  # type: ignore[no-untyped-def]
        return cls(
            data=frame.to_ndarray(format="rgb24"),
            pts=frame.pts,
            time=frame.time,
            dts=frame.dts,
            width=frame.width,
            height=frame.height,
            format=frame.format.name if hasattr(frame, "format") and frame.format else None,
        )

    def to_ndarray(self, format=None):  # type: ignore[no-untyped-def]
        return self.data


class UnitreeWebRTCConnection(Resource):
    _SPORT_API_ID_RAGEMODE: int = 2059
    _SPORT_API_ID_FREEAVOID: int = 2048

    def __init__(
        self,
        ip: str | None = None,
        mode: str = "ai",
        aes_128_key: str | None = None,
        *,
        connection_method: str = "local",
        username: str | None = None,
        password: str | None = None,
        serial_number: str | None = None,
        region: str = "cn",
        device_type: str = "Go2",
    ) -> None:
        self.ip = ip
        self.mode = mode
        self.stop_timer: threading.Timer | None = None
        self.cmd_vel_timeout = 0.2
        # 接受 4g / sta-t 作为 remote 的易读别名, 其余未知值继续按历史
        # local 行为处理, 从而保持旧命令兼容.
        method = (connection_method or "local").strip().lower()
        self._connection_method = method

        if method in ("remote", "4g", "sta-t"):
            # Remote 不依赖 robot_ip 或 LAN AES key. 账号负责云登录,
            # SN 负责把信令请求精确路由到账号绑定的 Go2.
            if not username or not password or not serial_number:
                raise ValueError(
                    "Remote WebRTC requires unitree_username, unitree_password, and unitree_serial"
                )
            # 必须先打共享 ICE 补丁, 后续底层 connect() 才会用它创建 Offer.
            _ensure_shared_ice_credentials()
            # LegionConnection 是 unitree-webrtc-connect 的底层驱动.
            # 它在构造阶段登录云端取得 token; connect() 阶段再获取临时
            # TURN 凭据, 发送 SDP Offer 并等待 DataChannel 完成设备验证.
            self.conn = LegionConnection(
                WebRTCConnectionMethod.Remote,
                serialNumber=serial_number,
                username=username,
                password=password,
                region=region,
                device_type=device_type,
            )
            # 底层默认同时配置 Unitree TURN 和 Google STUN. 这里仅对当前
            # Remote 实例关闭 Google STUN, 避免 Clash/TUN 下多出不稳定的
            # server-reflexive 候选, 同时保留云端返回的 Unitree TURN.
            # 这不是 aiortc 的强制 relay-only 策略: host candidate 仍可能被
            # ICE 收集; 但跨公网 4G 场景最终实测会选中 TURN relay 路径.
            orig_cfg = self.conn.create_webrtc_configuration

            def _turn_only_cfg(
                turn_server_info: Any, stunEnable: bool = True, turnEnable: bool = True
            ) -> Any:
                return orig_cfg(turn_server_info, stunEnable=False, turnEnable=turnEnable)

            self.conn.create_webrtc_configuration = _turn_only_cfg  # type: ignore[method-assign]
        elif method in ("local_ap", "ap"):
            # AP 仍是本地直连, 新固件握手需要每台设备自己的 AES key.
            self.conn = LegionConnection(WebRTCConnectionMethod.LocalAP, aes_128_key=aes_128_key)
        else:
            # 默认 LocalSTA 路径完全保留: 用局域网 IP 直接向 Go2 交换 SDP.
            if not ip:
                raise ValueError("LocalSTA WebRTC requires robot_ip")
            self.conn = LegionConnection(
                WebRTCConnectionMethod.LocalSTA, ip=self.ip, aes_128_key=aes_128_key
            )
        # 对外构造函数保持同步语义: 返回前必须完成 WebRTC 和设备验证.
        self.connect()

    def connect(self) -> None:
        self.loop = asyncio.new_event_loop()

        async def async_connect() -> None:
            # Remote 时, 底层依次完成公钥/TURN 获取, SDP Offer/Answer,
            # ICE 连通性检查, DTLS 握手, DataChannel 打开和 Unitree 验证.
            # LocalSTA 时, SDP 改由机器人局域网 HTTP 端口直接交换.
            await self.conn.connect()
            # 关闭机端的省流量模式, 否则大带宽点云 topic 可能不推送.
            await self.conn.datachannel.disableTrafficSaving(True)

            # native 解码器把压缩 voxel 二进制还原为 Nx3 点坐标.
            self.conn.datachannel.set_decoder(decoder_type="native")
            # 显式开启 Go2 内置雷达点云服务. 只有 lidar_state 不代表点云已推送.
            self.conn.datachannel.pub_sub.publish_without_callback(RTC_TOPIC["ULIDAR_SWITCH"], "on")

            # api_id=1002 是 SelectMode. 这里在连接完成后把运动控制器切到
            # self.mode, 默认值为 ai; 后续站立和速度指令才走正确控制器.
            await self.conn.datachannel.pub_sub.publish_request_new(
                RTC_TOPIC["MOTION_SWITCHER"], {"api_id": 1002, "parameter": {"name": self.mode}}
            )

        def start_background_loop() -> None:
            asyncio.set_event_loop(self.loop)
            self.loop.run_forever()

        self.thread = threading.Thread(target=start_background_loop, daemon=True)
        self.thread.start()

        # WebRTC 底层是 asyncio, DimOS 上层是同步 Resource API. 这里把协程
        # 投递到专用线程, 并阻塞等待结果, 让云登录/ICE/DTLS 错误原样抛给启动方.
        try:
            asyncio.run_coroutine_threadsafe(async_connect(), self.loop).result()
        except Exception:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=DEFAULT_THREAD_JOIN_TIMEOUT)
            raise

    def start(self) -> None:
        pass

    def stop(self) -> None:
        # Cancel timer
        if self.stop_timer:
            self.stop_timer.cancel()
            self.stop_timer = None

        async def async_disconnect() -> None:
            try:
                # 先发送零速度, 再关闭 PeerConnection. 这是网络层兜底,
                # 上层 GO2Connection.stop() 还会先发送一次 StandDown.
                self.conn.datachannel.pub_sub.publish_without_callback(
                    RTC_TOPIC["WIRELESS_CONTROLLER"],
                    data={"lx": 0, "ly": 0, "rx": 0, "ry": 0},
                )
                await self.conn.disconnect()
            except Exception:
                pass

        if self.loop.is_running():
            # 当前实现是 best-effort 清理: 提交断连协程后立即请求停止 loop,
            # 没有等待 Future 完成. 正常停止一般可用, 但连续快速重连时可能
            # 留下未完成的 ICE/DTLS 清理; 若后续遇到会话占用应优先检查这里.
            asyncio.run_coroutine_threadsafe(async_disconnect(), self.loop)

            self.loop.call_soon_threadsafe(self.loop.stop)

        if self.thread.is_alive():
            self.thread.join(timeout=DEFAULT_THREAD_JOIN_TIMEOUT)

    def move(self, twist: Twist, duration: float = 0.0) -> bool:
        """Send movement command to the robot using Twist commands.

        Args:
            twist: Twist message with linear and angular velocities
            duration: How long to move (seconds). If 0, command is continuous

        Returns:
            bool: True if command was sent successfully
        """
        x, y, yaw = twist.linear.x, twist.linear.y, twist.angular.z

        # WebRTC coordinate mapping:
        # x - Positive right, negative left
        # y - positive forward, negative backwards
        # yaw - Positive rotate right, negative rotate left
        async def async_move() -> None:
            self.conn.datachannel.pub_sub.publish_without_callback(
                RTC_TOPIC["WIRELESS_CONTROLLER"],
                data={"lx": -y, "ly": x, "rx": -yaw, "ry": 0},
            )

        async def async_move_duration() -> None:
            """Send movement commands continuously for the specified duration."""
            start_time = time.time()
            sleep_time = 0.01

            while time.time() - start_time < duration:
                await async_move()
                await asyncio.sleep(sleep_time)

        # Cancel existing timer and start a new one
        if self.stop_timer:
            self.stop_timer.cancel()

        # Auto-stop after 0.5 seconds if no new commands
        self.stop_timer = threading.Timer(self.cmd_vel_timeout, self.stop_movement)
        self.stop_timer.daemon = True
        self.stop_timer.start()

        try:
            if duration > 0:
                # Send continuous move commands for the duration
                future = asyncio.run_coroutine_threadsafe(async_move_duration(), self.loop)
                future.result()
                # Stop after duration
                self.stop_movement()
            else:
                # Single command for continuous movement
                future = asyncio.run_coroutine_threadsafe(async_move(), self.loop)
                future.result()
            return True
        except Exception as e:
            logger.warning("Failed to send movement command: %s", e)
            return False

    # 把 Unitree 的 callback 式 topic 订阅转换成 DimOS 使用的 Observable.
    # 订阅和取消订阅都必须回到 WebRTC 所属的 asyncio 线程执行.
    def unitree_sub_stream(self, topic_name: str):  # type: ignore[no-untyped-def]
        def subscribe_in_thread(cb) -> None:  # type: ignore[no-untyped-def]
            # Run the subscription in the background thread that has the event loop
            def run_subscription() -> None:
                self.conn.datachannel.pub_sub.subscribe(topic_name, cb)

            # Use call_soon_threadsafe to run in the background thread
            self.loop.call_soon_threadsafe(run_subscription)

        def unsubscribe_in_thread(cb) -> None:  # type: ignore[no-untyped-def]
            # Run the unsubscription in the background thread that has the event loop
            def run_unsubscription() -> None:
                self.conn.datachannel.pub_sub.unsubscribe(topic_name)

            # Use call_soon_threadsafe to run in the background thread
            self.loop.call_soon_threadsafe(run_unsubscription)

        return callback_to_observable(
            start=subscribe_in_thread,
            stop=unsubscribe_in_thread,
        )

    # 同步 RPC 桥接: 上层传入 topic + api_id, 底层包装成带唯一 id 的请求,
    # 通过 DataChannel 发送并等待同 id 的响应后再返回.
    def publish_request(self, topic: str, data: dict[Any, Any]) -> Any:
        future = asyncio.run_coroutine_threadsafe(
            self.conn.datachannel.pub_sub.publish_request_new(topic, data), self.loop
        )
        return future.result()

    @simple_mcache
    def raw_lidar_stream(self) -> Observable[RawLidarMsg]:
        # 压缩体素点云走 DataChannel 二进制帧, 不是 WebRTC Video Track.
        return backpressure(self.unitree_sub_stream(RTC_TOPIC["ULIDAR_ARRAY"]))

    @simple_mcache
    def raw_odom_stream(self) -> Observable[Pose]:
        # Go2 内部雷达里程计 topic, 实测 4G Remote 约 18 Hz.
        return backpressure(self.unitree_sub_stream(RTC_TOPIC["ROBOTODOM"]))

    @simple_mcache
    def lidar_stream(self) -> Observable[PointCloud2]:
        return backpressure(
            self.raw_lidar_stream().pipe(
                ops.map(pointcloud2_from_webrtc_lidar),
                ops.map(time_is_now),
                # repair_stale_ts(),
            )
        )

    @simple_mcache
    def tf_stream(self) -> Observable[Transform]:
        base_link = functools.partial(Transform.from_pose, "base_link")
        return backpressure(self.odom_stream().pipe(ops.map(base_link)))

    @simple_mcache
    def odom_stream(self) -> Observable[Pose]:
        return backpressure(
            self.raw_odom_stream().pipe(
                ops.map(
                    Odometry.from_msg,
                ),
                ops.map(time_is_now),
            )
        )

    @simple_mcache
    def video_stream(self) -> Observable[Image]:
        return backpressure(
            self.raw_video_stream().pipe(
                ops.filter(lambda frame: frame is not None),
                ops.map(
                    lambda frame: Image.from_numpy(
                        # np.ascontiguousarray(frame.to_ndarray("rgb24")),
                        frame.to_ndarray(format="rgb24"),  # type: ignore[attr-defined]
                        format=ImageFormat.RGB,  # Frame is RGB24, not BGR
                        frame_id="camera_optical",
                    ),
                ),
                ops.map(time_is_now),
            )
        )

    @simple_mcache
    def lowstate_stream(self) -> Observable[LowStateMsg]:
        return backpressure(self.unitree_sub_stream(RTC_TOPIC["LOW_STATE"]))

    def standup(self) -> bool:
        # 高层运动 API 与 LocalSTA 使用完全相同的 topic 和 api_id;
        # 4G 只改变底层传输路径, 不改变业务协议.
        return bool(self.publish_request(RTC_TOPIC["SPORT_MOD"], {"api_id": SPORT_CMD["StandUp"]}))

    def balance_stand(self) -> bool:
        """Activate BalanceStand mode — enables WIRELESS_CONTROLLER joystick commands."""
        return bool(
            self.publish_request(RTC_TOPIC["SPORT_MOD"], {"api_id": SPORT_CMD["BalanceStand"]})
        )

    def set_obstacle_avoidance(self, enabled: bool = True) -> None:
        self.publish_request(
            RTC_TOPIC["OBSTACLES_AVOID"],
            {"api_id": 1001, "parameter": {"enable": int(enabled)}},
        )

    def set_motion_mode(self, name: str) -> None:
        """Select the top-level motion controller via the motion switcher.

        mcf is the AI/sport controller that traverses stairs. normal is basic.
        """
        # api_id 1001 = CheckMode, 1002 = SelectMode, param {"name": <mode>}.
        current = None
        try:
            resp = self.publish_request(RTC_TOPIC["MOTION_SWITCHER"], {"api_id": 1001})
            current = json.loads(resp["data"]["data"]).get("name")
        except (KeyError, TypeError, ValueError) as e:
            logger.warning("Motion mode check failed: %s", e)
        if current == name:
            return
        self.publish_request(
            RTC_TOPIC["MOTION_SWITCHER"],
            {"api_id": 1002, "parameter": {"name": name}},
        )
        time.sleep(5)

    def free_avoid(self, enabled: bool = True) -> bool:
        """Toggle SportClient's hidden AI obstacle-avoidance (FreeAvoid).

        Mirrors Unitree's official SDK call:
            int32_t SportClient::FreeAvoid(bool flag);
        with constants:
            ROBOT_SPORT_API_ID_FREEAVOID = 2048
            topic = "rt/api/sport/request"  (== RTC_TOPIC["SPORT_MOD"])
            parameter is serialised via JsonizeDataBool with the field "data".

        This switch is not exposed via the Go2 app or remote controller. It is
        sport-mode-embedded and orthogonal to ObstaclesAvoidClient.SwitchSet
        (`set_obstacle_avoidance`, api_id=1001) — they may be enabled together.
        The robot must already be in sport mode and have completed
        `balance_stand` before calling this, otherwise the request may be
        rejected on the device side.

        Args:
            enabled: True to turn FreeAvoid on, False to turn it off.

        Returns:
            bool: True if the device acknowledged the request, False otherwise.
        """
        return bool(
            self.publish_request(
                RTC_TOPIC["SPORT_MOD"],
                {"api_id": self._SPORT_API_ID_FREEAVOID, "parameter": {"data": bool(enabled)}},
            )
        )

    def free_walk(self) -> bool:
        """Activate FreeWalk locomotion mode — enables walking and velocity commands."""
        return bool(self.publish_request(RTC_TOPIC["SPORT_MOD"], {"api_id": SPORT_CMD["FreeWalk"]}))

    def set_rage_mode(self, enable: bool) -> bool:
        """Toggle Rage Mode (api 2059) over WebRTC, both directions.

        BalanceStand → 2059 {data:enable} → SwitchJoystick(enable). When on,
        normal move() twists drive at the ~2.5 m/s rage envelope.
        """
        # Re-establish BalanceStand before toggling (notes: always BalanceStand
        # before flipping Rage).
        if not self.balance_stand():
            logger.warning("balance_stand() failed before rage toggle — proceeding")
        time.sleep(0.3)

        rage_ok = bool(
            self.publish_request(
                RTC_TOPIC["SPORT_MOD"],
                {"api_id": self._SPORT_API_ID_RAGEMODE, "parameter": {"data": enable}},
            )
        )
        if not rage_ok:
            return False

        if enable:
            time.sleep(2.0)  # let FsmRageMode transition settle
        joystick_ok = bool(
            self.publish_request(
                RTC_TOPIC["SPORT_MOD"],
                {"api_id": SPORT_CMD["SwitchJoystick"], "parameter": {"data": enable}},
            )
        )
        if not joystick_ok:
            logger.warning("SwitchJoystick failed after rage toggle", enabled=enable)
        return joystick_ok

    def liedown(self) -> bool:
        return bool(
            self.publish_request(RTC_TOPIC["SPORT_MOD"], {"api_id": SPORT_CMD["StandDown"]})
        )

    async def handstand(self):  # type: ignore[no-untyped-def]
        return self.publish_request(
            RTC_TOPIC["SPORT_MOD"],
            {"api_id": SPORT_CMD["Standup"], "parameter": {"data": True}},
        )

    def color(self, color: VUI_COLOR = VUI_COLOR.RED, colortime: int = 60) -> bool:
        return self.publish_request(  # type: ignore[no-any-return]
            RTC_TOPIC["VUI"],
            {
                "api_id": 1001,
                "parameter": {
                    "color": color,
                    "time": colortime,
                },
            },
        )

    @simple_mcache
    def raw_video_stream(self) -> Observable[VideoMessage]:
        subject: Subject[VideoMessage] = Subject()
        stop_event = threading.Event()

        from aiortc import MediaStreamTrack

        async def accept_track(track: MediaStreamTrack) -> None:
            # 视频不走 DataChannel. aiortc 从独立的 WebRTC media track 解码
            # av.VideoFrame, 再转换成可跨 DimOS worker 传输的 numpy 包装对象.
            while True:
                if stop_event.is_set():
                    return
                frame = await track.recv()
                serializable_frame = SerializableVideoFrame.from_av_frame(frame)  # type: ignore[no-untyped-call]
                subject.on_next(serializable_frame)

        self.conn.video.add_track_callback(accept_track)

        # 回调注册后再向 Go2 发送 video=on, 避免首帧早于消费者建立.
        def switch_video_channel() -> None:
            self.conn.video.switchVideoChannel(True)

        self.loop.call_soon_threadsafe(switch_video_channel)

        def stop() -> None:
            stop_event.set()  # Signal the loop to stop
            self.conn.video.track_callbacks.remove(accept_track)

            # Run the video channel switching off in the background thread
            def switch_video_channel_off() -> None:
                self.conn.video.switchVideoChannel(False)

            self.loop.call_soon_threadsafe(switch_video_channel_off)

        return subject.pipe(ops.finally_action(stop))

    def get_video_stream(self, fps: int = 30) -> Observable[Image]:
        """Get the video stream from the robot's camera.

        Implements the AbstractRobot interface method.

        Args:
            fps: Frames per second. This parameter is included for API compatibility,
                 but doesn't affect the actual frame rate which is determined by the camera.

        Returns:
            Observable: An observable stream of video frames or None if video is not available.
        """
        return self.video_stream()

    def stop_movement(self) -> None:
        """Cancel the auto-stop timer (used by move() for continuous commands)."""
        if self.stop_timer:
            self.stop_timer.cancel()
            self.stop_timer = None

    def disconnect(self) -> None:
        """Disconnect from the robot and clean up resources."""
        # Cancel timer
        if self.stop_timer:
            self.stop_timer.cancel()
            self.stop_timer = None

        if hasattr(self, "conn"):

            async def async_disconnect() -> None:
                try:
                    await self.conn.disconnect()
                except:
                    pass

            if hasattr(self, "loop") and self.loop.is_running():
                asyncio.run_coroutine_threadsafe(async_disconnect(), self.loop)

        if hasattr(self, "loop") and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)

        if hasattr(self, "thread") and self.thread.is_alive():
            self.thread.join(timeout=DEFAULT_THREAD_JOIN_TIMEOUT)
