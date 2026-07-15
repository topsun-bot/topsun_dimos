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

from collections import deque
from dataclasses import dataclass, field, replace
import math
import os
import threading
import time
from typing import Any, cast
import uuid

import numpy as np
from reactivex.disposable import Disposable

# skill 装饰器: 将方法暴露给 LLM agent 作为可调用工具
from dimos.agents.annotation import skill

# 能力常量, 用于声明该 skill container 提供移动能力
from dimos.agents.capabilities import CAP_MOVEMENT

# rpc 装饰器: 让方法可通过模块间 RPC 调用, 但不暴露给 LLM
from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.core.stream import In
from dimos.models.qwen.bbox import BBox
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Quaternion import Quaternion
from dimos.msgs.geometry_msgs.Vector3 import Vector3, make_vector3
from dimos.msgs.sensor_msgs.Image import Image
from dimos.navigation.base import NavigationState
from dimos.navigation.frontier_exploration.wavefront_frontier_goal_selector import (
    WavefrontFrontierExplorer,
)
from dimos.navigation.navigation_spec import NavigationInterfaceSpec
from dimos.navigation.topology import TopologyGraph

# 视觉查询工具: bbox 缩放, 物体检测, bbox 解析, 偏航角计算
from dimos.navigation.visual.query import (
    _scale_bbox_to_image,
    get_object_bbox_from_image,
    parse_simple_bbox_line,
    yaw_offset_from_bbox,
)
from dimos.perception.object_tracking_spec import ObjectTrackingSpec
from dimos.perception.spatial_memory_spec import SpatialMemorySpec
from dimos.robot.unitree.unitree_skill_container import UnitreeSkillContainer
from dimos.types.door_memory_spec import SpatialLandmarkMemorySpec
from dimos.types.relocalization_spec import RelocalizationStateSpec
from dimos.types.robot_location import RobotLocation
from dimos.types.spatial_record import RecordType, SpatialRecord
from dimos.utils.generic import extract_json_from_llm_response
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

# navigate_with_text 的 fallback 步骤顺序. 不同策略代表不同的搜索优先级,
# 通过环境变量 DIMOS_NAV_FALLBACK 选择. 每个策略是一个有序元组,
# 前面的步骤失败后才尝试后面的, 形成逐级降级的搜索链.
# 策略 object_room: 只做地标定位 + 逐房间扫描, 适合明确知道物体在哪个房间的场景
_NAV_FALLBACK_OBJECT_ROOM = (
    "landmark",  # 物体地标定位 -> 导航过去 + 360 度扫描; 找不到则继续降级
    "room_sweep",  # 遍历所有 ROOM 记录 + 360 度扫描
)
_NAV_FALLBACK_SEMANTIC = (
    "landmark",  # L3: 地标 JSON + 拓扑图 + 重新扫描
    "in_frame",  # L2: VLM bbox 检测 + 物体跟踪
    "room_sweep",  # L4: 遍历每个 ROOM + 360 度扫描
    "vlm_memory",  # L5: 对已存储的图片批量跑 VLM
    "clip_map",  # L6: CLIP 语义地图匹配
    "tagged",  # L1: CLIP 标记的位置 (放最后以减少误报)
)
# 策略 room_first: 优先房间级搜索, 适合物体可能在多个房间的场景
_NAV_FALLBACK_ROOM_FIRST = (
    "tagged",
    "in_frame",
    "landmark",
    "room_sweep",
    "vlm_memory",
    "clip_map",
)
_NAV_FALLBACK_STRATEGIES = {
    "object_room": _NAV_FALLBACK_OBJECT_ROOM,
    "semantic": _NAV_FALLBACK_SEMANTIC,
    "room_first": _NAV_FALLBACK_ROOM_FIRST,
}


# 旋转步长: tag_room 全景拍摄和房间内 360 度扫描共用.
# 真实 Go2 硬件对小角度旋转不精确 - 较大步长 (如 90 度) 比小步长更可靠.
# 可通过 DIMOS_ROTATION_STEP_DEG 环境变量覆盖.
def _rotation_step_deg() -> float:
    import os

    return float(os.getenv("DIMOS_ROTATION_STEP_DEG", "90"))


def _panorama_rotations() -> int:
    """全景拍摄时在初始朝向之后的额外旋转次数 (步长 x 3 约等于完整覆盖 360 度)."""
    import os

    override = os.getenv("DIMOS_ROOM_SCAN_ROTATIONS")
    if override:
        return max(1, int(override))
    return 3


def _search_rotation_step_deg() -> float:
    """物体搜索扫描时的旋转步长 - 比标记步长更小, 以获得更精细的视觉采集.

    默认值: 标记步长 - 20 度, 最小 10 度.
    可通过 DIMOS_SEARCH_ROTATION_STEP_DEG 环境变量覆盖.
    """
    import os

    override = os.getenv("DIMOS_SEARCH_ROTATION_STEP_DEG")
    if override:
        return max(1.0, float(override))
    return max(10.0, _rotation_step_deg() - 20.0)


# VLM 物体检测 prompt: 强制要求输出中文物体名, 避免后续 CLIP 匹配时中英文不一致.
# 要求返回 JSON 数组, 每项包含名称/描述/bbox, 用于后续的物体定位和空间记忆.
_VLM_OBJECT_LIST_PROMPT = (
    "列出图中所有可单独指认的物体（家具、电器、设备、装饰、人等）。\n"
    "【硬性要求】JSON 里每个 name 必须是 1–4 个汉字的中文名词。"
    "禁止英文：不要写 computer/desk/chair/monitor/table。\n"
    "正确示例：电脑、书桌、办公椅、灭火器、电视。\n"
    "Return ONLY a JSON array: "
    '[{"name": "<中文名>", "description": "<简短中文说明>", "bbox": [x1, y1, x2, y2]}]. '
    "bbox 为物体在画面中的边界框（像素坐标）。跳过墙面、地面、天花板、门。若无物体，返回 []."
)

# 相机水平视场角 (度), 用于将 bbox 中心转换为物体相对机器人的方位角.
# Go2 默认相机 HFOV 约 69 度, 可通过 DIMOS_CAMERA_HFOV_DEG 环境变量覆盖.
_CAMERA_HFOV_DEG = float(__import__("os").getenv("DIMOS_CAMERA_HFOV_DEG", "69"))


def _search_float_env(name: str, default: float, *, minimum: float = 0.0) -> float:
    """Read a non-negative object-search tuning value from the environment."""
    try:
        return max(minimum, float(os.getenv(name, str(default))))
    except ValueError:
        return default


def _enroute_object_search_enabled() -> bool:
    """Return whether the opt-in en-route object-search behavior is enabled."""
    value = os.getenv("DIMOS_ENROUTE_OBJECT_SEARCH_ENABLED", "false")
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class _SearchFrameSnapshot:
    search_id: str
    leg_id: int
    image: Image
    image_ts: float
    capture_pose_world: PoseStamped
    map_metadata: dict[str, Any]
    submitted_at: float


@dataclass(frozen=True)
class _SearchHit:
    snapshot: _SearchFrameSnapshot
    bbox: BBox
    detected_at: float
    object_yaw_world: float
    object_yaw_map: float | None


@dataclass
class _ObjectSearchContext:
    search_id: str
    query: str
    created_at: float = field(default_factory=time.time)
    hit_event: threading.Event = field(default_factory=threading.Event, repr=False)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    hit: _SearchHit | None = None
    active_leg_id: int = 0
    leg_origin: tuple[float, float] | None = None
    monitor_enabled: bool = False
    vlm_in_flight: bool = False
    last_request_at: float = 0.0
    terminal_result: str | None = None


class _EnrouteObjectHitError(Exception):
    """Internal control-flow signal raised when an en-route VLM request hits."""


# VLM 仍返回英文物体名时的兜底映射表 (遗留标签或 VLM 遵循指令不严格时使用).
_VLM_NAME_EN_TO_ZH: dict[str, str] = {
    "computer": "电脑",
    "computer monitor": "电脑",
    "monitor": "显示器",
    "screen": "显示器",
    "desk": "书桌",
    "office desk": "书桌",
    "office chair": "办公椅",
    "chair": "椅子",
    "table": "桌子",
    "conference table": "会议桌",
    "small table": "小桌",
    "curtain": "窗帘",
    "fire extinguisher": "灭火器",
    "extinguisher": "灭火器",
    "television": "电视",
    "tv": "电视",
    "tv stand": "电视柜",
    "suitcase": "行李箱",
    "clock": "闹钟",
    "person": "人",
    "cabinet": "柜子",
    "filing cabinet": "文件柜",
    "file cabinet": "文件柜",
}


def _normalize_vlm_object_name(raw: str) -> str:
    """将 VLM 返回的物体名统一为中文, 映射常见英文物体名.

    优先使用 _VLM_NAME_EN_TO_ZH 映射表进行转换; 若映射后仍为纯英文,
    发出警告提示用户重启后重新标记房间.
    """
    name = raw.strip()
    if not name:
        return name
    mapped = _VLM_NAME_EN_TO_ZH.get(name.lower())
    if mapped:
        if mapped != name:
            logger.info("[VLM] normalized object name %r → %r", name, mapped)
        return mapped
    # 映射表未命中且仍为纯 ASCII (无中文字符), 说明 VLM 未遵循中文指令
    if name.replace(" ", "").isascii() and not any("\u4e00" <= c <= "\u9fff" for c in name):
        logger.warning(
            "[VLM] object name %r is still English — re-tag room after restart if this persists",
            name,
        )
    return name


def _create_vl_model() -> Any:
    """根据环境变量配置创建 VLM (视觉语言模型) 实例.

    API key 与 endpoint 的配对是严格的 - DashScope key 只发往 DashScope endpoint,
    OpenAI key 只发往 OpenAI endpoint, 不会混用.

    ======================== ===================== ============================
    用途                       环境变量               默认值
    ======================== ===================== ============================
    Provider                  DIMOS_VLM_PROVIDER     auto: DASHSCOPE_API_KEY
    模型名                    DIMOS_VLM_MODEL_NAME   qwen3.6-plus / gpt-4o-mini
    ======================== ===================== ============================

    Provider 自动检测优先级:
    DASHSCOPE_API_KEY       -> dashscope (兼容模式)
    DIMOS_VLM_API_KEY      -> openai
    OPENAI_API_KEY          -> openai
    ALIBABA_API_KEY         -> qwen
    """
    import os

    from dimos.models.vl.openai import OpenAIVlModel

    provider = os.getenv("DIMOS_VLM_PROVIDER", "").lower().strip()
    model_name = os.getenv("DIMOS_VLM_MODEL_NAME")

    # Provider 自动检测: 按优先级检查各 API key 环境变量
    if not provider:
        if os.getenv("DASHSCOPE_API_KEY"):
            provider = "dashscope"
        elif os.getenv("DIMOS_VLM_API_KEY"):
            provider = "openai"
        elif os.getenv("OPENAI_API_KEY"):
            provider = "openai"
        elif os.getenv("ALIBABA_API_KEY"):
            provider = "qwen"

    model: Any = None

    if provider == "dashscope":
        # 使用原生 DashScope MultiModalConversation API (非兼容模式)
        from dimos.models.vl.dashscope import DashScopeVlModel

        model = DashScopeVlModel()
        if model_name:
            model.config.model_name = model_name
        return model

    if provider == "openai":
        model = OpenAIVlModel()
        if model_name:
            model.config.model_name = model_name

        # 检查是否配置了云端 fallback (本地不可用时自动切换)
        cloud_api_key = os.getenv("DIMOS_VLM_CLOUD_API_KEY")
        cloud_base_url = os.getenv("DIMOS_VLM_CLOUD_BASE_URL")
        if cloud_api_key and cloud_base_url:
            from dimos.models.vl.fallback import FallbackVlModel

            cloud_model = OpenAIVlModel()
            cloud_model.config.api_key = cloud_api_key
            cloud_model.config.base_url = cloud_base_url
            cloud_model_name = os.getenv("DIMOS_VLM_CLOUD_MODEL_NAME")
            if cloud_model_name:
                cloud_model.config.model_name = cloud_model_name
            elif model_name:
                cloud_model.config.model_name = model_name

            cooldown = float(os.getenv("DIMOS_VLM_FALLBACK_COOLDOWN", "60"))
            model = FallbackVlModel(model, cloud_model, cooldown_seconds=cooldown)
            logger.info(
                "[VLM] fallback enabled: local=%s cloud=%s cooldown=%.0fs",
                os.getenv("DIMOS_VLM_BASE_URL", "default"),
                cloud_base_url,
                cooldown,
            )

        return model

    if provider == "moondream":
        from dimos.models.vl.moondream import MoondreamVlModel

        return MoondreamVlModel()

    # Default: Qwen (遗留模式, 使用 ALIBABA_API_KEY)
    from dimos.models.vl.qwen import QwenVlModel

    model = QwenVlModel()
    if model_name:
        model.config.model_name = model_name
    return model


def _log_vlm_runtime_config(vl_model: Any) -> None:
    """记录当前激活的 VLM 后端信息 (不输出密钥等敏感信息)."""
    import os

    model_cls = type(vl_model).__name__
    model_name = getattr(getattr(vl_model, "config", None), "model_name", "?")
    keys = {
        "DASHSCOPE_API_KEY": bool(os.getenv("DASHSCOPE_API_KEY")),
        "DIMOS_VLM_API_KEY": bool(os.getenv("DIMOS_VLM_API_KEY")),
        "OPENAI_API_KEY": bool(os.getenv("OPENAI_API_KEY")),
        "ALIBABA_API_KEY": bool(os.getenv("ALIBABA_API_KEY")),
    }
    provider = os.getenv("DIMOS_VLM_PROVIDER", "auto")
    logger.info(
        "[VLM] backend=%s model_name=%s provider_env=%s keys_set=%s",
        model_cls,
        model_name,
        provider or "auto",
        keys,
    )


