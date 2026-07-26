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

import platform
import re
from typing import Literal, TypeAlias

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from dimos.constants import DEFAULT_BUILD_NATIVE
from dimos.models.vl.types import VlModelName
from dimos.visualization.rerun.constants import (
    RERUN_ENABLE_WEB,
    RERUN_OPEN_DEFAULT,
    RerunOpenOption,
    ViewerBackend,
)

TransportBackend: TypeAlias = Literal["lcm", "zenoh"]
NavigationTraceLevel: TypeAlias = Literal["off", "summary", "full", "forensic"]


def _get_all_numbers(s: str) -> list[float]:
    return [float(x) for x in re.findall(r"-?\d+\.?\d*", s)]


def _default_transport() -> TransportBackend:
    if platform.system() == "Darwin":
        return "zenoh"
    return "lcm"


class GlobalConfig(BaseSettings):
    robot_ip: str | None = None
    robot_ips: str | None = None
    # 新固件的 AES-128 设备密钥只用于 LocalSTA / LocalAP 的局域网握手.
    # 4G Remote 使用云端账号令牌和临时 TURN 凭据, 不读取这个字段.
    unitree_aes_128_key: str | None = None
    # 连接方式选择. local 是默认的局域网 LocalSTA; remote / 4g / sta-t
    # 都会进入 Unitree 云信令路径. 这些字段也会被 CLI 自动生成为
    # --unitree-webrtc-method 等参数, 并可由同名大写环境变量读取.
    unitree_webrtc_method: str = "local"
    # Remote 构造底层驱动时会立刻用账号密码登录 Unitree 云并取得 token.
    # 密码不要提交到仓库; 生产环境应通过环境变量或专用密钥管理器注入.
    unitree_username: str | None = None
    unitree_password: str | None = None
    # 云端用 SN 把本机 SDP Offer 路由到已经注册在线的那一台 Go2.
    unitree_serial: str | None = None
    # cn 连接中国区 robot-api.unitree.com; global 连接海外区云端.
    unitree_region: str = "cn"
    xarm7_ip: str | None = None
    xarm6_ip: str | None = None
    can_port: str | None = None
    device_path: str | None = None  # device path for real robot (e.g. /dev/ttyUSB0)
    simulation: str = ""
    replay: bool = False
    replay_db: str = "go2_short"
    # 默认 True: 启动时清空持久化记忆 (landmarks.json / CLIP Chroma / temporal DB),
    # 避免重启后旧 odom 坐标与 CLIP 房间图残留导致误匹配. 需要跨次运行保留记忆时用
    # --no-new-memory 或 DIMOS_NEW_MEMORY=false.
    new_memory: bool = True
    viewer: ViewerBackend = "rerun"
    rerun_open: RerunOpenOption = RERUN_OPEN_DEFAULT
    rerun_web: bool = RERUN_ENABLE_WEB
    rerun_host: str | None = None
    rerun_websocket_server_port: int = 3030
    n_workers: int = 2
    memory_limit: str = "auto"
    mujoco_camera_position: str | None = None
    mujoco_room: str | None = None
    mujoco_room_from_occupancy: str | None = None
    mujoco_global_costmap_from_occupancy: str | None = None
    mujoco_global_map_from_pointcloud: str | None = None
    mujoco_start_pos: str = "-1.0, 1.0"
    mujoco_steps_per_frame: int = 7
    scene_package: str | None = None
    robot_model: str | None = None
    robot_id: str | None = None
    robot_width: float = 0.3
    robot_rotation_diameter: float = 0.6
    nerf_speed: float = 1.0
    planner_robot_speed: float | None = None
    mcp_port: int = 9990
    # `DIMOS_TRANSPORT` (or `.env`) is the single switch read by every process
    # (dimos, humancli, agentspy, dtop). The `transport` alias keeps the bare
    # env name and the `--transport` CLI flag (which sets the field by name) working.
    transport: TransportBackend = Field(
        default_factory=_default_transport,
        validation_alias=AliasChoices("DIMOS_TRANSPORT", "transport"),
    )
    build_native: bool = DEFAULT_BUILD_NATIVE
    dtop: bool = False
    obstacle_avoidance: bool = True
    # Whether to enable Unitree SportClient's hidden AI obstacle-avoidance
    # switch (SportClient::FreeAvoid, sport api_id=2048). This capability is
    # hidden in Unitree's official SDK and not exposed by the app or remote.
    # Independent from `obstacle_avoidance` (ObstaclesAvoidClient.SwitchSet
    # api_id=1001) — both can be enabled simultaneously.
    free_avoid: bool = False
    detection_model: VlModelName = "moondream"
    listen_host: str = "127.0.0.1"
    dimsim_scene: str = "apt"
    dimsim_port: int = 8090
    # Navigation tracing is intentionally disabled by default.  The online
    # path only queues bounded records; report generation is always offline.
    navigation_trace_level: NavigationTraceLevel = "off"
    navigation_trace_costmap_min_interval_sec: float = 5.0
    navigation_trace_roi_interval_sec: float = 5.0
    navigation_trace_lowstate_hz: float = 2.0
    navigation_trace_webrtc_stats_hz: float = 0.0
    navigation_trace_scalar_queue_items: int = 2048
    navigation_trace_blob_queue_items: int = 2
    navigation_trace_scalar_max_bytes_per_producer: int = 67_108_864
    navigation_trace_blob_max_bytes_per_producer: int = 134_217_728
    navigation_trace_blob_max_item_bytes: int = 33_554_432
    navigation_trace_min_free_disk_bytes: int = 5_368_709_120
    navigation_trace_forensic_ack: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_assignment=True,
    )

    def update(self, **kwargs: object) -> None:
        """Update config fields in place."""
        for key, value in kwargs.items():
            if not hasattr(self, key):
                raise AttributeError(f"GlobalConfig has no field '{key}'")
            setattr(self, key, value)

    @property
    def unitree_connection_type(self) -> str:
        if self.replay:
            return "replay"
        if self.simulation:
            return self.simulation
        return "webrtc"

    @property
    def mujoco_start_pos_float(self) -> tuple[float, float]:
        x, y = _get_all_numbers(self.mujoco_start_pos)
        return (x, y)

    @property
    def mujoco_camera_position_float(self) -> tuple[float, ...]:
        if self.mujoco_camera_position is None:
            return (-0.906, 0.008, 1.101, 4.931, 89.749, -46.378)
        return tuple(_get_all_numbers(self.mujoco_camera_position))


global_config = GlobalConfig()