class NavigationSkillContainer(Module):
    """导航技能容器 - 将导航/感知/记忆能力封装为 LLM 可调用的 skill.

    该模块订阅相机图像和里程计数据, 提供位置标记, 物体搜索, 房间扫描等 skill.
    通过 Spec 协议注入空间记忆, 地标记忆, 重定位, 导航, 物体跟踪等依赖模块.
    """

    # 最新一帧相机图像 (由 _on_color_image 回调持续更新)
    _latest_image: Image | None = None
    # 最新里程计位姿 (由 _on_odom 回调持续更新)
    _latest_odom: PoseStamped | None = None
    _skill_started: bool = False
    # CLIP 语义相似度阈值, 低于此值认为不匹配
    _similarity_threshold: float = 0.23
    # 视觉重定位与里程计之间的漂移阈值 (基于房间参考图像)
    # 软漂移: 触发重定位校正但仍可信; 硬漂移: 里程计不可信, 必须重定位
    _drift_soft_m: float = 0.3
    _drift_hard_m: float = 1.0
    _relocalize_interval_s: float = 3.0
    _room_visual_max_distance: float = 0.35

    # 以下属性通过 blueprint 的 Spec 注入, 在 build 时自动绑定匹配的模块
    _spatial_memory: SpatialMemorySpec
    _landmark_memory: SpatialLandmarkMemorySpec
    _relocalization: RelocalizationStateSpec | None = None
    _navigation: NavigationInterfaceSpec
    _object_tracking: ObjectTrackingSpec | None = None
    _unitree_skill_container: UnitreeSkillContainer | None = None
    _frontier_explorer: WavefrontFrontierExplorer | None = None
    _memory_session_id: str = ""

    # 输入流: 相机图像和里程计, 由 blueprint 自动连接同名同类型的输出流
    color_image: In[Image]
    odom: In[PoseStamped]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._skill_started = False
        self._latest_image = None
        self._latest_odom = None
        # 用时间戳生成唯一 session ID, 用于关联同一次运行中的所有空间记录
        self._memory_session_id = f"session_{int(time.time())}"
        # room_sweep 时跳过的房间集合, 避免重复扫描已确认无目标物体的房间
        self._sweep_skip_rooms: set[str] = set()
        self._sensor_lock = threading.Lock()
        self._odom_history: deque[PoseStamped] = deque(maxlen=512)
        self._active_object_search: _ObjectSearchContext | None = None

        # 在初始化时创建 VLM 实例, 而非延迟到首次使用, 以便尽早发现配置错误
        self._vl_model = _create_vl_model()
        _log_vlm_runtime_config(self._vl_model)

    @rpc
    def start(self) -> None:
        """启动模块, 订阅相机图像和里程计数据流."""
        super().start()
        # 订阅输入流并注册为 disposable, 确保 stop 时自动取消订阅
        self.register_disposable(Disposable(self.color_image.subscribe(self._on_color_image)))
        self.register_disposable(Disposable(self.odom.subscribe(self._on_odom)))
        self._skill_started = True

    @rpc
    def stop(self) -> None:
        """停止模块, 由父类负责清理 disposable."""
        self._cancel_active_object_search()
        super().stop()

    def _on_color_image(self, image: Image) -> None:
        """相机图像回调 - 缓存最新帧, VLM 提交前再复制冻结."""
        self._ensure_search_runtime()
        with self._sensor_lock:
            self._latest_image = image

    def _odom_euler_tuple(self) -> tuple[float, float, float] | None:
        """将最新里程计的四元数姿态转换为欧拉角元组 (roll, pitch, yaw)."""
        if self._latest_odom is None:
            return None
        euler = self._latest_odom.orientation.to_euler()
        return (float(euler.x), float(euler.y), float(euler.z))

    def _on_odom(self, odom: PoseStamped) -> None:
        """里程计回调 - 缓存最新位姿并保留覆盖 VLM 延迟的短时历史."""
        self._ensure_search_runtime()
        with self._sensor_lock:
            self._latest_odom = odom
            self._odom_history.append(odom)
            cutoff = float(odom.ts) - _search_float_env(
                "DIMOS_SEARCH_ODOM_BUFFER_S", 15.0, minimum=1.0
            )
            while self._odom_history and self._odom_history[0].ts < cutoff:
                self._odom_history.popleft()

    @staticmethod
    def _pose_tuple_to_matrix(
        position: tuple[float, float, float],
        rotation: tuple[float, float, float],
    ) -> np.ndarray:
        """将 (位置, 欧拉角) 元组转换为 4x4 齐次变换矩阵.

        用于坐标变换: 将离散的位姿表示统一为矩阵形式, 便于矩阵乘法运算.
        """
        matrix = np.eye(4)
        matrix[:3, :3] = Quaternion.from_euler(Vector3(*rotation)).to_rotation_matrix()
        matrix[:3, 3] = np.asarray(position, dtype=float)
        return matrix

    @staticmethod
    def _matrix_to_pose_tuple(
        matrix: np.ndarray,
    ) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        """将 4x4 齐次变换矩阵转换为 (位置, 欧拉角) 元组.

        _pose_tuple_to_matrix 的逆操作.
        """
        position = (
            float(matrix[0, 3]),
            float(matrix[1, 3]),
            float(matrix[2, 3]),
        )
        euler = Quaternion.from_rotation_matrix(matrix[:3, :3]).to_euler()
        rotation = (float(euler.x), float(euler.y), float(euler.z))
        return position, rotation

    def _relocalization_context(self) -> tuple[str, str | None, Any] | None:
        """获取当前重定位上下文 (map_key, map_file, world_to_map 变换).

        如果重定位模块未注入或尚未完成重定位, 返回 None.
        所有依赖重定位的坐标变换都应先检查此方法的返回值.
        """
        relocalization = self._relocalization
        if relocalization is None:
            return None
        try:
            if not relocalization.is_relocalized():
                return None
            map_key = relocalization.get_current_map_key()
            world_to_map = relocalization.get_world_to_map()
            if not map_key or world_to_map is None:
                return None
            return (map_key, relocalization.get_current_map_file(), world_to_map)
        except Exception:
            # 重定位模块可能在运行中状态异常, 静默降级而非中断流程
            logger.debug("Relocalization context unavailable", exc_info=True)
            return None

    def _map_metadata_for_pose(
        self,
        position: tuple[float, float, float],
        rotation: tuple[float, float, float],
        *,
        observation_source: str,
    ) -> dict[str, Any]:
        """为给定世界坐标系下的位姿生成包含 map 坐标系变换的元数据.

        如果重定位可用, 将世界坐标转换为 map 坐标并记录变换矩阵;
        否则仅标记为未绑定重定位. 元数据会存入 SpatialRecord, 供后续跨 session 恢复.
        """
        ctx = self._relocalization_context()
        if ctx is None:
            return {
                "relocalization_bound": False,
                "observation_source": observation_source,
            }

        map_key, map_file, world_to_map = ctx
        T_world_map = world_to_map.to_matrix()
        T_world_pose = self._pose_tuple_to_matrix(position, rotation)
        # world -> map: 用 world_to_map 的逆矩阵将世界坐标位姿转到 map 坐标系
        T_map_pose = np.linalg.inv(T_world_map) @ T_world_pose
        pose_map_position, pose_map_rotation = self._matrix_to_pose_tuple(T_map_pose)
        return {
            "relocalization_bound": True,
            "map_key": map_key,
            "map_file": map_file,
            "frame": "map",
            "pose_map": {
                "position": list(pose_map_position),
                "rotation": list(pose_map_rotation),
            },
            "pose_world_observed": {
                "position": list(position),
                "rotation": list(rotation),
            },
            "T_world_map_at_observation": T_world_map.tolist(),
            "observation_source": observation_source,
        }

    def _record_for_current_world(self, record: SpatialRecord) -> SpatialRecord | None:
        """将存储在 map 坐标系中的记录转换到当前世界坐标系.

        如果记录的 map_key 与当前 map 不匹配则跳过 (返回 None);
        如果记录无 map 坐标或重定位不可用, 原样返回.
        转换后的记录会附带 pose_world_current 字段, 标记来源为重定位.
        """
        ctx = self._relocalization_context()
        if ctx is None:
            return record

        map_key, _map_file, world_to_map = ctx
        metadata = record.metadata or {}
        record_map_key = metadata.get("map_key")
        # 跨地图的记录不可用 - 不同 map 的坐标系不互通
        if record_map_key and record_map_key != map_key:
            logger.info(
                "Skipping record '%s': map_key=%r does not match current map_key=%r",
                record.name,
                record_map_key,
                map_key,
            )
            return None

        pose_map = metadata.get("pose_map")
        # 无 map 坐标的旧记录无法转换, 原样返回
        if not isinstance(pose_map, dict):
            return record
        raw_position = pose_map.get("position")
        raw_rotation = pose_map.get("rotation")
        if not isinstance(raw_position, (list, tuple)) or not isinstance(
            raw_rotation, (list, tuple)
        ):
            return record
        if len(raw_position) != 3 or len(raw_rotation) != 3:
            return record

        try:
            position_map = (
                float(raw_position[0]),
                float(raw_position[1]),
                float(raw_position[2]),
            )
            rotation_map = (
                float(raw_rotation[0]),
                float(raw_rotation[1]),
                float(raw_rotation[2]),
            )
            # map -> world: 用 world_to_map 矩阵将 map 坐标位姿转到当前世界坐标系
            T_world_pose = world_to_map.to_matrix() @ self._pose_tuple_to_matrix(
                position_map,
                rotation_map,
            )
            position_world, rotation_world = self._matrix_to_pose_tuple(T_world_pose)
        except Exception:
            logger.warning(
                "Failed to transform record '%s' from map pose", record.name, exc_info=True
            )
            return record

        new_metadata = dict(metadata)
        new_metadata["pose_world_current"] = {
            "position": list(position_world),
            "rotation": list(rotation_world),
        }
        new_metadata["pose_world_current_source"] = "relocalization"
        return replace(
            record,
            position=position_world,
            rotation=rotation_world,
            metadata=new_metadata,
            session_id=self._memory_session_id,
        )

    def _records_for_current_world(self, records: list[SpatialRecord]) -> list[SpatialRecord]:
        """批量将 map 坐标系记录转换到当前世界坐标系, 过滤掉不匹配的记录."""
        resolved: list[SpatialRecord] = []
        for rec in records:
            current = self._record_for_current_world(rec)
            if current is not None:
                resolved.append(current)
        return resolved

    def _ensure_search_runtime(self) -> None:
        """Initialize search-only state for normal construction and lightweight tests."""
        if "_sensor_lock" not in self.__dict__:
            self._sensor_lock = threading.Lock()
        if "_odom_history" not in self.__dict__:
            self._odom_history = deque(maxlen=512)
        if "_active_object_search" not in self.__dict__:
            self._active_object_search = None

    @staticmethod
    def _copy_pose(pose: PoseStamped) -> PoseStamped:
        return PoseStamped(
            ts=float(pose.ts),
            frame_id=pose.frame_id,
            position=make_vector3(
                float(pose.position.x),
                float(pose.position.y),
                float(pose.position.z),
            ),
            orientation=Quaternion(
                float(pose.orientation.x),
                float(pose.orientation.y),
                float(pose.orientation.z),
                float(pose.orientation.w),
            ),
        )

    def _new_object_search_context(self, query: str) -> _ObjectSearchContext:
        self._ensure_search_runtime()
        context = _ObjectSearchContext(
            search_id=f"search_{uuid.uuid4().hex[:10]}",
            query=query.strip(),
        )
        self._active_object_search = context
        logger.info(
            "[search:%s] started query=%r; current-position target detection disabled",
            context.search_id,
            context.query,
        )
        return context

    def _cancel_active_object_search(self) -> None:
        self._ensure_search_runtime()
        context = self._active_object_search
        if context is None:
            return
        context.cancel_event.set()
        with context.lock:
            context.monitor_enabled = False
        if self._active_object_search is context:
            self._active_object_search = None

    def _begin_search_leg(
        self,
        context: _ObjectSearchContext | None,
        target_name: str,
    ) -> None:
        if context is None or context.cancel_event.is_set():
            return
        origin: tuple[float, float] | None = None
        if self._latest_odom is not None:
            origin = (
                float(self._latest_odom.position.x),
                float(self._latest_odom.position.y),
            )
        with context.lock:
            context.active_leg_id += 1
            context.leg_origin = origin
            context.monitor_enabled = True
            context.last_request_at = 0.0
            leg_id = context.active_leg_id
        logger.info(
            "[search:%s] leg=%d navigation started target=%r origin=%s",
            context.search_id,
            leg_id,
            target_name,
            origin,
        )

    @staticmethod
    def _end_search_leg(context: _ObjectSearchContext | None) -> None:
        if context is None:
            return
        with context.lock:
            context.monitor_enabled = False

    def _build_search_snapshot(
        self,
        context: _ObjectSearchContext,
        leg_id: int,
    ) -> _SearchFrameSnapshot | None:
        self._ensure_search_runtime()
        with self._sensor_lock:
            image = self._latest_image
            if image is None or not hasattr(image, "data"):
                return None
            image_copy = image.copy()
            candidates = list(self._odom_history)
            if not candidates and self._latest_odom is not None:
                candidates = [self._latest_odom]

        if not candidates:
            return None
        nearest = min(candidates, key=lambda pose: abs(float(pose.ts) - float(image_copy.ts)))
        sync_error = abs(float(nearest.ts) - float(image_copy.ts))
        tolerance = _search_float_env("DIMOS_SEARCH_POSE_SYNC_TOLERANCE_S", 0.20, minimum=0.01)
        if sync_error > tolerance:
            logger.debug(
                "[search:%s] frame skipped: image/odom skew %.3fs > %.3fs",
                context.search_id,
                sync_error,
                tolerance,
            )
            return None

        capture_pose = self._copy_pose(nearest)
        capture_euler = capture_pose.orientation.to_euler()
        position = (
            float(capture_pose.position.x),
            float(capture_pose.position.y),
            float(capture_pose.position.z),
        )
        rotation = (
            float(capture_euler.x),
            float(capture_euler.y),
            float(capture_euler.z),
        )
        return _SearchFrameSnapshot(
            search_id=context.search_id,
            leg_id=leg_id,
            image=image_copy,
            image_ts=float(image_copy.ts),
            capture_pose_world=capture_pose,
            map_metadata=self._map_metadata_for_pose(
                position,
                rotation,
                observation_source="enroute_vlm_capture",
            ),
            submitted_at=time.time(),
        )

    @staticmethod
    def _object_yaws_from_bbox(
        snapshot: _SearchFrameSnapshot,
        bbox: BBox,
    ) -> tuple[float, float | None]:
        _height, width = snapshot.image.data.shape[:2]
        x1, y1, x2, y2 = (float(v) for v in bbox)
        offset = yaw_offset_from_bbox(
            x1 / width * 1000.0,
            y1,
            x2 / width * 1000.0,
            y2,
            _CAMERA_HFOV_DEG,
        )
        capture_yaw = float(snapshot.capture_pose_world.orientation.to_euler().z)
        object_yaw_world = math.atan2(
            math.sin(capture_yaw - offset),
            math.cos(capture_yaw - offset),
        )

        object_yaw_map: float | None = None
        pose_map = snapshot.map_metadata.get("pose_map")
        if isinstance(pose_map, dict):
            raw_rotation = pose_map.get("rotation")
            if isinstance(raw_rotation, (list, tuple)) and len(raw_rotation) == 3:
                map_capture_yaw = float(raw_rotation[2])
                object_yaw_map = math.atan2(
                    math.sin(map_capture_yaw - offset),
                    math.cos(map_capture_yaw - offset),
                )
        return object_yaw_world, object_yaw_map

    def _run_enroute_vlm(
        self,
        context: _ObjectSearchContext,
        snapshot: _SearchFrameSnapshot,
    ) -> None:
        bbox: BBox | None = None
        try:
            bbox = get_object_bbox_from_image(self._vl_model, snapshot.image, context.query)
        except Exception:
            logger.exception(
                "[search:%s] VLM request failed leg=%d",
                context.search_id,
                snapshot.leg_id,
            )

        detected_at = time.time()
        result_age = detected_at - snapshot.image_ts
        hit: _SearchHit | None = None
        if bbox is not None:
            object_yaw_world, object_yaw_map = self._object_yaws_from_bbox(snapshot, bbox)
            hit = _SearchHit(
                snapshot=snapshot,
                bbox=bbox,
                detected_at=detected_at,
                object_yaw_world=object_yaw_world,
                object_yaw_map=object_yaw_map,
            )

        with context.lock:
            context.vlm_in_flight = False
            valid = (
                hit is not None
                and not context.cancel_event.is_set()
                and context.hit is None
                and snapshot.search_id == context.search_id
                and snapshot.leg_id == context.active_leg_id
                and result_age
                <= _search_float_env("DIMOS_SEARCH_MAX_RESULT_AGE_S", 8.0, minimum=0.5)
            )
            if valid:
                context.hit = hit
                context.monitor_enabled = False
                context.hit_event.set()

        logger.info(
            "[search:%s] VLM result leg=%d hit=%s latency=%.2fs age=%.2fs valid=%s",
            context.search_id,
            snapshot.leg_id,
            bbox is not None,
            detected_at - snapshot.submitted_at,
            result_age,
            valid,
        )

    def _maybe_submit_enroute_vlm(self, context: _ObjectSearchContext | None) -> None:
        if context is None or context.cancel_event.is_set() or context.hit_event.is_set():
            return
        now = time.time()
        with context.lock:
            if not context.monitor_enabled or context.vlm_in_flight:
                return
            if now - context.last_request_at < _search_float_env(
                "DIMOS_SEARCH_VLM_INTERVAL_S", 0.8, minimum=0.1
            ):
                return
            leg_id = context.active_leg_id
            origin = context.leg_origin

        if origin is None or self._latest_odom is None:
            return
        displacement = math.hypot(
            float(self._latest_odom.position.x) - origin[0],
            float(self._latest_odom.position.y) - origin[1],
        )
        start_displacement = _search_float_env(
            "DIMOS_SEARCH_START_DISPLACEMENT_M", 0.20, minimum=0.0
        )
        if displacement < start_displacement:
            return

        snapshot = self._build_search_snapshot(context, leg_id)
        if snapshot is None:
            return
        if now - snapshot.image_ts > _search_float_env(
            "DIMOS_SEARCH_MAX_RESULT_AGE_S", 8.0, minimum=0.5
        ):
            return

        with context.lock:
            if (
                not context.monitor_enabled
                or context.vlm_in_flight
                or context.active_leg_id != leg_id
            ):
                return
            context.vlm_in_flight = True
            context.last_request_at = now

        logger.info(
            "[search:%s] VLM request leg=%d image_ts=%.3f capture=(%.2f, %.2f)",
            context.search_id,
            leg_id,
            snapshot.image_ts,
            snapshot.capture_pose_world.position.x,
            snapshot.capture_pose_world.position.y,
        )
        threading.Thread(
            target=self._run_enroute_vlm,
            args=(context, snapshot),
            name=f"enroute-vlm-{context.search_id}",
            daemon=True,
        ).start()

    def _poll_enroute_search(self, context: _ObjectSearchContext | None) -> None:
        if context is None:
            return
        if context.hit_event.is_set():
            raise _EnrouteObjectHitError
        self._maybe_submit_enroute_vlm(context)
        if context.hit_event.is_set():
            raise _EnrouteObjectHitError

    def _capture_pose_for_hit(self, hit: _SearchHit) -> PoseStamped | None:
        metadata = hit.snapshot.map_metadata
        pose_map = metadata.get("pose_map")
        if metadata.get("relocalization_bound") and isinstance(pose_map, dict):
            raw_position = pose_map.get("position")
            raw_rotation = pose_map.get("rotation")
            if not isinstance(raw_position, (list, tuple)) or not isinstance(
                raw_rotation, (list, tuple)
            ):
                return None
            if len(raw_position) != 3 or len(raw_rotation) != 3:
                return None
            position_map = (
                float(raw_position[0]),
                float(raw_position[1]),
                float(raw_position[2]),
            )
            rotation_map = [
                float(raw_rotation[0]),
                float(raw_rotation[1]),
                float(raw_rotation[2]),
            ]
            if hit.object_yaw_map is not None:
                rotation_map[2] = hit.object_yaw_map
            capture_record = SpatialRecord(
                name="enroute_capture_pose",
                record_type=RecordType.UNKNOWN,
                position=position_map,
                rotation=(rotation_map[0], rotation_map[1], rotation_map[2]),
                metadata={
                    "map_key": metadata.get("map_key"),
                    "pose_map": {
                        "position": list(raw_position),
                        "rotation": rotation_map,
                    },
                },
                session_id=self._memory_session_id,
            )
            current = self._record_for_current_world(capture_record)
            if current is None:
                return None
            return PoseStamped(
                position=make_vector3(*current.position),
                orientation=Quaternion.from_euler(Vector3(*current.rotation)),
                frame_id="map",
            )

        capture = hit.snapshot.capture_pose_world
        return PoseStamped(
            position=make_vector3(
                float(capture.position.x),
                float(capture.position.y),
                float(capture.position.z),
            ),
            orientation=Quaternion.from_euler(Vector3(0.0, 0.0, hit.object_yaw_world)),
            frame_id="map",
        )

    def _store_enroute_search_hit(self, context: _ObjectSearchContext, hit: _SearchHit) -> str:
        capture_pose = self._capture_pose_for_hit(hit)
        if capture_pose is None:
            return ""
        euler = capture_pose.orientation.to_euler()
        position = (
            float(capture_pose.position.x),
            float(capture_pose.position.y),
            float(capture_pose.position.z),
        )
        rotation = (float(euler.x), float(euler.y), float(euler.z))
        metadata = dict(hit.snapshot.map_metadata)
        pose_map = metadata.get("pose_map")
        if isinstance(pose_map, dict) and hit.object_yaw_map is not None:
            metadata["pose_map"] = dict(pose_map)
            raw_rotation = list(metadata["pose_map"].get("rotation", [0.0, 0.0, 0.0]))
            if len(raw_rotation) == 3:
                raw_rotation[2] = hit.object_yaw_map
                metadata["pose_map"]["rotation"] = raw_rotation
        metadata.update(
            {
                "observation_source": "enroute_vlm",
                "position_semantics": "observation_viewpoint",
                "search_id": context.search_id,
                "image_ts": hit.snapshot.image_ts,
                "vlm_result_ts": hit.detected_at,
                "vlm_latency_s": hit.detected_at - hit.snapshot.submitted_at,
                "bbox": [float(v) for v in hit.bbox],
            }
        )
        room_name = self._room_name_at_position(position[0], position[1])
        if room_name:
            metadata["room_name"] = room_name
        record = SpatialRecord(
            name=context.query,
            record_type=RecordType.LANDMARK,
            position=position,
            rotation=rotation,
            state="enroute VLM observation",
            confidence=1.0,
            metadata=metadata,
            session_id=self._memory_session_id,
        )
        return self._landmark_memory.record(record)

    def _finish_enroute_hit(self, context: _ObjectSearchContext) -> str:
        with context.lock:
            if context.terminal_result is not None:
                return context.terminal_result
            hit = context.hit
            context.monitor_enabled = False
        context.cancel_event.set()
        if hit is None:
            result = f"Search for '{context.query}' was interrupted without a valid VLM hit."
            context.terminal_result = result
            return result

        self._navigation.cancel_goal()
        cancel_deadline = time.time() + _search_float_env(
            "DIMOS_SEARCH_CANCEL_WAIT_S", 2.0, minimum=0.0
        )
        while (
            time.time() < cancel_deadline and self._navigation.get_state() != NavigationState.IDLE
        ):
            time.sleep(0.05)
        if self._navigation.get_state() != NavigationState.IDLE:
            result = (
                f"Recognized '{context.query}' en route, but the previous navigation goal "
                "did not release control after cancellation."
            )
            context.terminal_result = result
            return result

        capture_pose = self._capture_pose_for_hit(hit)
        if capture_pose is None:
            result = (
                f"Recognized '{context.query}' en route, but its capture pose does not belong "
                "to the current relocalized map."
            )
            context.terminal_result = result
            return result

        rewind_distance = self._planar_distance_to_pose(capture_pose)
        rewind_threshold = _search_float_env("DIMOS_SEARCH_REWIND_THRESHOLD_M", 0.40, minimum=0.05)
        logger.info(
            "[search:%s] hit accepted capture=(%.2f, %.2f) current_distance=%.2fm bbox=%s",
            context.search_id,
            capture_pose.position.x,
            capture_pose.position.y,
            rewind_distance,
            hit.bbox,
        )
        if rewind_distance > rewind_threshold:
            if not self._navigation.set_goal(capture_pose):
                result = (
                    f"Recognized '{context.query}' en route, but navigation rejected the "
                    "return-to-capture goal."
                )
                context.terminal_result = result
                return result
            rewind_deadline = time.time() + _search_float_env(
                "DIMOS_SEARCH_REWIND_TIMEOUT_S", 120.0, minimum=1.0
            )
            while time.time() < rewind_deadline:
                if self._navigation.is_goal_reached():
                    break
                if self._planar_distance_to_pose(capture_pose) <= rewind_threshold:
                    break
                time.sleep(0.2)
            else:
                self._navigation.cancel_goal()
                result = (
                    f"Recognized '{context.query}' en route, but failed to return to the "
                    f"capture viewpoint (distance was {rewind_distance:.2f}m)."
                )
                context.terminal_result = result
                return result

        self._navigation.cancel_goal()
        target_yaw = float(capture_pose.orientation.to_euler().z)
        current_euler = self._odom_euler_tuple()
        if current_euler is not None:
            yaw_delta = math.atan2(
                math.sin(target_yaw - current_euler[2]),
                math.cos(target_yaw - current_euler[2]),
            )
            if abs(math.degrees(yaw_delta)) > 3.0:
                self._rotate_in_place_degrees(math.degrees(yaw_delta))

        record_id = ""
        try:
            record_id = self._store_enroute_search_hit(context, hit)
        except Exception:
            logger.exception(
                "[search:%s] failed to update landmark memory after en-route hit",
                context.search_id,
            )
        action_result = self._run_arrival_action("point", context.query)
        result = (
            f"Found '{context.query}' en route, returned to the capture viewpoint, and faced "
            f"the object. {action_result}"
        )
        context.terminal_result = result
        logger.info(
            "[search:%s] finished FOUND rewind_distance=%.2fm record_id=%s",
            context.search_id,
            rewind_distance,
            record_id or "not-stored",
        )
        return result

    @skill
    def tag_location(self, location_name: str, num_photos: int = -1) -> str:
        """标记当前位置并存储参考图像, 供后续导航和视觉重定位使用.

        默认行为: 全景拍摄 - 机器人在原地旋转, 每旋转一步拍一张照片
        (默认 90 度一步, 旋转 3 次约覆盖 360 度). 每帧同时用 VLM 检测物体.

        传入 num_photos=0: 仅在当前朝向拍一张.
        传入 num_photos>=2: 手动指定全景拍摄步数.

        每张照片会作为房间参考图像存入空间记忆, 用于视觉重定位.

        Args:
            location_name (str): 位置名称 (如 "办公室", "厨房").
            num_photos (int): 全景照片数 (-1=自动, 0=单张, 2~12=手动).

        Returns:
            str: 标记结果摘要
        """

        if not self._skill_started:
            raise ValueError(f"{self} has not been started.")

        if not self._latest_odom:
            return "No odometry data received yet, cannot tag location."

        # num_photos < 0 表示自动全景模式; 手动模式下限制在 0-12 张
        auto_panorama = num_photos < 0
        if not auto_panorama:
            num_photos = max(0, min(num_photos, 12))
        # 缓存每次拍摄的帧数据: (图像 ndarray, 位置, 朝向), 供后续 VLM 异步检测使用
        captured_frames: list[
            tuple[Any, tuple[float, float, float], tuple[float, float, float]]
        ] = []

        has_cam = self._latest_image is not None and hasattr(self._latest_image, "data")
        cam_shape = getattr(self._latest_image.data, "shape", None) if has_cam else None  # type: ignore[union-attr]
        logger.info(
            "[tag_location] START name=%r auto_panorama=%s num_photos=%d has_camera=%s image_shape=%s",
            location_name,
            auto_panorama,
            num_photos,
            has_cam,
            cam_shape,
        )

        def _snap_one(name: str) -> bool:
            """拍摄一张照片: 存入 CLIP 空间记忆, 并启动异步 VLM 检测线程."""
            pos = self._latest_odom.position if self._latest_odom else None
            rot_tuple = self._odom_euler_tuple()
            if pos is None or rot_tuple is None:
                return False

            location = RobotLocation(
                name=name,
                position=(pos.x, pos.y, pos.z),
                rotation=rot_tuple,
            )
            # 先记录位置到空间记忆 (不含图像), 确保 CLIP 向量库和坐标库都有记录
            self._spatial_memory.tag_location(location)

            image_saved = False
            latest_image = self._latest_image
            if latest_image is not None and hasattr(latest_image, "data"):
                # 复制图像数据, 避免异步线程读取时被后续帧覆盖
                img_copy = latest_image.data.copy()
                pos_tuple = (float(pos.x), float(pos.y), float(pos.z))
                captured_frames.append(
                    (
                        img_copy.copy(),
                        (float(pos.x), float(pos.y), float(pos.z)),
                        rot_tuple,
                    )
                )
                try:
                    # 将图像存入 CLIP 向量库作为房间参考图像, 用于后续视觉重定位
                    image_saved = self._spatial_memory.tag_location_with_image(
                        location,
                        img_copy,
                    )
                except Exception:
                    logger.exception("Failed to store room reference image for '%s'", name)

                # 逐帧异步 VLM: 立即启动而非等所有帧拍完, 减少 VLM 检测延迟
                frame_idx = len(captured_frames) - 1
                threading.Thread(
                    target=self._detect_single_frame_async,
                    args=(img_copy, pos_tuple, rot_tuple, name),
                    daemon=True,
                    name=f"vlm-frame-{name}-{frame_idx}",
                ).start()

            logger.info(
                "[tag_location] snap name=%r image_saved=%s captured_total=%d",
                name,
                image_saved,
                len(captured_frames),
            )
            return image_saved

        # 先在初始朝向拍一张
        image_saved = _snap_one(location_name)
        position = self._latest_odom.position
        rot_tuple = self._odom_euler_tuple() or (0.0, 0.0, 0.0)
        logger.info(f"Tagged location '{location_name}' at ({position.x:.2f},{position.y:.2f})")

        # 每个位置名只创建一个 ROOM 地标 (record() 按名称合并; 拍照只追加 CLIP 图像)
        room_rec = SpatialRecord(
            name=location_name,
            record_type=RecordType.ROOM,
            position=(float(position.x), float(position.y), float(position.z)),
            rotation=rot_tuple,
            metadata=self._map_metadata_for_pose(
                (float(position.x), float(position.y), float(position.z)),
                rot_tuple,
                observation_source="tag_room",
            ),
            session_id=self._memory_session_id,
        )
        # 如果图像保存成功, 额外保存一张 JPEG 快照到地标记忆, 供 UI 展示和快速检索
        if image_saved and self._latest_image is not None and hasattr(self._latest_image, "data"):
            try:
                import cv2

                _, jpg = cv2.imencode(".jpg", self._latest_image.data)
                img_path = self._landmark_memory.save_snapshot(room_rec.record_id, jpg.tobytes())
                if img_path:
                    room_rec.image_snapshot_path = img_path
            except Exception:
                pass
        self._landmark_memory.record(room_rec)
        logger.info("[tag_location] room landmark saved for '%s'", location_name)

        # 全景模式或手动指定 >=2 张时, 旋转机器人拍摄后续照片
        if auto_panorama or num_photos >= 2:
            angle_step = _rotation_step_deg()
            n_rotations = _panorama_rotations() if auto_panorama else (num_photos - 1)
            done = 1
            for _i in range(n_rotations):
                ok = self._rotate_in_place_degrees(angle_step)
                if not ok:
                    break
                time.sleep(0.3)  # 等待相机画面稳定后再拍摄
                _snap_one(location_name)
                done += 1
            logger.info(
                "Panorama: %d photo(s) for '%s' (%.0f° × %d rotations)",
                done,
                location_name,
                angle_step,
                n_rotations,
            )

        if captured_frames:
            logger.info(
                "[tag_location] per-frame VLM threads already spawned: room=%r frames=%d",
                location_name,
                len(captured_frames),
            )
        else:
            # 没有捕获到任何帧: 可能是相机流未连接或仿真相机未启动
            logger.warning(
                "[tag_location] VLM SKIPPED for %r: no camera frames captured "
                "(check color_image stream / simulation camera)",
                location_name,
            )

        # 构建返回给 LLM 的结果摘要
        extra = ""
        if auto_panorama or num_photos >= 2:
            extra = f", {len(captured_frames)} panoramic photos ({_rotation_step_deg():.0f}° step)"
        suffix = ""
        if captured_frames:
            suffix += f", VLM detection on {len(captured_frames)} frame(s) in background"
            if not image_saved:
                suffix += " (CLIP room images failed — check Chroma/CLIP logs)"
            else:
                suffix += " (with CLIP room images)"
        return f"Tagged '{location_name}': ({position.x:.2f},{position.y:.2f}){extra}{suffix}."

    def _rotate_in_place_degrees(self, degrees: float) -> bool:
        """原地旋转指定角度, 成功返回 True.

        采用多层 fallback 适配不同机器人硬件和旋转模式:
        开环定时旋转 -> TF 闭环旋转 -> Go2 路径规划旋转 -> G1 定时角速度.
        这种逐级降级的设计保证了在缺少某些硬件接口时仍能完成旋转.
        """
        us = self._unitree_skill_container
        if us is None:
            return False

        # 开环定时旋转: 仅在 TF 里程计不可靠时通过 DIMOS_ROTATE_TIMED_FACTOR 启用.
        # 适用于 TF 数据缺失或漂移严重的场景, 牺牲精度换取可用性.
        if os.getenv("DIMOS_ROTATE_TIMED_FACTOR") and hasattr(us, "rotate_in_place_timed"):
            try:
                result = us.rotate_in_place_timed(degrees)
                if isinstance(result, bool):
                    return result
                return bool(result)
            except Exception:
                logger.exception("rotate_in_place_timed failed, falling back to TF-based")

        # TF 闭环旋转: 通过实时 TF 反馈控制旋转角度, 精度优于开环.
        # 已移除 max_delta 限制, 允许单次大角度旋转.
        if hasattr(us, "rotate_in_place_degrees"):
            try:
                result = us.rotate_in_place_degrees(degrees)
                if isinstance(result, bool):
                    return result
                return bool(result)
            except Exception:
                logger.exception("rotate_in_place_degrees failed")
                return False

        # Go2 fallback: 通过路径规划实现相对移动旋转, 前进量为 0 即纯旋转.
        if hasattr(us, "relative_move"):
            try:
                msg = us.relative_move(forward=0.0, left=0.0, degrees=degrees)
                if isinstance(msg, str):
                    return "goal reached" in msg.lower()
                return bool(msg)
            except Exception:
                logger.exception("relative_move rotation failed")
                return False
        # G1 fallback: 定时角速度旋转. G1 的 move 接口 yaw 参数单位是 rad/s 而非度数,
        # 需要将角度转换为角速度并估算旋转时长. 限制最大角速度为 90 deg/s 防止过快.
        if hasattr(us, "move"):
            try:
                duration = max(1.0, abs(degrees) / 45.0)
                yaw_rate = math.copysign(math.radians(min(90.0, abs(degrees))), degrees)
                us.move(x=0.0, y=0.0, yaw=yaw_rate, duration=duration)
                return True
            except Exception:
                logger.exception("move rotation failed")
                return False
        return False

    @skill
    def tag_room(self, name: str, num_photos: int = 0) -> str:
        """用 360 度全景拍照标记当前房间.

        默认每步旋转 100 度 (DIMOS_ROTATION_STEP_DEG), 步数足够覆盖约 360 度,
        每帧拍摄后执行 VLM 目标检测.

        这是 tag_location() 的便捷封装. 传 num_photos=0 (默认) 自动计算步数;
        传 num_photos>=2 则手动指定拍照数量 (仍使用固定步距, 非 360/N).
        """
        if num_photos <= 0:
            return self.tag_location(name, num_photos=-1)
        return self.tag_location(name, num_photos=max(2, min(num_photos, 12)))

    def _nav_fallback_strategy(self) -> str:
        """读取环境变量 DIMOS_NAV_FALLBACK 获取导航 fallback 策略名.

        支持三种策略: object_room (默认, 轻量), semantic (完整 6 层链), room_first.
        未知值回退到 object_room 并告警.
        """
        import os

        name = os.getenv("DIMOS_NAV_FALLBACK", "object_room").lower().strip()
        if name not in _NAV_FALLBACK_STRATEGIES:
            logger.warning("Unknown DIMOS_NAV_FALLBACK=%r, using 'object_room'", name)
            return "object_room"
        return name

    def _nav_fallback_step_tagged(self, query: str) -> str | None:
        """fallback 步骤: 在 CLIP 标记过的位置中查找目标.

        通过语义匹配查询之前 tag_location/tag_room 记录的位置,
        找到则导航过去. 这是成本最低的查找方式, 但 CLIP 语义匹配可能有误报.
        """
        logger.info("[tagged] Checking tagged locations for %r ...", query)
        msg = self._navigate_by_tagged_location(query)
        if msg:
            logger.info("[tagged] ✓ HIT: %s", msg)
        else:
            logger.info("[tagged] ✗ MISS")
        return msg

    def _nav_fallback_step_in_frame(self, query: str, *, timeout: float = 30.0) -> str | None:
        """fallback 步骤: 在当前视野中用 VLM 检测并跟踪目标.

        如果查询已被解析为房间名或已知 landmark, 则跳过此步骤:
        - 房间名应由 landmark 步骤处理导航
        - 已知坐标的 landmark 应由 landmark 步骤先导航过去
        只有当目标没有已知坐标时, 才用 VLM 实时检测 + 跟踪.
        """
        resolved = self._resolve_landmark_from_query(query)
        if resolved is not None:
            if resolved.record_type == RecordType.ROOM:
                logger.info("[in_frame] skip — %r is a room name, not an in-frame object", query)
                return None
            if resolved.record_type == RecordType.LANDMARK:
                logger.info(
                    "[in_frame] skip — %r has known landmark at (%.2f, %.2f); landmark step should navigate there first",
                    query,
                    resolved.position[0],
                    resolved.position[1],
                )
                return None
        logger.info("[in_frame] VLM bbox + tracking for %r (timeout=%.0fs) ...", query, timeout)
        msg = self._navigate_to_object(query, timeout=timeout)
        if msg:
            logger.info("[in_frame] ✓ HIT: %s", msg)
        else:
            logger.info("[in_frame] ✗ MISS")
        return msg

    def _nav_fallback_step_landmark(
        self,
        query: str,
        search_context: _ObjectSearchContext | None = None,
    ) -> str | None:
        """fallback 步骤: 通过 landmark 记忆导航到已知坐标并视觉确认.

        查询 landmark 记忆中是否有匹配的房间或物体记录, 有则导航过去.
        - 物体 landmark: 到达后执行 point 动作 (指向物体) 并停止 fallback 链
        - 房间 landmark: 到达房间后返回, 后续步骤可在此房间内搜索

        导航失败时若 landmark 关联了房间, 将该房间加入 skip 集合,
        避免 room_sweep 步骤重复搜索已知没有目标的房间.
        """
        logger.info("[landmark] Searching landmark memory for %r ...", query)
        landmark_target = self._resolve_landmark_from_query(query)
        if landmark_target is None:
            logger.info("[landmark] ✗ MISS: no landmark matching %r", query)
            return None

        logger.info(
            "[landmark] Found '%s' at (%.2f, %.2f), topology nav ...",
            landmark_target.name,
            landmark_target.position[0],
            landmark_target.position[1],
        )
        is_object_landmark = landmark_target.record_type == RecordType.LANDMARK
        # 物体 landmark 到达后指向物体; 房间 landmark 到达后停止即可.
        # arrival_distance=0.5 是到达容差, 过小会导致反复调整无法收敛.
        nav_landmark_msg = self._navigate_to_landmark(
            landmark_target,
            arrival_action="point" if is_object_landmark else "stop",
            arrival_distance=0.5,
            run_arrival_action=is_object_landmark,
            enable_visual_drift=False,
            search_context=search_context if is_object_landmark else None,
        )
        nav_lower = nav_landmark_msg.lower()
        # 检测导航失败的各种情况: 严重漂移/中止/跳过/超时/无法视觉锁定.
        # 匹配字符串而非错误码, 因为底层返回的是人类可读的消息.
        if (
            "severe visual/odom drift" in nav_lower
            or "aborted" in nav_lower
            or "navigation skipped" in nav_lower
            or "timed out" in nav_lower
            or "could not visually acquire" in nav_lower
        ):
            logger.warning("[landmark] ⚠ Navigation failed or object not found, fall through")
            if is_object_landmark:
                # 导航失败时把 landmark 所在房间加入 skip 集合,
                # 让后续 room_sweep 不再重复搜索这个已知没有目标的房间.
                meta = landmark_target.metadata or {}
                room = meta.get("room_name")
                if not room:
                    # metadata 没有记录房间名时, 用坐标反查房间.
                    room = self._room_name_at_position(
                        landmark_target.position[0], landmark_target.position[1]
                    )
                if room:
                    self._sweep_skip_rooms.add(str(room))
                    logger.info(
                        "[landmark] room %r already searched — room_sweep will skip it",
                        room,
                    )
            return None

        if landmark_target.record_type == RecordType.ROOM:
            logger.info("[landmark] ✓ Reached room '%s'", landmark_target.name)
            return nav_landmark_msg

        logger.info(
            "[landmark] ✓ Reached object landmark '%s'; stop fallback chain", landmark_target.name
        )
        return nav_landmark_msg

    def _nav_fallback_step_room_sweep(
        self,
        query: str,
        search_context: _ObjectSearchContext | None = None,
    ) -> str | None:
        """fallback 步骤: 遍历所有房间锚点搜索目标.

        依次导航到每个房间并执行 360 度扫描 (跳过 _sweep_skip_rooms 中已搜索的房间).
        找到目标后执行 point 动作指向物体, 并将扫描结果拼入返回消息.
        """
        logger.info("[room_sweep] Sweeping room anchors for %r ...", query)
        msg = self._room_anchor_sweep_for_object(query, search_context=search_context)
        if msg:
            logger.info("[room_sweep] ✓ HIT: %s", msg)
            if search_context is not None and search_context.terminal_result is not None:
                return msg
            return f"{self._run_arrival_action('point', query)} ({msg})"
        else:
            logger.info("[room_sweep] ✗ MISS")
            return None

    def _nav_fallback_step_vlm_memory(self, query: str) -> str | None:
        """fallback 步骤: 对历史存储的图像批量执行 VLM 查询.

        当实时导航和 landmark 搜索都失败时, 回顾之前拍摄的图像,
        用 VLM 识别哪张图像包含目标, 再导航到对应位置.
        """
        logger.info("[vlm_memory] Batch VLM on stored images for %r ...", query)
        msg = self._query_memory_images_with_vlm(query)
        if msg:
            logger.info("[vlm_memory] ✓ HIT: %s", msg)
        else:
            logger.info("[vlm_memory] ✗ MISS")
        return msg

    def _nav_fallback_step_clip_map(self, query: str) -> str | None:
        """fallback 步骤: 利用 CLIP 语义地图导航.

        CLIP 语义地图将空间区域与语义标签关联, 通过 CLIP 相似度
        在地图上找到与查询最匹配的区域并导航过去.
        """
        logger.info("[clip_map] CLIP semantic map for %r ...", query)
        msg = self._navigate_using_semantic_map(query)
        if msg:
            logger.info("[clip_map] ✓ HIT: %s", msg)
        else:
            logger.info("[clip_map] ✗ MISS")
        return msg

    def _run_navigate_fallback_chain(
        self,
        query: str,
        search_context: _ObjectSearchContext | None = None,
    ) -> str | None:
        """按策略定义的顺序依次执行 fallback 步骤, 首个命中即返回.

        用字典映射步骤名到 lambda, 使得不同策略只需改变步骤顺序而非重写逻辑.
        任意步骤返回非 None 消息即视为命中, 中止后续步骤.
        """
        strategy = self._nav_fallback_strategy()
        order = _NAV_FALLBACK_STRATEGIES[strategy]
        logger.info(
            "NAVIGATE_WITH_TEXT fallback strategy=%s order=%s",
            strategy,
            " → ".join(order),
        )

        steps: dict[str, Any] = {
            "tagged": lambda: self._nav_fallback_step_tagged(query),
            "in_frame": lambda: self._nav_fallback_step_in_frame(query),
            "landmark": lambda: self._nav_fallback_step_landmark(query, search_context),
            "room_sweep": lambda: self._nav_fallback_step_room_sweep(query, search_context),
            "vlm_memory": lambda: self._nav_fallback_step_vlm_memory(query),
            "clip_map": lambda: self._nav_fallback_step_clip_map(query),
        }

        for step_name in order:
            if search_context is not None and step_name == "in_frame":
                logger.info(
                    "[search:%s] skip in_frame: current-position detection is disabled",
                    search_context.search_id,
                )
                continue
            msg: str | None = steps[step_name]()
            if msg:
                return msg
        return None

    # TODO(capabilities): 这个 skill 是 instant 类型, 调用返回时 movement hold 就被释放,
    # 但 tagged-location 和 semantic-map 路径只调了 set_goal() 仍在持续导航.
    # 应改为 background 类型, 等机器人真正停止后再释放 hold
    # (planner 已发出 goal-reached 信号, 见 PatrollingModule),
    # 这样 patrol/follow/explore 就不会在一个活跃的导航目标上启动.
    @skill(uses=[CAP_MOVEMENT])
    def navigate_with_text(self, query: str) -> str:
        """用自然语言导航/找物, 内置多级 fallback 机制.

        默认策略 (DIMOS_NAV_FALLBACK=object_room): 物体 landmark -> 房间扫描.
        遍历所有房间并 360 度扫描, 找不到则结束.

        设置 DIMOS_ENROUTE_OBJECT_SEARCH_ENABLED=true 后, 才会在前往历史位置
        和其他房间的途中异步检测目标. 默认关闭时完全保留原有到点检测流程.

        其他策略: semantic (完整 6 层链) 或 room_first.

        每次只查找一个目标.
        Args:
            query: 文本查询 (物体名, 房间名, 或描述).
        """

        if not self._skill_started:
            raise ValueError(f"{self} has not been started.")

        logger.info("=" * 50)
        logger.info("NAVIGATE_WITH_TEXT START  query=%r", query)
        logger.info("=" * 50)

        self._sweep_skip_rooms = set()
        search_context: _ObjectSearchContext | None = None
        if _enroute_object_search_enabled():
            resolved = self._resolve_landmark_from_query(query)
            if resolved is None or resolved.record_type == RecordType.LANDMARK:
                search_context = self._new_object_search_context(query)
        else:
            logger.info(
                "En-route object search disabled; preserving arrival-only search for %r",
                query,
            )
        try:
            success_msg = self._run_navigate_fallback_chain(query, search_context)
        finally:
            if search_context is not None:
                search_context.cancel_event.set()
                self._end_search_leg(search_context)
                if self._active_object_search is search_context:
                    self._active_object_search = None
        if success_msg:
            logger.info("=" * 50)
            logger.info("NAVIGATE_WITH_TEXT END  query=%r  result=HIT", query)
            logger.info("=" * 50)
            return success_msg

        # 所有 fallback 步骤都未命中, 根据策略返回不同的失败描述.
        logger.info("=" * 50)
        logger.info("NAVIGATE_WITH_TEXT END  query=%r  result=ALL_MISS", query)
        logger.info("=" * 50)
        strategy = self._nav_fallback_strategy()
        if strategy == "object_room":
            return f"Could not find '{query}' (checked object landmark and swept all rooms)."
        return (
            f"Could not reach '{query}' (landmark, in-frame, room sweep, "
            f"VLM memory, CLIP map, or tagged location all missed)."
        )

    def _navigate_by_tagged_location(self, query: str) -> str | None:
        """通过 CLIP 语义检索在空间记忆中查找带标签的位置, 并导航过去.

        属于导航回退链的一环. 纯 CLIP 语义匹配过于宽松, 会产生跨语义误匹配
        (例如把"电脑屏幕"匹配到"卧室"), 因此命中后还要做一层文本重叠校验,
        只有查询词和命中名称存在字符/单词级别重叠时才真正导航.
        """
        robot_location = self._spatial_memory.query_tagged_location(query)

        if not robot_location:
            return None

        # 防御: 要求查询词和命中名称至少存在一些文本重叠.
        # 纯 CLIP 语义匹配过于宽松 (例如 "电脑屏幕" 会匹配到 "卧室").
        matched_name = robot_location.name.lower()
        query_lower = query.lower()
        if matched_name != query_lower:
            # CJK 按字符求交, 拉丁文按单词求交, 适配两种语言的分词粒度
            if matched_name.replace(" ", "").isascii() and query_lower.replace(" ", "").isascii():
                q_words = set(query_lower.split())
                m_words = set(matched_name.split())
                overlap = q_words & m_words
            else:
                overlap = set(query_lower) & set(matched_name)
            if not overlap:
                # 无任何文本重叠 -> 判定为语义误匹配, 放弃此结果让后续回退层继续尝试
                logger.warning(
                    "Tagged location '%s' matched query '%s' semantically but "
                    "has no text overlap — skipping to let fallback chain try",
                    robot_location.name,
                    query,
                )
                return None

        logger.info("Found tagged location", location=robot_location)
        goal_pose = PoseStamped(
            position=make_vector3(*robot_location.position),
            orientation=Quaternion.from_euler(Vector3(*robot_location.rotation)),
            frame_id="map",
        )

        return self._navigate_to(goal_pose, f"Found a tagged location called '{query}'.")

    def _navigate_to(self, pose: PoseStamped, message: str) -> str:
        """向导航模块下发目标 pose, 并返回提示 LLM 的状态文案.

        这是一个"发后即忘"的调用 -- set_goal 之后立即返回, 实际导航在后台异步进行.
        返回文案里提示 LLM 可以调用 stop_navigation 来取消, 因为这个 skill 标记为
        instant, 调用返回时 movement hold 就已释放.
        """
        logger.info(
            f"Navigating to pose: ({pose.position.x:.2f}, {pose.position.y:.2f}, {pose.position.z:.2f})"
        )
        self._navigation.set_goal(pose)

        return (
            f"{message}. Started navigating to that position. "
            f"To cancel movement call the 'stop_navigation' tool."
        )

    def _navigate_to_object(self, query: str, *, timeout: float = 30.0) -> str | None:
        """基于视觉跟踪的导航: 用 VLM 在当前帧定位物体, 然后边跟踪边导航过去.

        策略: 先用 VLM 在当前画面里框出物体 -> 启动 object tracking ->
        轮询导航状态和跟踪状态. 设计了三层退出条件:
        1. 导航到达目标 (IDLE + goal_reached) -> 成功;
        2. 跟踪连续丢失超过 5s -> 提前放弃, 让外层回退链尝试其它方式;
        3. 超时 -> 放弃.
        跟踪丢失的 5s 宽限期是为了应对短暂遮挡 (如机器人转向时物体出画面).
        """
        if self._object_tracking is None:
            return None

        try:
            bbox = self._get_bbox_for_current_frame(query)
        except Exception:
            logger.error(f"Failed to get bbox for {query}", exc_info=True)
            return None

        if bbox is None:
            logger.info("[L2]   VLM did not find %r in current frame", query)
            return None

        if not self._bbox_reasonable_for_tracking(bbox):
            # bbox 过大或过小都不适合跟踪: 过大说明物体占满画面 (已到跟前),
            # 过小说明距离太远跟踪器容易丢
            logger.warning(
                "[L2]   VLM bbox for %r too large/small for tracking (%s) — skip in_frame",
                query,
                bbox,
            )
            return None

        logger.info("[L2]   ✓ VLM found %r at bbox=%s, starting object tracking ...", query, bbox)

        try:
            track_result = self._object_tracking.track(bbox)  # type: ignore[arg-type]
        except Exception:
            logger.exception("[L2]   Object tracking failed to start for %r", query)
            return None
        if isinstance(track_result, dict) and track_result.get("status") != "tracking_started":
            logger.warning("[L2]   Tracker did not start for %r: %s", query, track_result)
            return None

        start_time = time.time()
        goal_set = False
        tracking_lost_at: float | None = None

        while time.time() - start_time < timeout:
            # 导航器回到 IDLE 且曾经设过目标 -> 导航结束, 需要判断是到达还是被取消
            if self._navigation.get_state() == NavigationState.IDLE and goal_set:
                logger.info("[L2]   Navigation state=IDLE, checking result ...")
                time.sleep(1.0)
                if not self._navigation.is_goal_reached():
                    logger.info("[L2]   ✗ Goal cancelled, tracking '%s' failed", query)
                    self._object_tracking.stop_track()
                    return None
                else:
                    logger.info("[L2]   ✓ Reached '%s'", query)
                    self._object_tracking.stop_track()
                    return f"Successfully arrived at '{query}'"

            # 快速回退: 如果跟踪连续丢失超过 5s, 提前退出让外层回退链接管.
            # 宽限期是为了应对短暂遮挡 (如机器人转向时物体暂时出画面)
            if goal_set and not self._object_tracking.is_tracking():
                if tracking_lost_at is None:
                    tracking_lost_at = time.time()
                    logger.info("[L2]   Tracking lost for %r, starting 5s grace period ...", query)
                elif time.time() - tracking_lost_at > 5.0:
                    logger.warning(
                        "[L2]   ✗ Tracking lost >5s — exiting early so fallback can activate"
                    )
                    self._object_tracking.stop_track()
                    return None
            else:
                if tracking_lost_at is not None:
                    logger.info("[L2]   Tracking re-acquired for %r", query)
                tracking_lost_at = None

            # 跟踪仍在进行才标记 goal_set, 确保上面 IDLE 检查不会在还没真正
            # 开始跟踪时就误判导航结束
            if self._object_tracking.is_tracking():
                goal_set = True

            time.sleep(0.25)

        logger.warning("[L2]   ✗ Navigation to '%s' timed out after %.0fs", query, timeout)
        self._object_tracking.stop_track()
        return None

    def _resolve_landmark_from_query(self, query: str) -> SpatialRecord | None:
        """从自然语言查询解析出 landmark 空间记录.

        先用 landmark_memory 做语义/文本匹配, 再用 _record_for_current_world
        过滤掉不属于当前世界 (地图) 的记录 -- 因为空间记忆可能同时保存多个
        会话/地图的 landmark, 跨世界引用会导致导航到错误坐标.
        """
        q = query.strip()
        if not q:
            return None
        target = self._landmark_memory.resolve_by_query(q)
        if target is not None:
            # 过滤掉不属于当前世界的记录, 避免跨地图误导航
            target = self._record_for_current_world(target)
        if target is not None:
            logger.info(
                "[landmark] resolved '%s' → '%s' at (%.2f, %.2f)",
                q,
                target.name,
                target.position[0],
                target.position[1],
            )
        return target

    def _resolve_room_landmark(self, room_name: str) -> SpatialRecord | None:
        """解析房间名称到对应的房间 landmark 记录.

        采用两步策略: 先尝试语义/模糊匹配, 再退回到精确名称匹配.
        语义匹配命中后必须校验 record_type 是否为 ROOM, 因为 resolve_by_query
        可能返回非房间类型的记录 (如物体 landmark 名称恰好包含房间关键词).
        """
        name = room_name.strip()
        if not name:
            return None
        # 第一步: 语义/模糊匹配, 但必须确认命中的是 ROOM 类型
        hit = self._landmark_memory.resolve_by_query(name)
        if hit is not None and hit.record_type == RecordType.ROOM:
            return self._record_for_current_world(hit)
        # 第二步: 精确名称匹配作为兜底, 遍历所有 ROOM 类型记录逐一比对
        for rec in self._landmark_memory.query_by_type(RecordType.ROOM):
            if rec.name == name:
                return self._record_for_current_world(rec)
        return None

    def _room_anchor_sweep_for_object(
        self,
        query: str,
        search_context: _ObjectSearchContext | None = None,
    ) -> str | None:
        """逐个房间锚点扫描找物: 导航到每个房间的锚点, 到达后原地扫描查找目标物体.

        这是导航回退链的最后一层 (L4). 排序策略: 物体记录所在房间优先,
        其余房间按到物体记忆位置的距离排序, 以最小化总搜索时间.
        每个房间到达后调用 _scan_room_for_object 做 360 度视觉扫描.
        """
        rooms = self._records_for_current_world(
            self._landmark_memory.query_by_type(RecordType.ROOM)
        )
        if not rooms:
            logger.info("[L4]   No room-type landmarks — sweep skipped")
            return None

        # 尝试解析物体的 landmark 记录, 用于确定物体所属房间和记忆位置
        obj_rec = self._resolve_landmark_from_query(query)
        obj_room: str | None = None
        if obj_rec is not None and obj_rec.record_type == RecordType.LANDMARK:
            meta = obj_rec.metadata or {}
            raw_room = meta.get("room_name")
            if raw_room:
                obj_room = str(raw_room)

        skip = self._sweep_skip_rooms

        # 排序: 物体记录所在房间放最前 (最可能找到), 其余按到物体记忆位置的距离排序
        if obj_rec is not None:
            ox, oy = obj_rec.position[0], obj_rec.position[1]

            def _room_sort_key(room: Any) -> tuple[int, float]:
                rname = room.name or room.record_id
                is_obj_room = 0 if rname == obj_room else 1
                dist = math.hypot(room.position[0] - ox, room.position[1] - oy)
                return (is_obj_room, dist)

            rooms = sorted(rooms, key=_room_sort_key)
            logger.info(
                "[L4]   Sorted rooms by proximity to object's recorded room '%s' at (%.2f, %.2f)",
                obj_room or "?",
                ox,
                oy,
            )

        logger.info(
            "[L4]   Sweeping %d room(s)%s ...",
            len(rooms),
            f" (skip already searched: {sorted(skip)})" if skip else "",
        )
        for ri, room in enumerate(rooms):
            rname = room.name or room.record_id
            if rname in skip:
                logger.info(
                    "[L4]   Room %d/%d: skip %r (already searched)", ri + 1, len(rooms), rname
                )
                continue
            logger.info(
                "[L4]   Room %d/%d: %r at (%.2f, %.2f)",
                ri + 1,
                len(rooms),
                rname,
                room.position[0],
                room.position[1],
            )
            nav_msg = self._navigate_to_landmark(
                room,
                arrival_action="stop",
                arrival_distance=0.6,
                run_arrival_action=False,
                enable_visual_drift=False,
                search_context=search_context,
            )
            if search_context is not None and search_context.terminal_result is not None:
                return nav_msg
            # 漂移/超时/中止时跳过当前房间继续下一个, 不中断整个扫描流程
            if "severe visual/odom drift" in nav_msg.lower():
                logger.warning("Room sweep: drift abort at %r; trying next room", rname)
                continue
            if "timed out" in nav_msg.lower() or "aborted" in nav_msg.lower():
                logger.warning("Room sweep: nav failed at %r; trying next room", rname)
                continue

            # stored_yaw 仅在物体记录所在房间使用 -- 在其它房间用会让机器人朝错方向.
            # 这是物体记忆中记录的朝向, 指向物体所在的方向
            stored_yaw: float | None = None
            if (
                obj_rec is not None
                and obj_rec.record_type == RecordType.LANDMARK
                and obj_room == rname
            ):
                stored_yaw = obj_rec.rotation[2] if abs(obj_rec.rotation[2]) > 1e-6 else None

            result = self._scan_room_for_object(query, stored_yaw=stored_yaw)
            if result:
                return result
        return None

    def _rotate_scan_in_place(self) -> None:
        """原地旋转 360 度进行视觉扫描.

        用于房间扫描场景: 机器人到达房间锚点后原地转一圈, 让相机覆盖
        房间各个方向以查找目标物体. 按优先级尝试三种旋转接口,
        适配不同版本的 UnitreeSkillContainer 实现.
        """
        us = self._unitree_skill_container
        if us is None:
            logger.warning("360 scan: no UnitreeSkillContainer wired; pausing briefly instead")
            time.sleep(1.0)
            return
        try:
            # 优先使用专用原地旋转接口, 依次回退到 relative_move 和 move
            if hasattr(us, "rotate_in_place_degrees"):
                us.rotate_in_place_degrees(360.0)
            elif hasattr(us, "relative_move"):
                us.relative_move(forward=0.0, left=0.0, degrees=360.0)
            elif hasattr(us, "move"):
                us.move(x=0.0, y=0.0, yaw=math.radians(90.0), duration=4.0)
        except Exception:
            logger.exception("360 scan rotation failed")

    def _planar_distance_to_pose(self, pose: PoseStamped) -> float:
        # 只取 x/y 两轴, 忽略 z -- 导航目标在地面平面内定义, 高度差对到达判断无意义
        if self._latest_odom is None:
            return float("inf")
        dx = float(self._latest_odom.position.x) - float(pose.position.x)
        dy = float(self._latest_odom.position.y) - float(pose.position.y)
        return math.hypot(dx, dy)

    def _yaw_toward_point(self, gx: float, gy: float) -> float:
        """计算从当前里程计位置指向地图点 (gx, gy) 的偏航角 (弧度)."""
        if self._latest_odom is None:
            return 0.0
        dx = gx - float(self._latest_odom.position.x)
        dy = gy - float(self._latest_odom.position.y)
        # 距离过近时 atan2 不稳定, 直接返回当前朝向避免抖动
        if math.hypot(dx, dy) < 1e-6:
            return float(self._latest_odom.orientation.to_euler().z)
        return math.atan2(dy, dx)

    def _room_name_at_position(self, x: float, y: float, *, radius: float = 2.5) -> str | None:
        """查找锚点距离 (x, y) 最近的 ROOM 类型地标.

        radius 既是搜索上限也是初始最优距离, 没有任何房间落在半径内时返回 None.
        """
        best: str | None = None
        best_d = radius
        for rec in self._records_for_current_world(
            self._landmark_memory.query_by_type(RecordType.ROOM)
        ):
            d = math.hypot(rec.position[0] - x, rec.position[1] - y)
            if d < best_d:
                best_d = d
                best = rec.name
        return best

    def _coordinate_frame_stale_reason(self, target: SpatialRecord) -> str | None:
        """检测持久化的坐标是否因重启导致里程计坐标系失效.

        重启后 odom 原点会重置, 上一个 session 保存的地标坐标在新 odom 坐标系下
        就失去了对应关系. 本方法通过视觉重定位交叉验证: 如果相机画面匹配到某
        房间, 但当前 odom 与该房间的保存坐标偏差过大, 说明坐标属于旧 odom 帧.

        返回 None 表示未检测到异常; 返回字符串则给出人类可读的失效原因.
        """
        if self._latest_odom is None:
            return None

        # 同一 session 内保存的地标天然与当前 odom 一致, 无需检查
        if target.session_id and target.session_id == self._memory_session_id:
            return None

        if target.session_id and target.session_id != self._memory_session_id:
            logger.info(
                "Landmark '%s' was recorded in session %s; current session is %s",
                target.name,
                target.session_id,
                self._memory_session_id,
            )

        # 没有图像就无法做视觉交叉验证, 只能放过
        if self._latest_image is None or not hasattr(self._latest_image, "data"):
            return None

        try:
            visual_room = self._spatial_memory.query_location_by_image(self._latest_image.data)
        except Exception:
            logger.debug("stale coordinate visual check failed", exc_info=True)
            return None
        if visual_room is None:
            return None

        # CLIP 嵌入距离越小越相似; 超过阈值视为不可靠匹配
        distance = float(visual_room.metadata.get("distance", 1.0))
        if distance > self._room_visual_max_distance:
            return None

        room_rec = self._resolve_room_landmark(visual_room.name)
        if room_rec is None:
            return None

        # 核心判据: 视觉说"我在这个房间", 但 odom 离房间锚点太远,
        # 说明 odom 坐标系已经漂移, 房间坐标是旧 session 残留
        ox = float(self._latest_odom.position.x)
        oy = float(self._latest_odom.position.y)
        drift = math.hypot(ox - room_rec.position[0], oy - room_rec.position[1])
        # 2m 以内可能是正常接近过程中的偏差, 不算失效
        if drift <= 2.0:
            return None

        return (
            f"Current camera visually matches room '{visual_room.name}', but odom is "
            f"{drift:.1f}m away from that room's saved coordinate. Persisted room/object "
            "coordinates likely belong to an old odom frame after restart. Re-tag rooms "
            "in this session or start with --new-memory before navigating."
        )

    def _periodic_visual_drift_correction(
        self,
        active_goal: PoseStamped,
        last_corr: list[float],
        interval: float,
        *,
        destination_pose: PoseStamped | None = None,
        expected_room_name: str | None = None,
        enable_visual_drift: bool = True,
    ) -> tuple[PoseStamped, bool]:
        """基于房间图像重定位周期性修正里程计漂移.

        用 CLIP 视觉匹配判断机器人当前实际所在房间, 与里程计位置对比:
        - 偏差在软阈值内: 不修正
        - 偏差在硬阈值内: 平移目标坐标补偿漂移 (软修正)
        - 偏差超过硬阈值: 判定严重漂移, 取消导航 (硬修正)

        每次成功匹配还会把当前帧作为该房间的参考图像存入空间记忆,
        让参考集随机器人移动自然增长.

        last_corr 用单元素 list 模拟可变引用, 使调用方能在多次调用间共享时间戳.

        Returns:
            (可能更新后的目标, 是否检测到严重漂移)
        """
        if not enable_visual_drift:
            return (active_goal, False)

        # 节流: 一个 interval 周期内只做一次视觉重定位, 避免频繁 CLIP 推理
        now = time.time()
        if now - last_corr[0] < interval:
            return (active_goal, False)
        last_corr[0] = now

        # 逼近地标阶段: odom 离房间锚点远是正常的, 此时做漂移修正会产生误判
        if destination_pose is not None and self._planar_distance_to_pose(destination_pose) > 1.5:
            return (active_goal, False)

        if self._latest_image is None or self._latest_odom is None:
            return (active_goal, False)
        if not hasattr(self._latest_image, "data"):
            return (active_goal, False)

        try:
            loc = self._spatial_memory.query_location_by_image(self._latest_image.data)
        except Exception:
            logger.exception("visual relocalization failed")
            return (active_goal, False)

        if loc is None:
            return (active_goal, False)

        # CLIP 嵌入距离, 越小越相似
        conf = float(loc.metadata.get("distance", 1.0))
        if conf > self._room_visual_max_distance:
            return (active_goal, False)

        # 里程计预过滤: CLIP 说"我在房间A", 但房间A的锚点离当前 odom 5m 以上,
        # 几乎可以肯定是 CLIP 误匹配了两个外观相似的房间
        vx = float(loc.position[0])
        vy = float(loc.position[1])
        ox = float(self._latest_odom.position.x)
        oy = float(self._latest_odom.position.y)
        clip_to_odom = math.hypot(ox - vx, oy - vy)

        room_name = loc.name

        # 导航中如果视觉匹配到别的房间, 忽略 -- 可能是路过相邻房间
        if expected_room_name and room_name != expected_room_name:
            logger.info(
                "Visual match '%s' ignored while navigating toward room '%s'",
                room_name,
                expected_room_name,
            )
            return (active_goal, False)

        if clip_to_odom > 5.0:
            logger.warning(
                "Visual match '%s' rejected: matched position (%.1f, %.1f) is "
                "%.1fm from odometry (%.1f, %.1f) — likely false positive",
                room_name,
                vx,
                vy,
                clip_to_odom,
                ox,
                oy,
            )
            return (active_goal, False)
        # 偏差极小时, 当前帧与已保存参考高度一致, 顺势把它加入参考集
        # 扩大该房间的视觉覆盖, 后续从不同角度接近也能匹配
        if room_name and clip_to_odom < 0.8:
            try:
                euler = self._latest_odom.orientation.to_euler()
                room_loc = RobotLocation(
                    name=room_name,
                    position=(ox, oy, float(self._latest_odom.position.z)),
                    rotation=(
                        float(euler.x),
                        float(euler.y),
                        float(euler.z),
                    ),
                )
                self._spatial_memory.tag_location_with_image(room_loc, self._latest_image.data)
                if not hasattr(self, "_auto_ref_count"):
                    self._auto_ref_count: dict[str, int] = {}
                self._auto_ref_count[room_name] = self._auto_ref_count.get(room_name, 0) + 1
                logger.info(
                    "Visual match '%s' (conf=%.3f, clip2odom=%.2fm) → auto-added reference #%d",
                    room_name,
                    conf,
                    clip_to_odom,
                    self._auto_ref_count[room_name],
                )
            except Exception:
                logger.exception("Failed to auto-add room reference for '%s'", room_name)

        delta = clip_to_odom  # reuse for drift correction below

        # 三级漂移决策: 无需修正 / 软修正(平移目标) / 硬修正(取消导航)
        if delta < self._drift_soft_m:
            return (active_goal, False)

        if delta < self._drift_hard_m:
            # 软修正: 把视觉匹配位置与 odom 的差值叠加到目标坐标上,
            # 等效于"承认 odom 有系统性偏移, 用视觉差值补偿"
            dx = vx - ox
            dy = vy - oy
            shifted = PoseStamped(
                position=make_vector3(
                    float(active_goal.position.x) + dx,
                    float(active_goal.position.y) + dy,
                    float(active_goal.position.z),
                ),
                orientation=active_goal.orientation,
                frame_id=active_goal.frame_id,
            )
            logger.info(
                "Visual soft drift correction Δ=%.2fm, shifting goal by (%.2f, %.2f)",
                delta,
                dx,
                dy,
            )
            self._navigation.set_goal(shifted)
            return (shifted, False)

        # 硬修正: 偏差过大, 继续导航可能撞墙或跑偏, 取消并让上层决策
        logger.warning("Severe drift Δ=%.2fm between odom and visual room match; cancelling", delta)
        self._navigation.cancel_goal()
        return (active_goal, True)

    def _inch_goal_toward(
        self,
        standoff_pose: PoseStamped,
        arrival_distance: float,
    ) -> PoseStamped | None:
        """生成一个朝目标方向小幅前进的中间目标, 用于近距离精准靠拢.

        当机器人离目标较近 (< 1.5m) 时, 直接导航到最终目标容易因定位误差
        而反复重规划. 本方法计算一个不超过当前距离的小步长目标, 让机器人
        像蠕虫一样逐步逼近, 每步都重新评估, 提高最终停靠精度.

        Returns:
            下一步的中间目标 PoseStamped, 或 None 表示已到达 (无需再前进).
        """
        if self._latest_odom is None:
            return None
        rx = float(self._latest_odom.position.x)
        ry = float(self._latest_odom.position.y)
        gx = float(standoff_pose.position.x)
        gy = float(standoff_pose.position.y)
        dx, dy = gx - rx, gy - ry
        dist = math.hypot(dx, dy)
        # 2cm 容差: 已经在到达距离内, 不需要再前进
        if dist <= arrival_distance + 0.02:
            return None
        # 重合点保护: 避免除零
        if dist < 1e-6:
            return None
        ux, uy = dx / dist, dy / dist
        # 速度随距离衰减: 远时快 (上限 0.6), 近时慢 (下限 0.15), 防止冲过头
        v = max(0.15, min(0.6, dist * 0.4))
        # 步长 = 速度 * 0.35s, 但不超过"剩余距离 - 到达距离的 85%",
        # 确保每步都留有余量, 不会一步跨过目标
        step = min(v * 0.35, max(0.0, dist - arrival_distance * 0.85))
        # 最小步长 8cm, 避免步长过小导致导航模块认为已到达而拒绝执行
        step = max(0.08, step)
        return PoseStamped(
            position=make_vector3(rx + ux * step, ry + uy * step, standoff_pose.position.z),
            orientation=standoff_pose.orientation,
            frame_id=standoff_pose.frame_id,
        )

    def _wait_goal_with_relocalize(
        self,
        active_goal: PoseStamped,
        last_corr: list[float],
        segment_deadline: float,
        relocalize_interval: float,
        *,
        destination_pose: PoseStamped | None = None,
        expected_room_name: str | None = None,
        enable_visual_drift: bool = True,
        search_context: _ObjectSearchContext | None = None,
    ) -> tuple[PoseStamped, bool]:
        """等待导航目标完成, 同时周期性做视觉漂移修正.

        在 deadline 之前循环检查三种退出条件:
        1. 严重漂移 -> 立即返回, 让上层决定是否重试;
        2. 导航到达 -> 正常完成;
        3. 超时 -> 返回当前状态, 上层可继续推进或放弃.

        200ms 轮询间隔在响应性和 CPU 开销之间取平衡.
        """
        ag: PoseStamped = active_goal
        while time.time() < segment_deadline:
            self._poll_enroute_search(search_context)
            ag, severe = self._periodic_visual_drift_correction(
                ag,
                last_corr,
                relocalize_interval,
                destination_pose=destination_pose,
                expected_room_name=expected_room_name,
                enable_visual_drift=enable_visual_drift,
            )
            if severe:
                return (ag, True)
            if self._navigation.is_goal_reached():
                return (ag, False)
            time.sleep(0.2)
        self._poll_enroute_search(search_context)
        return (ag, False)

    def _run_arrival_action(self, action: str, target_name: str) -> str:
        """到达目标后执行预设动作.

        Args:
            action: 到达后要执行的动作名称, 支持的取值包括 stop/none, point/present,
                sit_point/sit_and_point, sit/sit_down, wave/wave_hand, stand/stand_up,
                recover/recovery/recovery_stand 等.
            target_name: 目标名称, 仅用于拼接返回消息, 不影响动作选择.

        Returns:
            描述执行结果的字符串, 供 agent 感知本次到达动作的状态.
        """
        # 统一转为小写并去空白, 这样用户输入 "Sit" / " sit " 都能匹配
        a = (action or "stop").lower().strip()
        # 到达后先取消导航目标, 避免底盘仍在尝试移动的同时执行动作导致冲突
        self._navigation.cancel_goal()
        if a in ("stop", "none", ""):
            return f"Reached '{target_name}' (arrival_action=stop)."
        us = self._unitree_skill_container
        if us is None:
            # 没有接入 Unitree 技能容器时无法执行任何肢体动作, 只能跳过并告知 agent
            logger.warning("arrival_action=%s but UnitreeSkillContainer is not wired", action)
            return (
                f"Reached '{target_name}' (arrival_action={action!r} skipped: no gesture module)."
            )
        if a in ("point", "present"):
            # "Hello" 指令在 Go2 上表现为挥手/指向动作, 执行后需恢复站姿以保持稳定
            point_out = us.execute_sport_command("Hello")
            time.sleep(0.5)
            recovery_out = us.execute_sport_command("RecoveryStand")
            return (
                f"Reached '{target_name}', executing arrival_action={action!r}: "
                f"{point_out} {recovery_out}"
            )

        if a in ("sit_point", "sit_and_point", "sit_point_experimental"):
            # 先坐下再挥手, 坐下后底盘更稳定不易倾倒; 各动作间留足时间让电机执行完
            sit_out = us.execute_sport_command("Sit")
            time.sleep(1.0)
            point_out = us.execute_sport_command("Hello")
            time.sleep(1.0)
            recovery_out = us.execute_sport_command("RecoveryStand")
            return (
                f"Reached '{target_name}', executing arrival_action={action!r}: "
                f"{sit_out} {point_out} {recovery_out}"
            )

        # 将用户可读的动作别名映射到 Unitree SDK 的 sport command 名称
        cmd_map = {
            "sit": "Sit",
            "sit_down": "Sit",
            "wave": "Hello",
            "wave_hand": "Hello",
            "stand": "StandUp",
            "stand_up": "StandUp",
            "recover": "RecoveryStand",
            "recovery": "RecoveryStand",
            "recovery_stand": "RecoveryStand",
        }
        cmd = cmd_map.get(a)
        if cmd is None:
            # 无法识别的动作不做任何肢体操作, 避免误触发危险动作
            return f"Reached '{target_name}' (unknown arrival_action={action!r})."
        out = us.execute_sport_command(cmd)
        return f"Reached '{target_name}', executing arrival_action={action!r}: {out}"

    def _servo_to_bbox(self, bbox: BBox) -> bool:
        """原地旋转, 将目标物体的水平中心对准相机视野正中.

        将像素坐标的 bbox 转换到 0-1000 归一化坐标系, 再通过
        ``yaw_offset_from_bbox`` 计算偏航角, 最后原地旋转到正对目标.

        Args:
            bbox: 目标在当前画面中的像素边界框, 格式为 (x1, y1, x2, y2).

        Returns:
            True 表示旋转成功或本身已居中无需旋转, False 表示因无图像等原因失败.
        """
        if self._latest_image is None:
            return False
        h, w = self._latest_image.data.shape[:2]
        x1_px, y1_px, x2_px, y2_px = bbox
        # 像素坐标归一化到 0-1000 空间, 与 _scale_bbox_to_image 中 Qwen 分支的逆变换保持一致,
        # 这样 yaw_offset_from_bbox 才能用统一的归一化坐标计算角度
        x1 = x1_px / w * 1000.0
        y1 = y1_px / h * 1000.0
        x2 = x2_px / w * 1000.0
        y2 = y2_px / h * 1000.0

        offset_rad = yaw_offset_from_bbox(x1, y1, x2, y2, self._camera_hfov_deg)
        offset_deg = math.degrees(offset_rad)

        # 3 度以内视为已居中, 避免因微小误差反复微调产生抖动
        if abs(offset_deg) < 3.0:
            logger.info("[servo] object already centred (offset=%.1f°)", offset_deg)
            return True

        # yaw_offset 为正表示目标在画面右侧, 需要顺时针(负角度)旋转才能对准
        turn_deg = -offset_deg
        logger.info(
            "[servo] turning %.1f° to face object (bbox offset=%.1f°)",
            turn_deg,
            offset_deg,
        )
        return self._rotate_in_place_degrees(turn_deg)

    def _detect_and_servo(self, target_name: str) -> str | None:
        """在当前帧中检测目标物体并原地旋转伺服对准它.

        整合了 VLM 检测和视觉伺服两个步骤: 先用 VLM 获取目标 bbox, 校验合理性后
        调用 _servo_to_bbox 原地旋转对准. 任何一步失败都返回 None, 由调用方决定
        是否继续重试或降级.

        Args:
            target_name: 要检测和伺服对准的目标物体名称.

        Returns:
            成功时返回描述性字符串, 失败(无图像/VLM 失败/未找到/bbox 不合理/伺服失败)
            返回 None.
        """
        if self._latest_image is None:
            return None
        # VLM 查询可能因网络超时或模型异常而失败, 捕获后返回 None 让调用方降级处理
        try:
            bbox = self._get_bbox_for_current_frame(target_name)
        except Exception:
            logger.exception("[detect+servo] VLM query failed for '%s'", target_name)
            return None

        if bbox is None:
            return None

        # 过大/过小的 bbox 不可靠: 过大可能是 VLM 误检框住整个画面,
        # 过小则目标太远无法精确伺服, 两种情况都跳过
        if not self._bbox_reasonable_for_tracking(bbox):
            logger.warning(
                "[detect+servo] bbox for '%s' too large/small for servoing (%s) — skip",
                target_name,
                bbox,
            )
            return None

        logger.info("[detect+servo] ✓ found '%s' at bbox=%s, servoing ...", target_name, bbox)
        if not self._servo_to_bbox(bbox):
            logger.warning("[detect+servo] servo rotation failed for '%s'", target_name)
            return None

        logger.info("[detect+servo] ✓ servoed to '%s'", target_name)
        return f"Visually acquired '{target_name}'"

    def _recognize_objects_simple(self, image: Image) -> list[dict[str, Any]]:
        """用 VLM 列出图中所有物体, 返回紧凑格式的识别结果列表.

        复用 tag_room 阶段同一个 prompt (_VLM_SIMPLE_BBOX_PROMPT), 保证扫描
        时的识别口径和建库时一致 -- 否则同一物体在建库和查找阶段可能被
        VLM 起不同的名字, 导致名称匹配失败.
        """
        response = self._vl_model.query(image, self._VLM_SIMPLE_BBOX_PROMPT)
        if not response or not str(response).strip():
            return []
        items = parse_simple_bbox_line(str(response).strip())
        logger.info(
            "[room_scan] list VLM recognition: %d object(s) — %s",
            len(items),
            ", ".join(str(o.get("name", "")) for o in items[:8]),
        )
        return items

    @staticmethod
    def _bbox_area_0to1000(bbox: list[int] | list[float]) -> float:
        # VLM 输出的 bbox 在 0-1000 归一化坐标系中, 这里直接用该坐标系
        # 计算面积即可用于比较大小 (无需换算回像素), max(0, ...) 防御
        # VLM 偶尔给出 x1 > x2 的倒序坐标导致负面积
        x1, y1, x2, y2 = (float(v) for v in bbox)
        return max(0.0, x2 - x1) * max(0.0, y2 - y1)

    def _pick_object_from_recognition(
        self,
        items: list[dict[str, Any]],
        target_name: str,
    ) -> dict[str, Any] | None:
        """从 VLM 识别结果列表中挑选一个与目标名称匹配的物体条目.

        当画面中存在多个同名物体时 (例如两把椅子), 选择 bbox 面积最大的那个
        -- 面积大通常意味着距离更近, 优先伺服到最近的物体更符合用户预期.
        名称匹配前会经过 _normalize_vlm_object_name 归一化, 以消除中英文
        和大小写差异.
        """
        want = _normalize_vlm_object_name(target_name)
        # 先归一化再比较, 避免 "chair" vs "椅子" 这类因 VLM 输出语言不同而漏配
        candidates = [
            item for item in items if _normalize_vlm_object_name(str(item.get("name", ""))) == want
        ]
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        # 多个候选时取面积最大的, 距离最近优先
        return max(
            candidates,
            key=lambda item: self._bbox_area_0to1000(list(item.get("bbox", []))),
        )

    def _bbox_from_simple_recognition_item(self, item: dict[str, Any]) -> BBox | None:
        """将识别结果中的 0-1000 归一化 bbox 转换为像素坐标 bbox.

        _scale_bbox_to_image 会自动判断坐标是 0-1 分数 / 0-1000 / 还是
        绝对像素, 因此这里直接透传即可. 需要同时校验 _latest_image 存在,
        因为缩放依赖图像尺寸.
        """
        bbox_list = item.get("bbox")
        if not isinstance(bbox_list, list) or len(bbox_list) != 4 or self._latest_image is None:
            return None
        return _scale_bbox_to_image(
            (float(bbox_list[0]), float(bbox_list[1]), float(bbox_list[2]), float(bbox_list[3])),
            self._latest_image,
        )

    def _detect_and_servo_by_list_recognition(self, target_name: str) -> str | None:
        """用列表识别方式检测当前帧中的目标物体并伺服转向它.

        与 _detect_and_servo 的区别: 后者用 query prompt 单独询问某一物体
        的位置, 而本方法让 VLM 一次性列出画面中所有物体再按名称匹配.
        在房间扫描的每一步旋转后调用, 列表识别比逐个 query 更高效 --
        一次 VLM 调用就能覆盖所有可能的目标, 无需为每个候选物体单独请求.
        """
        if self._latest_image is None:
            return None
        try:
            items = self._recognize_objects_simple(self._latest_image)
        except Exception:
            logger.exception("[room_scan] VLM list recognition failed for '%s'", target_name)
            return None

        want = _normalize_vlm_object_name(target_name)
        seen = [_normalize_vlm_object_name(str(item.get("name", ""))) for item in items]
        if want not in seen:
            # 当前帧没看到目标, 返回 None 让调用方继续旋转
            logger.info(
                "[room_scan] '%s' not in recognition list %s — continue rotating",
                want,
                seen or ["(empty)"],
            )
            return None

        obj = self._pick_object_from_recognition(items, target_name)
        if obj is None:
            return None

        bbox = self._bbox_from_simple_recognition_item(obj)
        if bbox is None:
            return None
        # 过滤过大/过小的 bbox: 过大说明 VLM 框错了整个画面, 过小说明物体
        # 太远无法可靠伺服, 两种情况都不值得转向
        if not self._bbox_reasonable_for_tracking(bbox):
            logger.warning(
                "[room_scan] bbox for '%s' unreasonable after list recognition: %s",
                want,
                bbox,
            )
            return None

        logger.info("[room_scan] ✓ '%s' in list at bbox=%s, servoing ...", want, obj.get("bbox"))
        if not self._servo_to_bbox(bbox):
            logger.warning("[room_scan] servo rotation failed for '%s'", want)
            return None
        logger.info("[room_scan] ✓ servoed to '%s' via list recognition", want)
        return f"Visually acquired '{want}' (list recognition)"

    def _scan_room_for_object(
        self,
        target_name: str,
        *,
        stored_yaw: float | None = None,
    ) -> str | None:
        """在房间内做 360 度旋转扫描以搜索目标物体.

        策略分三步:
        1. 若有之前存储的朝向角(stored_yaw), 先转到该方向 -- 这是空间记忆
           中记录的物体所在方位, 能大幅减少扫描步数;
        2. 转到位后先用 query prompt 做一次精确检测;
        3. 若未命中, 则分 n 步旋转扫描, 每步用列表识别检测所有物体.

        扫描阶段之所以用列表识别而非 query prompt, 是因为扫描时不知道目标
        会在哪个角度出现, 列表识别一次调用覆盖所有物体, 效率更高.
        """
        step_deg = _search_rotation_step_deg()
        # 向上取整覆盖完整 360 度, 末尾会多转一小段确保无盲区 -- 宁可
        # 重叠也不留缝隙, 否则刚好卡在两个步长之间的物体会被漏掉
        n_steps = max(1, math.ceil(360.0 / step_deg))

        if stored_yaw is not None:
            euler = self._odom_euler_tuple()
            if euler is not None:
                diff = stored_yaw - euler[2]
                # 用 atan2(sin, cos) 把角度差归一化到 [-pi, pi], 避免跨越
                # +/-180 度边界时出现 350 度 vs -10 度这种等价但数值差很大
                # 的情况, 导致机器人多转一整圈
                diff = math.atan2(math.sin(diff), math.cos(diff))
                diff_deg = math.degrees(diff)
                # 5 度以内认为已经对准, 不值得小幅转动 (小幅旋转的累积误差
                # 反而可能让机器人偏离)
                if abs(diff_deg) > 5.0:
                    logger.info(
                        "[room_scan] turning %.1f° to face '%s' (stored yaw=%.1f°)",
                        diff_deg,
                        target_name,
                        math.degrees(stored_yaw),
                    )
                    self._rotate_in_place_degrees(diff_deg)
                    time.sleep(0.5)

        # 先检测当前视角: 如果 stored_yaw 已经把目标带入视野就不用扫描了
        try:
            result = self._detect_and_servo(target_name)
            if result:
                logger.info("[room_scan] ✓ '%s' found in initial view", target_name)
                return result
        except Exception:
            logger.exception("[room_scan] detect-and-servo error for '%s'", target_name)

        logger.info(
            "[room_scan] '%s' not in view; doing %.0f°×%d scan ...",
            target_name,
            step_deg,
            n_steps,
        )
        logger.info(
            "[room_scan] rotation scan for '%s' uses list recognition (not query prompt)",
            target_name,
        )

        # 逐步旋转扫描: 每转一步停 0.5s 等画面稳定再识别, 避免运动模糊
        # 导致 VLM 识别失败
        for step in range(n_steps):
            self._rotate_in_place_degrees(step_deg)
            time.sleep(0.5)
            try:
                result = self._detect_and_servo_by_list_recognition(target_name)
                if result:
                    logger.info(
                        "[room_scan] ✓ '%s' found at scan step %d/%d",
                        target_name,
                        step + 1,
                        n_steps,
                    )
                    return result
            except Exception:
                # 单步识别失败不中断整个扫描, 继续转下一步 -- 可能只是
                # 这一帧画面模糊或 VLM 抽风, 下一个角度也许就能识别
                logger.exception(
                    "[room_scan] detect error for '%s' at step %d/%d",
                    target_name,
                    step + 1,
                    n_steps,
                )
        return None

    def _visual_acquire_object(
        self, target_name: str, stored_yaw: float | None = None
    ) -> str | None:
        """到达目标坐标后, 用视觉定位并面向物体.

        到达导航终点只保证机器人在物体附近, 但不一定正对物体.
        如果有之前存储的朝向角(stored_yaw), 扫描时会优先转到该方向,
        再通过 VLM 检测 + 视觉伺服把物体居中到视野中.

        Args:
            target_name: 要搜索的物体名称.
            stored_yaw: 已知时为世界坐标系下的偏航角(弧度), 扫描前优先面朝此方向.

        Returns:
            成功视觉锁定物体时返回描述信息, 失败返回 None.
        """
        # 扫描整个房间寻找目标物体; stored_yaw 可让扫描从最可能的方向开始
        result = self._scan_room_for_object(target_name, stored_yaw=stored_yaw)
        if result:
            return result
        logger.warning("[visual_acquire] ✗ '%s' not found visually", target_name)
        return None

    def _parse_vlm_object_list_response(self, response: str | None) -> list[dict[str, Any]]:
        """从 VLM 文本响应中提取物体列表.

        VLM 返回的 JSON 可能是数组(多个物体)或单个对象(一个物体),
        也可能因模型输出不稳定而解析失败. 此方法统一兜底为 list[dict].

        Args:
            response: VLM 的原始文本响应, 可能为 None 或非 JSON 文本.

        Returns:
            解析出的物体字典列表; 解析失败或无有效内容时返回空列表.
        """
        parsed = extract_json_from_llm_response(response or "")
        if parsed is None:
            return []
        if isinstance(parsed, list):
            # 过滤掉非 dict 元素, 防止下游访问键时抛错
            return [o for o in parsed if isinstance(o, dict)]
        if isinstance(parsed, dict):
            # 单对象场景包装成单元素列表, 统一后续处理流程
            return [parsed]
        logger.warning("VLM object list parse: unexpected type %s", type(parsed).__name__)
        return []

    def _pose_for_vlm_object(
        self,
        obj: dict[str, Any],
        default_position: tuple[float, float, float],
        default_rotation: tuple[float, float, float],
        frame_poses: list[tuple[tuple[float, float, float], tuple[float, float, float]]] | None,
    ) -> tuple[tuple[float, float, float], tuple[float, float, float], int | None]:
        """根据 VLM 检测到物体时所在的帧, 取该帧的捕获位姿作为物体位姿.

        全景扫描时会拍摄多帧, 每帧记录机器人当时的位姿. VLM 返回的
        image_indices 指明物体出现在哪一帧, 用该帧位姿比用默认位姿更准确.

        Args:
            obj: VLM 返回的单个物体字典, 含 image_indices 字段.
            default_position: 无法关联到帧时使用的默认位置.
            default_rotation: 无法关联到帧时使用的默认旋转.
            frame_poses: 各帧的 (position, rotation) 列表.

        Returns:
            (位置, 旋转, 帧索引); 帧索引为 None 表示未关联到具体帧.
        """
        indices = obj.get("image_indices")
        if frame_poses and isinstance(indices, list) and indices:
            try:
                idx = int(indices[0])
            except (TypeError, ValueError):
                idx = -1
            # 越界保护: 索引非法时回退到默认位姿
            if 0 <= idx < len(frame_poses):
                return frame_poses[idx][0], frame_poses[idx][1], idx
        return default_position, default_rotation, None

    def _store_detected_objects(
        self,
        objects: list[dict[str, Any]],
        position: tuple[float, float, float],
        rotation: tuple[float, float, float],
        *,
        room_name: str | None = None,
        frame_poses: list[tuple[tuple[float, float, float], tuple[float, float, float]]]
        | None = None,
        frames: list[Any] | None = None,
    ) -> int:
        """将 VLM 检测到的物体存入空间记忆.

        对每个物体: 解析名称和描述, 关联到拍摄帧的位姿, 再根据 bbox
        精修物体的朝向角, 最后写入 landmark memory 供后续导航复用.

        Args:
            objects: VLM 返回的物体字典列表.
            position: 默认位置(无法关联帧时使用).
            rotation: 默认旋转(无法关联帧时使用).
            room_name: 物体所在房间名, 用于空间记忆分层检索.
            frame_poses: 各拍摄帧的 (位置, 旋转) 列表.
            frames: 各拍摄帧的图像数据列表, 用于从 bbox 推算物体朝向.

        Returns:
            成功存入空间记忆的物体数量.
        """
        stored = 0
        hfov_rad = math.radians(_CAMERA_HFOV_DEG)
        for obj in objects:
            name = _normalize_vlm_object_name((obj.get("name") or "").strip())
            if not name:
                continue
            desc = (obj.get("description") or "").strip()
            indices = obj.get("image_indices")
            # 优先用物体出现帧的位姿, 比默认位姿更贴近真实位置
            obj_pos, obj_rot, view_idx = self._pose_for_vlm_object(
                obj, position, rotation, frame_poses
            )
            if indices:
                desc = f"{desc} (views {indices})".strip() if desc else f"views {indices}"

            # 如果 VLM 返回了 bbox, 利用它在图像中的水平位置推算物体相对机器人的朝向偏移
            bbox = obj.get("bbox")
            # 单帧场景下 image_indices 可能缺失, 此时默认取第 0 帧
            _eff_idx = view_idx
            if _eff_idx is None and frames is not None and len(frames) == 1:
                _eff_idx = 0
            if bbox and frames and _eff_idx is not None and 0 <= _eff_idx < len(frames):
                try:
                    bbox_ok = (
                        isinstance(bbox, (list, tuple))
                        and len(bbox) == 4
                        and all(isinstance(v, (int, float)) for v in bbox)
                    )
                    if bbox_ok:
                        frame = frames[_eff_idx]
                        if hasattr(frame, "shape") and len(frame.shape) >= 2:
                            h, w = frame.shape[:2]
                            x1, y1, x2, y2 = (float(v) for v in bbox)
                            # bbox 坐标格式不统一: 可能是 0-1 归一化, 0-1000 千分比, 或绝对像素
                            # 根据最大值范围自动判断并转换为像素坐标
                            mx = max(x1, y1, x2, y2)
                            if mx <= 1.0:
                                x1, x2 = x1 * w, x2 * w
                            elif mx <= 1000.0 and min(x1, y1, x2, y2) >= 0.0:
                                x1, x2 = x1 / 1000.0 * w, x2 / 1000.0 * w
                            # 只在水平方向合理时才推算朝向, 避免 bbox 异常导致错误角度
                            if 0 <= x1 < x2 <= w * 2:
                                cx = (x1 + x2) / 2.0
                                # bbox 中心偏离图像中心的比例 * 水平视场角 = 物体相对光轴的偏角
                                angle_offset = (cx / w - 0.5) * hfov_rad
                                robot_yaw = float(obj_rot[2])
                                # 物体朝向 = 机器人朝向 + bbox 偏角
                                object_yaw = robot_yaw + angle_offset
                                obj_rot = (0.0, 0.0, object_yaw)
                                logger.info(
                                    "VLM bbox bearing: '%s' cx=%.0f/%d "
                                    "angle_offset=%.1f° robot_yaw=%.1f° → object_yaw=%.1f°",
                                    name,
                                    cx,
                                    w,
                                    math.degrees(angle_offset),
                                    math.degrees(robot_yaw),
                                    math.degrees(object_yaw),
                                )
                except Exception:
                    # bbox 推算失败不应阻断存储, 降级使用帧位姿的原始朝向
                    logger.debug("bbox-to-bearing failed for '%s'", name, exc_info=True)

            # 组装空间记忆元数据: 观测位姿 + 重定位上下文 + 房间/帧索引
            meta: dict[str, Any] = {
                "observed_position": list(obj_pos),
                "observed_rotation": list(obj_rot),
            }
            meta.update(
                self._map_metadata_for_pose(
                    obj_pos,
                    obj_rot,
                    observation_source="vlm_object_panorama",
                )
            )
            if room_name:
                meta["room_name"] = room_name
            if view_idx is not None:
                meta["view_index"] = view_idx
            if indices:
                meta["image_indices"] = indices
            rec = SpatialRecord(
                name=name,
                record_type=RecordType.LANDMARK,
                position=obj_pos,
                rotation=obj_rot,
                state=desc,
                metadata=meta,
                session_id=self._memory_session_id,
            )
            self._landmark_memory.record(rec)
            stored += 1
            logger.info(
                "VLM stored object '%s' at (%.2f, %.2f)%s%s",
                name,
                obj_pos[0],
                obj_pos[1],
                f" [room={room_name}]" if room_name else "",
                f" view={view_idx}" if view_idx is not None else "",
            )
        return stored

    def _vlm_query_all_images(self, images: list[Any], prompt: str) -> str:
        """一次性把多张图片打包成一个多模态 VLM 请求.

        相比逐张调用, 批量请求能减少网络往返和 token 开销,
        同时让模型跨图片做对比判断(例如全景环视去重).

        Args:
            images: 待查询的图片列表, 元素可以是 DimosImage 或 numpy 数组.
            prompt: 发给 VLM 的文本指令.

        Returns:
            VLM 返回的文本(已 strip).

        Raises:
            RuntimeError: VLM 返回空响应时抛出, 上游需要区分"API 失败"和"无内容".
        """
        from dimos.msgs.sensor_msgs.Image import Image as DimosImage

        if not images:
            logger.warning("[VLM] query_batch skipped: empty image list")
            return ""
        # 统一转成 DimosImage, 调用方可能传入裸 numpy 数组
        dimos_images = [
            img if isinstance(img, DimosImage) else DimosImage.from_numpy(img) for img in images
        ]
        shapes = [getattr(img.data, "shape", None) for img in dimos_images]
        model_cls = type(self._vl_model).__name__
        logger.info(
            "[VLM] query_batch START model=%s n_images=%d shapes=%s prompt_len=%d",
            model_cls,
            len(dimos_images),
            shapes,
            len(prompt),
        )
        t0 = time.time()
        try:
            results = self._vl_model.query_batch(dimos_images, prompt)
        except Exception:
            # 记录耗时便于排查超时是模型侧还是网络侧
            logger.exception(
                "[VLM] query_batch FAILED after %.1fs (model=%s)",
                time.time() - t0,
                model_cls,
            )
            raise
        elapsed = time.time() - t0
        # query_batch 返回列表, 每个元素对应一次推理; 这里只发了一个 prompt 所以取 [0]
        if not results or not (results[0] or "").strip():
            logger.error(
                "[VLM] query_batch empty response after %.1fs (model=%s)",
                elapsed,
                model_cls,
            )
            raise RuntimeError("VLM query_batch returned empty response")
        text = results[0].strip()
        logger.info(
            "[VLM] query_batch OK in %.1fs — response preview: %s",
            elapsed,
            text[:200].replace("\n", " "),
        )
        return str(text)

    # ------------------------------------------------------------------ #
    #  紧凑格式 VLM 辅助方法 (单帧, bbox 感知)                            #
    # ------------------------------------------------------------------ #

    # 紧凑格式 prompt: 强制 VLM 输出单行 "名称,x1,y1,x2,y2;..." 以降低解析复杂度.
    # 坐标用 0-1000 归一化而非像素绝对值, 这样同一 prompt 可适配不同分辨率.
    _VLM_SIMPLE_BBOX_PROMPT = (
        "识别图中物品，最多8个。"
        "仅输出一行，格式：名称,x1,y1,x2,y2;名称,x1,y1,x2,y2;"
        "坐标为0-1000整数，相对原图。"
        "不要解释、不要JSON、不要换行，物品名称要求为中文。"
        "跳过墙面、地面、天花板。"
    )

    #: 相机水平视场角(度). 子类按实际相机参数覆盖, 用于从 bbox 推算物体方位角偏移.
    _camera_hfov_deg: float = 90.0

    def _parse_simple_vlm_response(
        self,
        response: str,
        capture_yaw: float,
    ) -> list[dict[str, Any]]:
        """解析紧凑格式 VLM 响应(名称,x1,y1,x2,y2;), 转为内部物体字典列表.

        关键设计: 利用相机水平视场角把 bbox 的水平中心转成相对光轴的偏航角(yaw_offset),
        再叠加拍照时刻的机器人朝向(capture_yaw), 得到物体在世界坐标系下的绝对朝向.
        这样即使物体不在画面正中, 也能估算出大致方位, 供后续导航和视觉锁定使用.

        Args:
            response: VLM 返回的原始文本.
            capture_yaw: 拍照时机器人的朝向(radians, 来自 odom 的 yaw 分量).

        Returns:
            物体字典列表, 每个字典包含 name / bbox(0-1000归一化) /
            yaw_offset(相对画面中心偏角) / object_yaw(世界系绝对方位).
        """
        # 由 parse_simple_bbox_line 拆出每个物体的 bbox, 然后利用相机视场角
        # 把 bbox 水平中心转成偏航角偏移, 叠加拍照朝向得到世界系绝对方位
        items = parse_simple_bbox_line(response)
        result: list[dict[str, Any]] = []
        for item in items:
            x1, y1, x2, y2 = item["bbox"]
            # bbox 水平中心 -> 相对光轴的偏航角
            offset = yaw_offset_from_bbox(x1, y1, x2, y2, self._camera_hfov_deg)
            item["yaw_offset"] = offset
            # 机器人朝向 + 偏移 = 世界坐标系下的物体朝向
            item["object_yaw"] = capture_yaw + offset
            result.append(item)
        return result

    def _detect_single_frame_async(
        self,
        image_data: Any,
        position: tuple[float, float, float],
        rotation: tuple[float, float, float],
        room_name: str,
    ) -> None:
        """对单张拍照帧运行 VLM 检测, 并将识别到的物体存入地标记忆.

        使用紧凑 bbox 格式(名称,x1,y1,x2,y2;)输出, 这样可以通过 bbox 水平中心
        相对画面中心的偏移量, 结合机器人拍照时的朝向, 推算出每个物体更准确的
        世界坐标系朝向. rotation[2](odom yaw) 会按物体的 bbox 偏移逐个修正后再存储.

        Args:
            image_data: 单帧图像(DimosImage 或 numpy 数组).
            position: 拍照时机器人位置 (x, y, z).
            rotation: 拍照时机器人姿态 (roll, pitch, yaw), yaw 来自 odom.
            room_name: 当前所在房间名, 用于元数据标注.
        """
        from dimos.msgs.sensor_msgs.Image import Image as DimosImage

        if self._vl_model is None:
            logger.warning("[VLM single-frame] no VLM model available, skipping room=%r", room_name)
            return

        image = (
            image_data if isinstance(image_data, DimosImage) else DimosImage.from_numpy(image_data)
        )
        # 拍照时刻的机器人朝向, 作为计算每个物体世界系 yaw 的基准
        capture_yaw = float(rotation[2])

        logger.info(
            "[VLM single-frame] START room=%r pos=(%.2f,%.2f) yaw=%.2f°",
            room_name,
            position[0],
            position[1],
            math.degrees(capture_yaw),
        )
        try:
            response = self._vl_model.query(image, self._VLM_SIMPLE_BBOX_PROMPT)
        except Exception:
            logger.exception("[VLM single-frame] VLM call FAILED for room=%r", room_name)
            return

        if not response or not response.strip():
            logger.warning("[VLM single-frame] empty response for room=%r", room_name)
            return

        logger.info(
            "[VLM single-frame] response room=%r: %s",
            room_name,
            response.strip()[:200].replace("\n", " "),
        )

        parsed = self._parse_simple_vlm_response(response.strip(), capture_yaw)
        if not parsed:
            logger.warning("[VLM single-frame] no objects parsed for room=%r", room_name)
            return

        stored = 0
        for item in parsed:
            # 规范化物体名称(去英文/统一称呼), 便于后续按名称检索
            name = _normalize_vlm_object_name(item.get("name", "").strip())
            if not name:
                continue
            # 用 bbox 偏移修正后的 yaw, 比单纯用机器人朝向更准确
            obj_yaw = float(item.get("object_yaw", capture_yaw))
            obj_rotation = (rotation[0], rotation[1], obj_yaw)
            bbox = item.get("bbox", [])
            # 元数据保留完整的观测信息, 便于后续调试和重定位
            meta: dict[str, Any] = {
                "observed_position": list(position),
                "observed_rotation": list(obj_rotation),
                "room_name": room_name,
                "bbox_0to1000": bbox,
                "yaw_offset_deg": round(math.degrees(float(item.get("yaw_offset", 0.0))), 1),
                "capture_yaw_deg": round(math.degrees(capture_yaw), 1),
            }
            meta.update(
                self._map_metadata_for_pose(
                    position,
                    obj_rotation,
                    observation_source="vlm_object_single_frame",
                )
            )
            rec = SpatialRecord(
                name=name,
                record_type=RecordType.LANDMARK,
                position=position,
                rotation=obj_rotation,
                state="",
                metadata=meta,
                session_id=self._memory_session_id,
            )
            self._landmark_memory.record(rec)
            stored += 1
            logger.info(
                "[VLM single-frame] stored '%s' at (%.2f,%.2f) yaw=%.1f° "
                "(cap=%.1f° offset=%.1f°) room=%r",
                name,
                position[0],
                position[1],
                math.degrees(obj_yaw),
                math.degrees(capture_yaw),
                math.degrees(float(item.get("yaw_offset", 0.0))),
                room_name,
            )

        logger.info(
            "[VLM single-frame] DONE room=%r stored=%d object(s)",
            room_name,
            stored,
        )

    def _detect_objects_panorama_batch_async(
        self,
        captures: list[tuple[Any, tuple[float, float, float], tuple[float, float, float]]],
        room_name: str,
    ) -> None:
        """对全景环视的多帧一次性调用 VLM, 检测并存储去重后的物体.

        将整个房间的 360 度环视帧打包成一次 VLM 请求, 让模型跨帧去重并标注
        物体出现在哪些帧中(image_indices). 这样比逐帧检测后再合并去重更准确,
        因为模型能看到全局上下文. 单帧时复用通用 prompt 并强制 image_indices=[0].

        Args:
            captures: 每个元素为 (图像, 位置, 朝向), 代表一个全景视角的拍摄.
            room_name: 当前房间名.
        """
        n = len(captures)
        frames = [c[0] for c in captures]
        frame_poses = [(c[1], c[2]) for c in captures]
        # 以最后一帧的位姿作为物体存储的参考位姿(机器人最终停留位置)
        position = frame_poses[-1][0] if frame_poses else (0.0, 0.0, 0.0)
        rotation = frame_poses[-1][1] if frame_poses else (0.0, 0.0, 0.0)
        logger.info(
            "[VLM] panorama thread START room=%r n_frames=%d thread=%s",
            room_name,
            n,
            threading.current_thread().name,
        )
        if n == 0:
            logger.warning("[VLM] panorama thread END (no frames) room=%r", room_name)
            return

        if n == 1:
            # 单帧时复用通用列表 prompt, 但强制标注 image_indices=[0]
            prompt = _VLM_OBJECT_LIST_PROMPT + ' Include "image_indices": [0] for each object.'
        else:
            # 多帧全景: 让 VLM 跨帧去重并标注物体出现在哪些帧
            prompt = (
                f"以下 {n} 张图（编号 0 到 {n - 1}）来自房间「{room_name}」的 360° 环视。\n"
                "列出所有图中可见、可单独指认的物体，按中文名去重。\n"
                "【硬性要求】name 必须是 1–4 个汉字（如：电脑、书桌、办公椅），禁止 computer/desk/chair。\n"
                "Return ONLY a JSON array: "
                '[{"name": "<中文名>", "description": "<简短中文说明>", '
                '"image_indices": [0, 2], "bbox": [x1, y1, x2, y2]}]. '
                "bbox 为物体在对应照片中的边界框（像素坐标）。"
                "跳过墙面、地面、天花板、门。若无物体，返回 []."
            )

        try:
            logger.info("[VLM] panorama calling API for room=%r ...", room_name)
            response = self._vlm_query_all_images(frames, prompt)
        except Exception:
            logger.exception("[VLM] panorama FAILED for room=%r (%d frames)", room_name, n)
            return

        objects = self._parse_vlm_object_list_response(response)
        if not objects:
            logger.warning(
                "[VLM] panorama parse empty for room=%r. Raw response: %s",
                room_name,
                (response or "")[:500],
            )
            return

        stored = self._store_detected_objects(
            objects,
            position,
            rotation,
            room_name=room_name,
            frame_poses=frame_poses,
            frames=frames,
        )
        logger.info(
            "[VLM] panorama DONE room=%r stored=%d object(s) from %d frame(s)",
            room_name,
            stored,
            n,
        )

    @skill
    def detect_objects_in_view(self) -> str:
        """检测当前相机画面中可命名的物体, 并存入地标记忆.

        这是一个 @skill 方法, 暴露给 LLM agent 调用. 检测到的物体以中文名存储,
        后续可通过 navigate_with_text("电脑") 或 navigate_to_landmark("电脑") 导航.

        Returns:
            检测到的物体名称列表(逗号分隔), 或状态消息(无图像/无物体/失败等).
        """
        if not self._skill_started:
            raise ValueError(f"{self} has not been started.")
        if self._latest_image is None:
            return "No camera image available."
        if not hasattr(self._latest_image, "data"):
            return "Camera image has no pixel data."

        logger.info(
            "[detect_objects_in_view] START image_shape=%s",
            getattr(self._latest_image.data, "shape", None),
        )
        try:
            response = self._vlm_query_all_images(
                [self._latest_image.data], _VLM_OBJECT_LIST_PROMPT
            )
        except Exception as exc:
            logger.exception("[detect_objects_in_view] VLM call failed")
            return f"VLM call failed: {exc}"

        objects = self._parse_vlm_object_list_response(response)
        if not objects:
            return f"No objects parsed from VLM response: {(response or '')[:200]}"

        pos = self._latest_odom.position if self._latest_odom else None
        euler_tuple = self._odom_euler_tuple()
        position = (float(pos.x), float(pos.y), float(pos.z)) if pos else (0.0, 0.0, 0.0)
        rotation = euler_tuple if euler_tuple else (0.0, 0.0, 0.0)
        stored = self._store_detected_objects(
            objects,
            position,
            rotation,
            frames=[self._latest_image.data],
            frame_poses=[(position, rotation)],
        )
        names = [(o.get("name") or "").strip() for o in objects if (o.get("name") or "").strip()]

        if stored:
            return f"Detected {stored} object(s): {', '.join(names)}. Stored in landmark memory."
        return "No nameable objects detected."

    def _bbox_reasonable_for_tracking(self, bbox: BBox) -> bool:
        """检查 bbox 尺寸是否适合用于视觉跟踪.

        过小的 bbox(小于 24px)跟踪器容易丢失; 过大的 bbox(占画面 55% 以上)
        通常是把背景或整个家具圈进去了, 跟踪意义不大. 没有图像数据时默认放行,
        交给下游处理.

        Args:
            bbox: [x1, y1, x2, y2] 像素坐标.

        Returns:
            True 如果 bbox 尺寸在合理跟踪范围内.
        """
        if self._latest_image is None or not hasattr(self._latest_image, "data"):
            return True
        h, w = self._latest_image.data.shape[:2]
        x1, y1, x2, y2 = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
        bw, bh = x2 - x1, y2 - y1
        # 太小: 跟踪器容易跟丢
        if bw < 24 or bh < 24:
            return False
        # 太大: 大概率圈了背景, 跟踪无意义
        if bw * bh > 0.55 * w * h:
            return False
        return True

    def _get_bbox_for_current_frame(self, query: str) -> BBox | None:
        """获取当前帧中目标物体的 bbox.

        调用 VLM 在最新相机帧中定位指定物体, 返回其边界框.
        用于视觉锁定前的目标定位.

        Args:
            query: 目标物体的名称(中文).

        Returns:
            bbox [x1, y1, x2, y2] 像素坐标, 或 None(无图像/VLM 未找到).
        """
        if self._latest_image is None:
            return None

        return get_object_bbox_from_image(self._vl_model, self._latest_image, query)

    def _query_memory_images_with_vlm(self, query: str) -> str | None:
        """方式一: 让 VLM 检查已存储的记忆图片, 查找目标物体.

        从两个来源收集候选图片:
        - Source A: 空间记忆中按文本检索的帧(CLIP 语义匹配 top-3)
        - Source B: 每个房间的参考图片(每房间取一张保证多样性)

        将所有候选图片打包成一次 VLM 批量请求, 让模型判断哪些图片包含目标物体,
        然后导航到第一个确认的位置. 到达后尝试视觉锁定.

        Args:
            query: 目标物体名称(中文).

        Returns:
            导航结果描述, 或 None(无候选/VLM 未确认/导航失败).
        """
        import numpy as np

        candidates: list[
            tuple[np.ndarray, dict[str, Any], str]
        ] = []  # (image, metadata, source_tag)

        # Source A: 从空间记忆中按文本语义检索相关帧(CLIP top-3)
        try:
            spatial_results = self._spatial_memory.query_by_text_with_images(query, limit=3)
            logger.info("[L5]   Source A (CLIP spatial): %d frames retrieved", len(spatial_results))
            for r in spatial_results:
                img = r.get("image")
                meta = r.get("metadata") or {}
                if img is not None:
                    candidates.append((img, meta, "spatial"))
        except Exception:
            logger.exception("[L5]   query_by_text_with_images failed")

        # Source B: 房间参考图片(每房间取一张, 保证场景多样性)
        try:
            room_list = self._spatial_memory.get_room_images()
            seen_rooms: set[str] = set()
            for room_info in room_list:
                room_name = str(room_info.get("name", ""))
                if room_name in seen_rooms:
                    continue
                seen_rooms.add(room_name)
                images: list[Any] = cast("list[Any]", room_info.get("images") or [])
                if not images:
                    continue
                first_img: dict[str, Any] = images[0] if isinstance(images[0], dict) else {}
                first_img_id = str(first_img.get("location_id", ""))
                if not first_img_id:
                    continue
                room_img = self._spatial_memory.get_room_image(first_img_id)
                if room_img is not None:
                    room_meta: dict[str, Any] = {"room_name": room_name}
                    room_rec = self._resolve_room_landmark(room_name)
                    if room_rec is not None:
                        room_meta["pos_x"] = room_rec.position[0]
                        room_meta["pos_y"] = room_rec.position[1]
                    candidates.append((room_img, room_meta, "room"))
            logger.info("[L5]   Source B (room refs): %d unique rooms added", len(seen_rooms))
        except Exception:
            logger.exception("[L5]   room image retrieval failed")

        if not candidates:
            logger.info("[L5]   ✗ No candidate images available")
            return None

        logger.info(
            "[L5]   Total candidates: %d (spatial=%d, room=%d) — sending batch VLM query ...",
            len(candidates),
            sum(1 for _, _, s in candidates if s == "spatial"),
            sum(1 for _, _, s in candidates if s == "room"),
        )

        # 单次批量 VLM 调用: "这 N 张图片中哪些包含目标物体?"
        index_labels = "\n".join(
            f"Image {i}: source={src}, meta={meta}"
            for i, (_img, meta, src) in enumerate(candidates)
        )
        batch_prompt = (
            f"You are shown {len(candidates)} images numbered 0 to {len(candidates) - 1}.\n"
            f"{index_labels}\n"
            f"For each image that contains '{query}', output its index number.\n"
            f"Return ONLY a JSON array of indices, e.g. [0, 2]. If none contain it, return []."
        )

        try:
            response_text = self._vlm_query_all_images(
                [img for img, _meta, _src in candidates], batch_prompt
            )
        except Exception:
            logger.exception("VLM batch memory query failed")
            return None

        matched_indices = extract_json_from_llm_response(response_text)
        if not isinstance(matched_indices, list) or not matched_indices:
            logger.info("[L5]   ✗ VLM batch: '%s' not confirmed in any candidate", query)
            return None
        logger.info("[L5]   ✓ VLM batch confirmed '%s' in indices: %s", query, matched_indices)

        # 导航到第一个确认包含目标的候选位置
        try:
            idx = int(matched_indices[0])
        except (ValueError, TypeError):
            logger.warning("VLM batch query: non-numeric index %r", matched_indices[0])
            return None
        if idx < 0 or idx >= len(candidates):
            logger.warning("VLM batch query: index %d out of range", idx)
            return None

        _img, meta, source = candidates[idx]
        logger.info(
            "VLM confirmed '%s' in %s image #%d (meta=%s)",
            query,
            source,
            idx,
            meta,
        )

        if source == "room":
            # 来源是房间参考图: VLM 在某个房间的参考图里发现了目标物体,
            # 但房间图只携带房间名, 没有物体坐标, 所以策略是:
            # 先导航到房间锚点, 到达后再做局部视觉搜索
            room_name = str(meta.get("room_name", ""))
            if room_name:
                room_rec = self._resolve_room_landmark(room_name)
                if room_rec is not None:
                    logger.info(
                        "[vlm_memory] '%s' in room '%s' → navigate to room then re-scan",
                        query,
                        room_name,
                    )
                    # 导航到房间锚点但不执行到达动作, 也不做视觉漂移修正
                    # 因为这里只是先到房间, 真正的目标是物体而非房间本身
                    nav_msg = self._navigate_to_landmark(
                        room_rec,
                        arrival_action="stop",
                        arrival_distance=0.6,
                        run_arrival_action=False,
                        enable_visual_drift=False,
                    )
                    # 若房间导航本身因严重漂移失败, 则放弃后续物体搜索
                    if "severe visual/odom drift" in nav_msg.lower():
                        logger.warning("[vlm_memory] drift at room '%s'", room_name)
                        return None
                    # 若物体之前被标记过且有存储朝向 (yaw), 优先用存储朝向做视觉获取
                    obj_rec = self._resolve_landmark_from_query(query)
                    stored_yaw: float | None = None
                    if obj_rec is not None and obj_rec.record_type == RecordType.LANDMARK:
                        # rotation[2] 是 yaw; 接近 0 视为未记录过朝向
                        stored_yaw = (
                            obj_rec.rotation[2] if abs(obj_rec.rotation[2]) > 1e-6 else None
                        )
                    if stored_yaw is not None:
                        vis_msg = self._visual_acquire_object(query, stored_yaw)
                        if vis_msg:
                            return vis_msg
                    # 无存储朝向时, 在当前位置做有限时长的物体搜索
                    found = self._navigate_to_object(query, timeout=15.0)
                    if found:
                        return found
                    return (
                        f"VLM found '{query}' in room '{room_name}' image; "
                        f"reached the room but could not track '{query}' in view. {nav_msg}"
                    )

        # 非 room 来源: 候选图携带了 pos_x/pos_y 坐标, 可以直接导航到该位置
        if "pos_x" not in meta or "pos_y" not in meta:
            logger.warning(
                "[vlm_memory] no coordinates for '%s' (source=%s meta=%s)",
                query,
                source,
                meta,
            )
            return None
        pos_x = float(meta["pos_x"])
        pos_y = float(meta["pos_y"])

        goal_pose = PoseStamped(
            position=make_vector3(pos_x, pos_y, 0.0),
            orientation=Quaternion.from_euler(Vector3(0.0, 0.0, 0.0)),
            frame_id="map",
        )
        self._navigation.set_goal(goal_pose)
        # 阻塞等待到达, 最长 120 秒
        deadline = time.time() + 120.0
        arrived = False
        while time.time() < deadline:
            if self._navigation.is_goal_reached():
                arrived = True
                break
            time.sleep(0.5)
        prefix = (
            f"Found '{query}' via VLM batch inspection of {len(candidates)} stored images "
            f"(source={source}, index={idx})."
        )
        if not arrived:
            return f"{prefix} Navigation started but did not reach target within timeout."
        # 到达后, 若物体有存储朝向, 再尝试视觉获取确认物体在视野中
        obj_rec = self._resolve_landmark_from_query(query)
        if obj_rec is not None and obj_rec.record_type == RecordType.LANDMARK:
            stored_yaw = obj_rec.rotation[2] if abs(obj_rec.rotation[2]) > 1e-6 else None
            vis_msg = self._visual_acquire_object(query, stored_yaw)
            if vis_msg:
                return f"{prefix} ({vis_msg})"
        return prefix

    def _navigate_using_semantic_map(self, query: str) -> str | None:
        """通过 CLIP 语义检索在空间记忆中查找与查询文本最匹配的位置并导航过去.

        这是 navigate_with_text 多级 fallback 链路中的一环 (Level 6):
        直接用 CLIP 文本编码器在已存储的空间记忆帧中做相似度搜索,
        取 top-1 结果作为导航目标. 相似度低于阈值时返回 None, 由上层
        尝试其他策略.

        Args:
            query: 自然语言描述, 例如 "红色的沙发" 或 "厨房门口".

        Returns:
            导航结果消息字符串, 或 None 表示语义匹配未达阈值.
        """
        results = self._spatial_memory.query_by_text(query)

        if not results:
            logger.info("[L6]   CLIP text search returned 0 results")
            return None

        # CLIP 距离越小越相似, similarity = 1 - distance
        best_match = results[0]
        dist = best_match.get("distance", 1.0)
        similarity = 1.0 - dist
        logger.info("[L6]   Top CLIP match: distance=%.4f similarity=%.4f", dist, similarity)

        # _get_goal_pose_from_result 内部会检查相似度阈值, 不达标返回 None
        goal_pose = self._get_goal_pose_from_result(best_match)

        logger.info("[L6]   Goal pose: %s", goal_pose)
        if not goal_pose:
            logger.info(
                "[L6]   ✗ Similarity below threshold (%.4f < %.4f)",
                similarity,
                self._similarity_threshold,
            )
            return None

        message = f"Found a location in the semantic map matching '{query}'."
        return self._navigate_to(goal_pose, message)

    @skill
    def stop_navigation(self) -> str:
        """立即停止当前导航目标, 取消正在执行的路径规划.

        仅取消导航目标, 不会停止物体跟踪或前沿探索等其他子系统.
        如需全面停止请使用 stop_all_motion.
        """

        if not self._skill_started:
            raise ValueError(f"{self} has not been started.")

        self._cancel_active_object_search()
        self._cancel_goal_and_stop()

        return "Stopped"

    @skill
    def stop_all_motion(self) -> str:
        """全面停止所有运动子系统并让机器人恢复稳定站立姿态.

        依次停止: 导航目标 -> 前沿探索 -> 物体跟踪 -> 执行 RecoveryStand.
        每个子系统的停止都做了 try/except 保护, 单个子系统失败不会
        阻止其他子系统的停止, 确保在异常状态下也能尽可能恢复.
        """

        if not self._skill_started:
            raise ValueError(f"{self} has not been started.")

        self._cancel_active_object_search()
        self._cancel_goal_and_stop()
        exploration_msg = ""
        if self._frontier_explorer is not None:
            try:
                exploration_msg = self._frontier_explorer.end_exploration()
            except Exception:
                logger.exception("Failed to stop exploration during stop_all_motion")
                exploration_msg = "Failed to stop exploration."

        if self._object_tracking is not None:
            try:
                self._object_tracking.stop_track()
            except Exception:
                logger.exception("Failed to stop object tracking during stop_all_motion")

        recovery_msg = ""
        if self._unitree_skill_container is not None:
            try:
                # RecoveryStand 让 Go2 从趴下/侧翻等不稳定姿态恢复到稳定站立
                recovery_msg = self._unitree_skill_container.execute_sport_command("RecoveryStand")
            except Exception:
                logger.exception("Failed to execute RecoveryStand during stop_all_motion")
                recovery_msg = "RecoveryStand failed."

        return (
            "Stopped navigation and tracking. "
            + (f"Exploration: {exploration_msg} " if exploration_msg else "")
            + (f"Recovery: {recovery_msg}" if recovery_msg else "No Unitree recovery module wired.")
        )

    @skill
    def emergency_stop(self) -> str:
        """紧急停止, 等同于 stop_all_motion.

        作为语义别名暴露给 LLM agent, 当用户说 "紧急停止" / "emergency"
        时 agent 会选择这个 skill, 避免因措辞差异导致无法触发.
        """
        return self.stop_all_motion()

    @skill
    def stop_movement(self) -> str:
        """停止移动, 等同于 stop_all_motion.

        作为语义别名暴露给 LLM agent, 当用户说 "停下" / "别动了"
        时 agent 会选择这个 skill.
        """
        return self.stop_all_motion()

    @skill
    def find_room_visually(self) -> str:
        """通过视觉相似度 (CLIP) 识别机器人当前所在的房间.

        将当前摄像头画面与已存储的房间参考图做 CLIP 嵌入比对,
        纯视觉匹配, 不依赖任何坐标或 SLAM 数据, 因此在机器人重启
        或 SLAM 重置后仍然有效.

        适用场景: 用户问 "我在哪个房间?" / "这是哪里?", 或在重启后
        需要重新定位时调用.

        Returns:
            str: 房间名及置信度, 或未找到匹配时的提示信息.
        """
        if not self._skill_started:
            raise ValueError(f"{self} has not been started.")

        if self._latest_image is None:
            return "No camera image available yet — cannot identify room visually."

        try:
            if hasattr(self._latest_image, "data"):
                frame = self._latest_image.data
            else:
                return "Camera image has no pixel data."

            result = self._spatial_memory.query_location_by_image(frame)
            if result is None:
                return "No matching room found. Try recording rooms first with tag_location."

            # CLIP distance > 0.3 视为低置信度, 不宜直接报告房间名
            distance = result.metadata.get("distance", 1.0)
            if distance > 0.3:
                return (
                    f"Closest match is '{result.name}' but confidence is low "
                    f"(distance={distance:.3f}). Try moving to a more distinctive viewpoint."
                )

            return (
                f"You are in '{result.name}' "
                f"(confidence: {(1.0 - distance) * 100:.0f}%, distance={distance:.3f})."
            )
        except Exception as e:
            logger.exception("find_room_visually failed")
            return f"Error identifying room: {e}"

    @skill
    def navigate_to_landmark(
        self,
        name: str,
        arrival_action: str = "stop",
        arrival_distance: float = 0.5,
    ) -> str:
        """导航到已记录的 landmark (房间或物体) 位置.

        通过 landmark memory 查找目标位置, 然后基于拓扑图规划经过已知
        waypoint 的最短路径. 到达后可选择执行 Unitree sport 动作.

        对于物体类 landmark, 若到达后视觉获取失败, 会自动 fallback
        到逐房间扫描搜索.

        Args:
            name: landmark 名称 (支持中文).
            arrival_action: 到达后执行的动作, 可选 ``stop`` | ``sit`` | ``wave`` | ``point``.
            arrival_distance: 到达判定距离 (米), 机器人距目标此距离时触发 arrival_action.
        """
        if not self._skill_started:
            raise ValueError(f"{self} has not been started.")

        target = self._landmark_memory.resolve_by_query(name)
        if target is None:
            return (
                f"No landmark found matching '{name}'. "
                f"Use query_landmarks to see available landmarks (names are in Chinese)."
            )
        target = self._record_for_current_world(target)
        if target is None:
            return (
                f"No landmark found matching '{name}' on the currently loaded map. "
                "Use query_landmarks to inspect stored landmarks or re-tag the room."
            )

        # 物体类 landmark 默认用 "point" (指向) 而非 "stop", 因为对物体来说
        # 到达后指向物体比单纯停下更有意义; 房间类 landmark 保持用户指定的 action
        effective_action = (
            "point"
            if target.record_type == RecordType.LANDMARK and arrival_action == "stop"
            else arrival_action
        )

        result = self._navigate_to_landmark(
            target,
            arrival_action=effective_action,
            arrival_distance=arrival_distance,
            run_arrival_action=True,
        )

        # 自动 fallback: 若是物体 landmark 且到达后视觉获取失败,
        # 则逐个房间扫描搜索该物体, 而非直接放弃
        if (
            target.record_type == RecordType.LANDMARK
            and "could not visually acquire" in result.lower()
        ):
            logger.info(
                "[navigate_to_landmark] Visual acquire failed for %r, falling back to room sweep",
                name,
            )
            # 初始化 skip 集合, 把已搜索过的房间排除掉, 避免重复搜索
            self._sweep_skip_rooms = set()
            meta = target.metadata or {}
            room = meta.get("room_name")
            if not room:
                # metadata 里没有房间名时, 用坐标反查当前所在房间
                room = self._room_name_at_position(target.position[0], target.position[1])
            if room:
                self._sweep_skip_rooms.add(str(room))
                logger.info(
                    "[navigate_to_landmark] Room %r already searched — room_sweep will skip it",
                    room,
                )

            sweep_result = self._room_anchor_sweep_for_object(name)
            if sweep_result:
                return f"{self._run_arrival_action(effective_action, name)} ({sweep_result})"
            return f"{result} (fallback: swept all rooms — '{name}' not found in any known room)"

        return result

    def _navigate_to_landmark(
        self,
        target: SpatialRecord,
        *,
        arrival_action: str = "stop",
        arrival_distance: float = 0.5,
        run_arrival_action: bool = True,
        relocalize_interval: float | None = None,
        enable_visual_drift: bool = False,
        search_context: _ObjectSearchContext | None = None,
    ) -> str:
        """导航到指定 landmark 的内部实现.

        整体流程: 拓扑路径规划 (经过已知 waypoint) -> 逐段导航 ->
        最终接近 (standoff) -> 视觉确认 (物体类) -> 执行到达动作.

        若首次尝试因严重漂移失败, 会从当前 odom 位置重新规划拓扑路径
        再试一次. 物体类 landmark 不做视觉漂移修正, 因为其坐标来自
        VLM 捕获位姿, 与房间 CLIP 参考无关.

        Args:
            target: 目标 SpatialRecord (房间或物体).
            arrival_action: 到达后执行的动作.
            arrival_distance: 到达判定距离 (米).
            run_arrival_action: 是否在到达后执行 arrival_action.
            relocalize_interval: 视觉重定位间隔 (秒), None 用默认值.
            enable_visual_drift: 是否开启视觉漂移修正.
            search_context: 寻物任务上下文; 提供时在导航途中异步检测目标.
        """
        # 检查目标的坐标系是否已过期 (如 SLAM 重置后旧坐标失效)
        stale_reason = self._coordinate_frame_stale_reason(target)
        if stale_reason:
            logger.warning("Skipping navigation to '%s': %s", target.name, stale_reason)
            self._navigation.cancel_goal()
            return f"Navigation skipped for '{target.name}': {stale_reason}"

        interval = (
            relocalize_interval if relocalize_interval is not None else self._relocalize_interval_s
        )
        is_object_landmark = target.record_type == RecordType.LANDMARK
        if is_object_landmark:
            # 物体坐标来自 VLM 捕获时的位姿, 与房间 CLIP 参考无关, 不做视觉漂移修正
            enable_visual_drift = False
            expected_room = None
        elif target.record_type == RecordType.ROOM:
            # 房间类 landmark: 期望房间就是目标本身
            expected_room = target.name
        else:
            # 其他类型: 用坐标反查所属房间名, 用于漂移检测时的期望房间匹配
            expected_room = self._room_name_at_position(target.position[0], target.position[1])
        self._begin_search_leg(search_context, target.name or target.record_id)

        def _try_navigate() -> str | None:
            """单次导航尝试: 拓扑路径遍历 + 最终接近.

            成功返回 None, 严重漂移时返回错误字符串.
            会被外层调用最多两次 (首次 + 漂移恢复后重试).
            """
            nonlocal interval
            # 用当前 world 的所有 landmark 构建拓扑图, 用于规划最短路径
            all_records = self._records_for_current_world(self._landmark_memory.get_all())
            topo = TopologyGraph()
            for r in all_records:
                topo.add_record(r)

            all_waypoints: list[Any] = []
            if self._latest_odom is not None:
                pos = self._latest_odom.position
                all_waypoints = topo.shortest_path(
                    float(pos.x), float(pos.y), target.position[0], target.position[1]
                )
                logger.info(
                    "[L3]   Topology: %d waypoints from (%.2f, %.2f) → '%s' (%.2f, %.2f)",
                    len(all_waypoints),
                    pos.x,
                    pos.y,
                    target.name,
                    target.position[0],
                    target.position[1],
                )

            last_corr = [0.0]
            final_dest = PoseStamped(
                position=make_vector3(target.position[0], target.position[1], target.position[2]),
                orientation=Quaternion.from_euler(Vector3(0.0, 0.0, 0.0)),
                frame_id="map",
            )

            for i, wp in enumerate(all_waypoints):
                logger.info(
                    "[L3]   Waypoint %d/%d: '%s' at (%.2f, %.2f)",
                    i + 1,
                    len(all_waypoints),
                    wp.name,
                    wp.x,
                    wp.y,
                )
                segment_goal = PoseStamped(
                    position=make_vector3(wp.x, wp.y, 0.0),
                    orientation=Quaternion.from_euler(Vector3(0.0, 0.0, 0.0)),
                    frame_id="map",
                )
                self._navigation.set_goal(segment_goal)
                deadline = time.time() + 120.0
                _, severe = self._wait_goal_with_relocalize(
                    segment_goal,
                    last_corr,
                    deadline,
                    interval,
                    destination_pose=final_dest,
                    expected_room_name=expected_room,
                    enable_visual_drift=enable_visual_drift,
                    search_context=search_context,
                )
                if severe:
                    return (
                        "Navigation aborted: severe visual/odom drift detected "
                        "(>1m vs room reference). Retry navigate_with_text or re-tag rooms."
                    )

            if is_object_landmark:
                # 物体: standoff 直接在物体位置, 朝向用存储的 yaw; 无存储 yaw 时朝向物体
                obj_yaw = target.rotation[2]
                if abs(obj_yaw) < 1e-6:
                    obj_yaw = self._yaw_toward_point(target.position[0], target.position[1])
                standoff_pose = PoseStamped(
                    position=make_vector3(
                        target.position[0],
                        target.position[1],
                        target.position[2],
                    ),
                    orientation=Quaternion.from_euler(Vector3(0.0, 0.0, obj_yaw)),
                    frame_id="map",
                )
            else:
                # 房间/其他: 在目标前方 1.5m 处 standoff, 朝向目标 (yaw 方向)
                # 这样机器人到达时是面向目标而非站在目标正上方
                yaw = target.rotation[2]
                offset_x = 1.5 * math.cos(yaw)
                offset_y = 1.5 * math.sin(yaw)
                standoff_pose = PoseStamped(
                    position=make_vector3(
                        target.position[0] + offset_x,
                        target.position[1] + offset_y,
                        target.position[2],
                    ),
                    orientation=Quaternion.from_euler(Vector3(0.0, 0.0, yaw)),
                    frame_id="map",
                )

            approach_deadline = time.time() + 180.0
            _last_logged_dist = float("inf")
            dist = float("inf")
            _last_active_goal_pos: tuple[float, float, float] | None = None
            _min_replan_interval = 3.0
            _last_replan_time = 0.0
            _stuck_replan_streak = 0
            _stuck_position: tuple[float, float] | None = None
            # 物体 landmark 至少保持 0.85m 距离, 避免撞到物体本身
            effective_arrival_distance = (
                max(arrival_distance, 0.85) if is_object_landmark else arrival_distance
            )
            while time.time() < approach_deadline:
                dist = self._planar_distance_to_pose(standoff_pose)
                if abs(dist - _last_logged_dist) > 0.15:
                    logger.info(
                        "Approach '%s': distance=%.2fm (target≤%.2f) → %s",
                        target.name,
                        dist,
                        effective_arrival_distance,
                        "coarse" if dist > 1.5 else "decelerating",
                    )
                    _last_logged_dist = dist
                if dist <= effective_arrival_distance:
                    break
                if dist > 1.5:
                    # 远距离: 直接导航到 standoff_pose
                    active_goal = standoff_pose
                else:
                    # 近距离: 逐步微调目标位置 (inching), 避免过冲
                    inch = self._inch_goal_toward(standoff_pose, effective_arrival_distance)
                    if inch is None:
                        # 已在 arrival_distance + 2cm 内, 足够近, 停止微调
                        break
                    active_goal = inch

                cur_pos = (
                    float(active_goal.position.x),
                    float(active_goal.position.y),
                    float(active_goal.position.z),
                )
                goal_changed = (
                    _last_active_goal_pos is None
                    or math.hypot(
                        cur_pos[0] - _last_active_goal_pos[0],
                        cur_pos[1] - _last_active_goal_pos[1],
                    )
                    > 0.05
                )
                # 限频重新规划: 目标变化或导航空闲时才重新 set_goal, 最少间隔 3 秒
                nav_idle = self._navigation.get_state() == NavigationState.IDLE
                since_last_replan = time.time() - _last_replan_time
                if (goal_changed or nav_idle) and since_last_replan >= _min_replan_interval:
                    self._navigation.set_goal(active_goal)
                    _last_active_goal_pos = cur_pos
                    _last_replan_time = time.time()

                # Wait until arrival, severe drift, or the remaining approach window.
                wait_deadline = min(time.time() + 30.0, approach_deadline)
                _, severe = self._wait_goal_with_relocalize(
                    active_goal,
                    last_corr,
                    wait_deadline,
                    interval,
                    destination_pose=standoff_pose,
                    expected_room_name=expected_room,
                    enable_visual_drift=enable_visual_drift,
                    search_context=search_context,
                )
                if severe:
                    return (
                        "Navigation aborted: severe visual/odom drift detected "
                        "(>1m vs room reference). Retry navigate_with_text or re-tag rooms."
                    )
                dist = self._planar_distance_to_pose(standoff_pose)
                if is_object_landmark and self._navigation.is_goal_reached() and dist <= 1.0:
                    logger.info(
                        "Accepting object '%s' arrival at %.2fm: planner reached safe standoff",
                        target.name,
                        dist,
                    )
                    break

                # 卡住检测: 机器人靠近目标但反复重规划 (遇障碍/偏航) 且无进展时,
                # 连续 5 次卡在同一位置则接受当前位置 - 够近了
                if self._navigation.get_state() == NavigationState.IDLE and dist <= 2.0:
                    cur_pos_2d = (
                        (
                            self._latest_odom.position.x,
                            self._latest_odom.position.y,
                        )
                        if self._latest_odom
                        else None
                    )
                    if _stuck_position is None:
                        _stuck_position = cur_pos_2d
                        _stuck_replan_streak = 1
                    elif (
                        cur_pos_2d
                        and math.hypot(
                            cur_pos_2d[0] - _stuck_position[0],
                            cur_pos_2d[1] - _stuck_position[1],
                        )
                        < 0.3
                    ):
                        _stuck_replan_streak += 1
                    else:
                        _stuck_position = cur_pos_2d
                        _stuck_replan_streak = 1

                    if _stuck_replan_streak >= 5:
                        logger.info(
                            "Stuck near '%s' after %d replans at %.2fm — accepting position",
                            target.name,
                            _stuck_replan_streak,
                            dist,
                        )
                        break

            self._navigation.cancel_goal()
            if dist > effective_arrival_distance:
                stale_reason = self._coordinate_frame_stale_reason(target)
                if stale_reason:
                    return f"Navigation timed out for '{target.name}': {stale_reason}"
                return (
                    f"Navigation timed out before reaching '{target.name}' "
                    f"(remaining distance {dist:.2f}m)."
                )
            return None  # Success

        # 第一次尝试
        try:
            err = _try_navigate()
        except _EnrouteObjectHitError:
            if search_context is None:
                raise
            return self._finish_enroute_hit(search_context)
        if err is None:
            if search_context is not None and search_context.hit_event.is_set():
                return self._finish_enroute_hit(search_context)
            self._end_search_leg(search_context)
            tname = target.name or target.record_id
            vis_msg = ""
            if is_object_landmark:
                stored_yaw = target.rotation[2] if abs(target.rotation[2]) > 1e-6 else None
                vis_msg = self._visual_acquire_object(tname, stored_yaw) or ""
                if vis_msg:
                    logger.info("[L3] Visual acquire: %s", vis_msg)
                else:
                    logger.warning("[L3] Visual acquire: '%s' not found in view", tname)
            if run_arrival_action and vis_msg:
                return f"{self._run_arrival_action(arrival_action, tname)} ({vis_msg})"
            if is_object_landmark:
                base = f"Arrived near '{tname}' standoff"
                if vis_msg:
                    return f"{base} ({vis_msg})"
                return f"{base} (could not visually acquire '{tname}'; arrival_action not run)."
            if run_arrival_action:
                return self._run_arrival_action(arrival_action, tname)
            base = f"Arrived near '{tname}' standoff"
            if vis_msg:
                return f"{base} ({vis_msg})"
            return f"{base} (arrival_action not run)."

        # 严重漂移恢复: 从当前 odom 位置重新规划拓扑路径再试一次
        logger.warning(
            "Severe drift on first attempt; re-planning topology from current odom for retry"
        )
        self._navigation.cancel_goal()
        time.sleep(0.5)

        try:
            err2 = _try_navigate()
        except _EnrouteObjectHitError:
            if search_context is None:
                raise
            return self._finish_enroute_hit(search_context)
        if err2 is None:
            if search_context is not None and search_context.hit_event.is_set():
                return self._finish_enroute_hit(search_context)
            self._end_search_leg(search_context)
            tname = target.name or target.record_id
            vis_msg = ""
            if is_object_landmark:
                stored_yaw = target.rotation[2] if abs(target.rotation[2]) > 1e-6 else None
                vis_msg = self._visual_acquire_object(tname, stored_yaw) or ""
                if vis_msg:
                    logger.info("[L3] Visual acquire (retry): %s", vis_msg)
                else:
                    logger.warning("[L3] Visual acquire (retry): '%s' not found in view", tname)
            if run_arrival_action and vis_msg:
                return f"{self._run_arrival_action(arrival_action, tname)} ({vis_msg})"
            if is_object_landmark:
                base = f"Arrived near '{tname}' standoff after drift recovery"
                if vis_msg:
                    return f"{base} ({vis_msg})"
                return f"{base} (could not visually acquire '{tname}'; arrival_action not run)."
            if run_arrival_action:
                return self._run_arrival_action(arrival_action, tname)
            base = f"Arrived near '{tname}' standoff after drift recovery"
            if vis_msg:
                return f"{base} ({vis_msg})"
            return f"{base} (arrival_action not run)."

        self._end_search_leg(search_context)
        return (
            "Navigation aborted: severe visual/odom drift persisted after re-plan. "
            "Retry navigate_with_text or re-tag rooms."
        )

    @skill
    def clear_all_memory(self) -> str:
        """Clear all landmark and spatial memory (rooms, objects, CLIP images).

        Use before a fresh mapping session. Does not require restart.

        清空所有空间记忆, 包括 landmark 记忆和空间记忆.

        适用场景: 开始一次全新的建图任务前调用, 避免旧记忆污染新地图.
        无需重启进程, 清空后立即生效.
        """
        if not self._skill_started:
            raise ValueError(f"{self} has not been started.")

        # landmark 记忆和空间记忆是两套独立存储, 需要分别清空.
        # 空间记忆可能没有实现 clear_all 接口, 用 hasattr 做防御性检查.
        lm_n = self._landmark_memory.clear_all()
        spatial_stats: dict[str, int] = {}
        if hasattr(self._spatial_memory, "clear_all"):
            spatial_stats = self._spatial_memory.clear_all()
        logger.info(
            "[clear_all_memory] landmarks=%d spatial=%s",
            lm_n,
            spatial_stats,
        )
        # 返回给 LLM agent 的清空结果摘要, 让 agent 知道实际删除了多少条记录
        return (
            f"Cleared memory: {lm_n} landmark(s) removed; "
            f"spatial memory: {spatial_stats or 'unchanged'}."
        )

    @skill
    def query_landmarks(self, query: str) -> str:
        """Search landmarks — rooms, objects, or by name.

        Use ``"all"`` to list everything, ``"rooms"`` for rooms only,
        ``"objects"`` for VLM-detected objects, or any text to search by name.
        ``"open"`` / ``"closed"`` to filter by door state.

        Args:
            query: "all" | "rooms" | "objects" | "open" | "closed" | name search

        查询已存储的 landmark, 支持按类型, 名称, 门状态等多种方式检索.

        这是暴露给 LLM agent 的 @skill 接口, agent 通过自然语言查询来感知
        周围有哪些房间和物体, 从而决定下一步导航目标.
        """
        if not self._skill_started:
            raise ValueError(f"{self} has not been started.")

        from dimos.types.spatial_record import RecordType

        # 关键词分多级匹配: 先匹配预定义类别, 再退回到模糊文本搜索.
        # 这样 LLM 用简单的 "rooms" / "all" 就能精确检索, 用自然语言则走语义搜索.
        query_lower = query.lower().strip()
        if query_lower in ("all", "list all", "all landmarks"):
            records = self._landmark_memory.get_all()
        elif query_lower in ("rooms", "all rooms"):
            records = self._landmark_memory.query_by_type(RecordType.ROOM)
        elif query_lower in ("landmarks", "objects", "all objects"):
            records = self._landmark_memory.query_by_type(RecordType.LANDMARK)
        elif query_lower in ("open", "opened"):
            records = self._landmark_memory.query_by_state("open")
        elif query_lower in ("closed", "close"):
            records = self._landmark_memory.query_by_state("closed")
        else:
            hit = self._landmark_memory.resolve_by_query(query)
            if hit is not None:
                records = [hit]
            else:
                records = self._landmark_memory.query_by_text(query, limit=10)

        # 按 world_id 过滤, 只返回当前地图/会话中的 landmark.
        # 切换地图后旧 landmark 不应出现在查询结果里, 否则导航会指向错误的坐标.
        records = self._records_for_current_world(records)
        if not records:
            return (
                f"No landmarks found matching '{query}' on the current map/session. "
                f"Try query_landmarks objects or use navigate_with_text."
            )

        # 按类型分组展示, 让 LLM 更容易解析结果结构, 避免长列表混在一起难以区分
        rooms = [r for r in records if r.record_type == RecordType.ROOM]
        objects = [r for r in records if r.record_type == RecordType.LANDMARK]
        other = [r for r in records if r.record_type not in (RecordType.ROOM, RecordType.LANDMARK)]

        lines = [f"Found {len(records)} landmark(s):"]
        if rooms:
            lines.append(f"  ── Rooms ({len(rooms)}) ──")
            for r in rooms:
                lines.append(
                    f"  [room] {r.name} at ({r.position[0]:.1f}, {r.position[1]:.1f}) · seen {r.observation_count}x"
                )
        if objects:
            lines.append(f"  ── Objects ({len(objects)}) ──")
            for r in objects:
                desc = f" — {r.state}" if r.state else ""
                lines.append(
                    f"  [obj]  {r.name}{desc} at ({r.position[0]:.1f}, {r.position[1]:.1f}) · seen {r.observation_count}x"
                )
        if other:
            lines.append(f"  ── Other ({len(other)}) ──")
            for r in other:
                state_str = f" ({r.state})" if r.state else ""
                lines.append(
                    f"  [{r.record_type.value}] {r.name}{state_str} at ({r.position[0]:.1f}, {r.position[1]:.1f})"
                )
        return "\n".join(lines)

    def _cancel_goal_and_stop(self) -> None:
        """取消当前导航目标, 让机器人停止移动.

        内部辅助方法, 不暴露给 LLM. 在导航异常退出或需要紧急停止时调用,
        确保导航栈不会继续执行上一个未完成的目标.
        """
        self._navigation.cancel_goal()

    def _get_goal_pose_from_result(self, result: dict[str, Any]) -> PoseStamped | None:
        """从视觉检索结果中提取目标位姿.

        将 CLIP/向量检索返回的匹配结果转换为导航可用的 PoseStamped.
        如果匹配相似度低于阈值则返回 None, 表示虽然找到了匹配但不可靠,
        不应作为导航目标使用.
        """
        # result["distance"] 是向量距离 (越小越相似), 转换为相似度分数 (越大越相似)
        similarity = 1.0 - (result.get("distance") or 1)
        if similarity < self._similarity_threshold:
            # 相似度不够时宁可放弃, 也不把不可靠的匹配送给导航栈, 避免导航到错误位置
            logger.warning(
                f"Match found but similarity score ({similarity:.4f}) is below threshold ({self._similarity_threshold})"
            )
            return None

        metadata = result.get("metadata")
        if not metadata:
            return None
        # metadata 是一个列表, 取第一条作为最佳匹配的位姿信息.
        # pos_x/pos_y 是 landmark 在地图坐标系下的 2D 位置, rot_z 是偏航角.
        first = metadata[0]
        pos_x = first.get("pos_x", 0)
        pos_y = first.get("pos_y", 0)
        theta = first.get("rot_z", 0)

        return PoseStamped(
            position=make_vector3(pos_x, pos_y, 0),
            orientation=Quaternion.from_euler(make_vector3(0, 0, theta)),
            frame_id="map",
        )
