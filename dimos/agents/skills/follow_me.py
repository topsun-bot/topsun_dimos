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
"""跟随用户（follow_me）：在人多场景下用 TorchReID 锚定我，跟丢自动描述+原地等。

这玩意儿跟原版 `person_follow` 区别：
- 不需要文字描述也能用（"跟着我" 这种话就行），自动锚定画面里
  最居中且最显眼的人；带描述也支持（"跟那个穿蓝衣服的人"）。
- 用 **TorchReID OSNet** 提 anchor embedding，每帧候选都重算
  embedding 跟 anchor 算余弦相似度做匹配；这样**人流量多的时候**
  谁路过都不会跟错。
- 跟丢不退出 follow，狗**原地等**；丢失 N 秒后用 Qwen VL 看一帧
  描述周围环境，把消息通过 `AgentSpec.add_message` 注入回 agent，
  agent 用 LLM 加工后再发给用户。
- 默认贴近跟：`max_linear=1.2 m/s, max_angular=1.5 rad/s, distance=1.0m`，
  距离死区 ±0.2m（合适距离不前后晃）、后退限速 0.15×max（贴脸才慢退）、
  YOLO conf=0.18（只看到下半身也能框）、ReID 多模版 gallery 自动覆盖姿态变化、
  控制循环 20Hz、angular_gain=1.1（左右摇摆狗立刻甩头跟）。
  室外建议启动时加 `--no-free-avoid`，否则 sport mode 会自己刹。

关键调用链：
    follow_me("可选描述")
        ├─ 拿最近一帧
        ├─ YOLO person detector 找所有人
        ├─ 选 anchor bbox（VL 匹配 / 启发式：最居中+最大）
        ├─ TorchReID 提 anchor embedding
        └─ 启动 follow_loop 后台线程
              ├─ 20Hz 取最新一帧
              ├─ YOLO 找所有人 -> 每个 ReID embed -> cosine sim
              ├─ 选 sim 最高的且超阈值的 -> visual_servo.compute_twist
              │   + 自行做 angular 死区 / EMA 平滑 / 边缘 boost / 距离死区
              └─ 没找到 -> Twist.zero()；
                       丢失 ≥ lost_grace 秒 -> VL 描述环境 -> 主动报告
"""

from __future__ import annotations

import base64
from datetime import datetime
import io
import json
from pathlib import Path
from threading import Event, RLock, Thread
import time
from typing import TYPE_CHECKING, Any

from langchain_core.messages import HumanMessage
import numpy as np
from reactivex.disposable import Disposable
import torch

from dimos.agents.agent_spec import AgentSpec
from dimos.agents.annotation import skill
from dimos.constants import DEFAULT_THREAD_JOIN_TIMEOUT
from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.core.stream import In, Out
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Quaternion import Quaternion
from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.msgs.geometry_msgs.Vector3 import Vector3, make_vector3
from dimos.msgs.sensor_msgs.CameraInfo import CameraInfo
from dimos.msgs.sensor_msgs.Image import Image
from dimos.navigation.base import NavigationState
from dimos.navigation.navigation_spec import NavigationInterfaceSpec
from dimos.navigation.visual_servoing.visual_servoing_2d import VisualServoing2D
from dimos.perception.detection.detectors.person.yolo import YoloPersonDetector
from dimos.robot.unitree.go2.connection_spec import GO2ConnectionSpec
from dimos.utils.logging_config import setup_logger

if TYPE_CHECKING:
    from dimos.models.embedding.treid import TorchReIDModel
    from dimos.models.vl.base import VlModel

logger = setup_logger()

# 默认调参（贴近跟 + 跟头快 + 不那么神经质地后退）
# 这一组对应室内、单人正脸跟。想要快/远跟可以在 humancli 里再让 agent 改参。
DEFAULT_MAX_LINEAR_SPEED: float = 1.2  # m/s；正常步行速度，慢动会跟丢就是因为前向给太低
DEFAULT_MAX_ANGULAR_SPEED: float = 1.5  # rad/s；横向甩头上限，太大变摇头，太小快速横移跟不上
DEFAULT_ANGULAR_GAIN: float = 1.1  # 比例增益；中心区配合死区不抖、边缘区另有 boost
DEFAULT_ANGULAR_DEAD_ZONE: float = (
    0.04  # 横向死区：|x_norm| < 这个不转头（≈中心 ±25 px），消除"原地摇摆"
)
DEFAULT_ANGULAR_SMOOTHING: float = 0.5  # EMA 角速度平滑（α），0.5 = 半数惯性，响应足够快
DEFAULT_ANGULAR_EDGE_THRESHOLD: float = 0.25  # |x_norm| 超这个值就把 gain 临时放大
DEFAULT_ANGULAR_EDGE_BOOST: float = 1.6  # gain 放大倍数，防止"人走快就出画面"
DEFAULT_TARGET_DISTANCE: float = 1.0  # m；过去 0.6 太贴，狗几乎贴在你脚边
DEFAULT_REID_THRESHOLD: float = 0.55  # cosine sim >= 这个才视为同一人。OSNet 工程经验：同人通常 0.55-0.85，异人 0.0-0.5，0.55 是合理分界
DEFAULT_LOST_GRACE_SECONDS: float = (
    5.0  # 连续丢几秒才发"看不见你"消息（原 2s 太敏感，进出画面就会刷屏）
)
DEFAULT_NOTIFY_COOLDOWN_SECONDS: float = 10.0  # 两次"看不见"通知最少间隔（防止反复入画 → 反复刷屏）
DEFAULT_RECOVERY_GRACE_SECONDS: float = (
    1.5  # 跟丢后必须连续找到 ≥ 这么久才发"又看到你"（防 YOLO 单帧漏检误报恢复）
)
DEFAULT_FREQUENCY: float = 20.0  # Hz；YOLO + ReID 每帧推理，GPU 不够就降回 15
# VisualServoing2D 自己的默认 _min_distance=0.8m、_assumed_object_width=0.45m、
# _backup_speed_factor=0.6——三个一组合，狗对"近"特别敏感，一靠近就 0.5*0.6=0.3 m/s
# 暴退；如果再叠加之前 max_linear=1.8，那就是 1.08 m/s 直接窜后面去。这里全部压低。
DEFAULT_MIN_DISTANCE: float = 0.5  # m；估距 < 0.5m 才强制倒退（target=1.0 时差不多就该退了）
DEFAULT_ASSUMED_PERSON_WIDTH: float = 0.55  # m；人正面 bbox 宽（含躯干 + 一点 padding）
DEFAULT_BACKUP_SPEED_FACTOR: float = 0.15  # 后退速度 = max_linear * 这个；0.15 → 慢退
DEFAULT_DISTANCE_DEAD_ZONE: float = 0.2  # m；|当前距离 - 目标| 在这范围内就只转不前后动
# YOLO + ReID 鲁棒性参数
# 默认换成 yolo11n.pt 纯 detection（不是 -pose）。pose 模型要求关键点完整，
# 狗相机机位低看不到头时 conf 直接掉到 0.2 以下整人被滤掉；纯 detection 对
# "只看到下半身/侧身/转身"鲁棒得多，跟随场景必须用它。
DEFAULT_YOLO_MODEL: str = "yolo11n.pt"
DEFAULT_YOLO_CONF: float = 0.18  # conf 再降一档，只看到大腿/背影也能框
DEFAULT_REID_GALLERY_MAX: int = 8  # 锚点模版库最大数（FIFO，首帧 base 永不替换）
DEFAULT_REID_GALLERY_UPDATE_MARGIN: float = 0.15  # sim >= threshold + margin 才入模版库。0.15 = 0.7 高于 0.55 阈值，确保入库的都是"真很像"的帧
# 位置连续性 fallback：ReID 不达标但 bbox 和上一帧锁定框 IoU 够高就视为同一人。
# 这是经典的 multi-object tracking 兜底，能扛"姿态突变 / OSNet 失效"那种情况。
# 但 IoU 兜底"只看位置不看人"，开太久会跟错人，所以 grace 缩到 8 帧 (≈400ms)。
DEFAULT_IOU_CONTINUITY: float = 0.5  # IoU >= 50% 才认为是同一人（更严，防止两个人擦肩而过被混淆）
DEFAULT_CONTINUITY_GRACE_FRAMES: int = (
    8  # IoU 兜底最多连续用 8 帧 (≈400ms)，超过强制要求 ReID 真匹配
)

# 人物花名册（person registry）相关参数：
# 这套机制让用户教 agent："这个人叫 XX"，agent 把外观 ReID 特征 + 文字描述存进
# 本地 JSON。下次说"跟着 XX"，agent 先在画面里按 ReID 找出那个人再启动跟随。
DEFAULT_PEOPLE_REGISTRY_PATH = Path.home() / ".dimos" / "people_registry.json"
DEFAULT_PERSON_MATCH_THRESHOLD: float = (
    0.55  # 反查/按名字找的最低相似度。和 follow 同步到 0.55，保持"是同一人"的判定一致
)
DEFAULT_PERSON_TOP1_MARGIN: float = (
    0.08  # top1 必须比 top2 高出这么多才算"可信地是 XXX"。0.08 防止两人外观相近时归错
)
DEFAULT_PERSON_MAX_TEMPLATES: int = 6  # 每个人最多存几个 embedding 模版（多角度更稳）

# "去找某某" 工作流（goto_and_greet_person）相关参数
DEFAULT_GOTO_TIMEOUT_SECONDS: float = 120.0  # 导航总超时上限，超过就放弃走，告诉用户走不到
DEFAULT_GOTO_POLL_INTERVAL: float = 0.3  # 主循环节拍：每隔多久查一次导航 state（顺手扫一帧）
DEFAULT_GOTO_SCAN_INTERVAL: float = 0.4  # YOLO+ReID 扫描节拍：边走边看的频率（默认 2.5Hz）
DEFAULT_GOTO_IDENTIFY_TIMEOUT: float = 15.0  # 到达目标点后给视觉确认的最大额外时长
# 路上看到目标人物时，达到这俩任一条件就提前停车 + 挥手：
#   - sim 超高（>= 阈值 + EARLY_STOP_SIM_BONUS），高置信度铁定是 ta；
#   - bbox 在画面里占比很大（>= EARLY_STOP_BBOX_HEIGHT_RATIO * image_height），
#     说明人已经在狗附近，挥手对方能看见。
DEFAULT_GOTO_EARLY_STOP_SIM_BONUS: float = 0.10
DEFAULT_GOTO_EARLY_STOP_BBOX_HEIGHT_RATIO: float = 0.45
DEFAULT_GOTO_HELLO_API_ID: int = 1016  # 宇树 sport_mod 挥手动作 api_id（SPORT_CMD["Hello"]）

Bbox4 = tuple[int, int, int, int]


def _emb_to_b64(emb: torch.Tensor) -> str:
    """ReID 特征张量 → base64 字符串（存 JSON 用）。"""
    buf = io.BytesIO()
    torch.save(emb.detach().cpu().float(), buf)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _emb_from_b64(s: str) -> torch.Tensor:
    """base64 字符串 → ReID 特征张量。"""
    tensor: torch.Tensor = torch.load(
        io.BytesIO(base64.b64decode(s)),
        map_location="cpu",
        weights_only=True,
    )
    return tensor


def _l2_normalize(vec: torch.Tensor) -> torch.Tensor:
    out: torch.Tensor = vec / (vec.norm(p=2, dim=-1, keepdim=True) + 1e-8)
    return out


def _cosine_sim(a: torch.Tensor, b: torch.Tensor) -> float:
    """两个特征向量的余弦相似度，结果在 [-1, 1]。"""
    a = _l2_normalize(a.flatten().float())
    b = _l2_normalize(b.flatten().float())
    return float(torch.dot(a, b).item())


def _pose_to_dict(pose: PoseStamped) -> dict[str, Any]:
    """把当前 odom 位姿打成可 JSON 序列化的纯 Python dict。

    导航 (NavigationInterface.set_goal) 一般用 "map" 或 "world" 系做目标。
    GO2Connection 的 _publish_tf 把 odom 当成 base_link 在 world 系下的位姿
    publish 出来（这里 odom 的 frame_id 默认是 "world"）。
    """
    p, q = pose.position, pose.orientation
    return {
        "x": float(p.x),
        "y": float(p.y),
        "z": float(p.z),
        "qx": float(q.x),
        "qy": float(q.y),
        "qz": float(q.z),
        "qw": float(q.w),
        "frame_id": pose.frame_id or "world",
        "ts": datetime.now().isoformat(timespec="seconds"),
    }


def _pose_from_dict(d: dict[str, Any]) -> PoseStamped:
    """把 _pose_to_dict 的产物还原成 PoseStamped；位姿 dict 缺字段时用合理默认。"""
    return PoseStamped(
        position=make_vector3(d.get("x", 0.0), d.get("y", 0.0), d.get("z", 0.0)),
        orientation=Quaternion(
            d.get("qx", 0.0), d.get("qy", 0.0), d.get("qz", 0.0), d.get("qw", 1.0)
        ),
        frame_id=d.get("frame_id", "world"),
    )


def _bbox_iou(a: Bbox4, b: Bbox4) -> float:
    """两个 bbox (x1,y1,x2,y2) 的 IoU，结果在 [0, 1]。用作 ReID 失效时的位置兜底。"""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


class FollowMeConfig(ModuleConfig):
    """FollowMeSkillContainer 的配置。

    使用 main 仓库当前的 `Config(ModuleConfig)` 模式，所有参数通过蓝图里
    `FollowMeSkillContainer.blueprint(camera_info=..., max_linear_speed=..., ...)`
    传进来。
    """

    camera_info: CameraInfo
    max_linear_speed: float = DEFAULT_MAX_LINEAR_SPEED
    max_angular_speed: float = DEFAULT_MAX_ANGULAR_SPEED
    target_distance: float = DEFAULT_TARGET_DISTANCE
    reid_threshold: float = DEFAULT_REID_THRESHOLD
    frequency: float = DEFAULT_FREQUENCY
    lost_grace_seconds: float = DEFAULT_LOST_GRACE_SECONDS
    min_distance: float = DEFAULT_MIN_DISTANCE
    assumed_person_width: float = DEFAULT_ASSUMED_PERSON_WIDTH
    backup_speed_factor: float = DEFAULT_BACKUP_SPEED_FACTOR
    distance_dead_zone: float = DEFAULT_DISTANCE_DEAD_ZONE
    angular_gain: float = DEFAULT_ANGULAR_GAIN
    angular_dead_zone: float = DEFAULT_ANGULAR_DEAD_ZONE
    angular_smoothing: float = DEFAULT_ANGULAR_SMOOTHING
    angular_edge_threshold: float = DEFAULT_ANGULAR_EDGE_THRESHOLD
    angular_edge_boost: float = DEFAULT_ANGULAR_EDGE_BOOST
    yolo_model: str = DEFAULT_YOLO_MODEL
    yolo_conf: float = DEFAULT_YOLO_CONF
    reid_gallery_max: int = DEFAULT_REID_GALLERY_MAX
    reid_gallery_update_margin: float = DEFAULT_REID_GALLERY_UPDATE_MARGIN
    notify_cooldown_seconds: float = DEFAULT_NOTIFY_COOLDOWN_SECONDS
    recovery_grace_seconds: float = DEFAULT_RECOVERY_GRACE_SECONDS
    iou_continuity: float = DEFAULT_IOU_CONTINUITY
    continuity_grace_frames: int = DEFAULT_CONTINUITY_GRACE_FRAMES


class FollowMeSkillContainer(Module):
    """带 ReID 的"跟我走"技能容器。

    通过 `@skill` 暴露 `follow_me()` 和 `stop_following()` 两个工具给 LLM。
    """

    config: FollowMeConfig

    color_image: In[Image]
    odom: In[PoseStamped]
    cmd_vel: Out[Twist]

    # 框架根据 Spec/Protocol 注解在 deploy 时自动注入
    _agent_spec: AgentSpec
    _navigation: NavigationInterfaceSpec
    _go2_connection: GO2ConnectionSpec

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        c = self.config

        # 仿真模式用 mujoco 相机内参
        camera_info = c.camera_info
        if c.g.simulation == "mujoco":
            from dimos.robot.unitree.mujoco_connection import MujocoConnection

            camera_info = MujocoConnection.camera_info_static
        elif c.g.simulation == "dimsim":
            from dimos.robot.unitree.dimsim_connection import DimSimConnection

            camera_info = DimSimConnection.camera_info_static

        self._camera_info = camera_info
        self._visual_servo = VisualServoing2D(camera_info, bool(c.g.simulation))
        # 覆盖速度上限（VS 类的字段是类级别默认，赋值实例属性 override）
        self._visual_servo._max_linear_speed = c.max_linear_speed
        self._visual_servo._max_angular_speed = c.max_angular_speed
        self._visual_servo._target_distance = c.target_distance
        # min_distance / assumed_object_width / backup_speed_factor 都覆盖，
        # 让"靠近"不会被一脚踩刹车暴退到墙根去
        self._visual_servo._min_distance = c.min_distance
        self._visual_servo._assumed_object_width = c.assumed_person_width
        self._visual_servo._backup_speed_factor = c.backup_speed_factor
        # 角度比例增益。从 1.8 降到 1.1：bbox 中心每帧都在抖（YOLO 检的天然误差），
        # gain 高 + 抖 = 狗一直微调脚步，看上去就是"原地左右摇摆"。
        self._visual_servo._angular_gain = c.angular_gain

        # 这几条 angular 死区 / 平滑 / 边缘加速 是 follow_me 自己来做（main 仓
        # VisualServoing2D 没把这些字段塞进 compute_twist），_follow_loop 里对
        # twist.angular.z 做后处理。状态量直接挂在 self 上。
        self._angular_dead_zone = c.angular_dead_zone
        self._angular_smoothing = c.angular_smoothing
        self._angular_edge_threshold = c.angular_edge_threshold
        self._angular_edge_boost = c.angular_edge_boost
        self._angular_z_prev: float = 0.0

        # 距离死区：处于 [target-dead, target+dead] 范围内不前后动，只调头
        self._distance_dead_zone = c.distance_dead_zone
        self._target_distance = c.target_distance
        # YOLO 配置（detector 实例化时传进去）
        self._yolo_model = c.yolo_model
        self._yolo_conf = c.yolo_conf
        # ReID 多模版库参数
        self._reid_gallery_max = c.reid_gallery_max
        self._reid_gallery_update_margin = c.reid_gallery_update_margin
        # IoU 位置连续性兜底（应对 ReID 失效，例如姿态突变 / 部分人体）
        self._iou_continuity = c.iou_continuity
        self._continuity_grace_frames = c.continuity_grace_frames
        # 通知节流：避免你"走出画面 + 走回"反复触发"看不见 / 又看到"两条消息刷屏
        self._notify_cooldown = c.notify_cooldown_seconds
        self._recovery_grace = c.recovery_grace_seconds
        self._reid_threshold = c.reid_threshold
        self._frequency = c.frequency
        self._lost_grace = c.lost_grace_seconds

        # 运行时状态。RLock / Event / Thread 都不能 pickle，但 main 的 Module
        # 自己 __getstate__ 会保留 self.__dict__，worker 进程里反序列化后再起
        # start() 会重新创建。这里先初始化好默认值，pickle 时 _lock/_should_stop
        # 这种锁我们走 lazy 创建（_ensure_runtime_state）。
        self._latest_image: Image | None = None
        # 当前狗在 world 系下的位姿（来自 GO2Connection.odom），用来：
        # 1) register_person 时把"此刻人在的地方"绑到该人物 entry 的 last_seen_pose；
        # 2) goto_and_greet_person 时计算导航完成的判断（虽然主要还是看 is_goal_reached）。
        self._latest_odom: PoseStamped | None = None
        self._lock: RLock | None = None
        self._should_stop: Event | None = None
        self._thread: Thread | None = None

        # 锚点 & 跟丢状态
        # 多模版 ReID：base 是开始跟随时锁定那一帧的特征（永不替换，避免跟错背景污染）；
        # gallery 是运行中"跟得稳"的帧顺手补进来的特征（FIFO），用来覆盖站起来只剩下半身、
        # 转身、举手挡脸 等姿态变化导致的外观差异。
        self._anchor_base_emb: torch.Tensor | None = None
        self._anchor_gallery: list[torch.Tensor] = []
        self._anchor_description: str = ""
        self._lost_since: float | None = None
        self._lost_message_sent: bool = False
        # 通知防抖状态
        self._last_lost_notify_time: float = 0.0  # 上次发"看不见"通知的时间
        self._recovered_since: float | None = (
            None  # 跟丢后重新看到的起点（用来等够 recovery_grace 再发"又看到"）
        )
        # IoU 位置连续性兜底状态：记住上一帧锁定的 bbox + 连续兜底了多少帧
        self._last_locked_bbox: Bbox4 | None = None
        self._continuity_frames_used: int = 0
        # 控指令调试日志节流（避免每帧刷屏，每秒 1 条）
        self._last_servo_log_time: float = 0.0
        # rerun bbox 可视化：让用户在 rerun viewer 的 Camera 视图上看到"锁定我的框"。
        # follow_me 跟 RerunBridgeModule 不在一个 worker 进程，所以这里要单独 connect
        # 到同一个 grpc proxy（recording_id="dimos-main" 保证合并进同一个 recording）。
        # 失败不影响主控制循环；lazy init。
        self._rerun_ready: bool = False

        # 重模型延迟加载，避免 import 时直接装载 GPU
        self._detector: YoloPersonDetector | None = None
        self._reid: TorchReIDModel | None = None
        self._vl: VlModel | None = None

        # 人物花名册（person registry）：name -> {
        #   "description": "蓝色裤子，黑色上衣",
        #   "embeddings_b64": [ReID 模版1, 模版2, ...],
        #   "registered_at": iso 时间戳,
        #   "updated_at": iso 时间戳,
        # }
        # 持久化到 ~/.dimos/people_registry.json，启动时自动加载。
        # _last_described 暂存上一次 describe_person 的结果，紧接着 register_person
        # 可以无缝接力，不用再抽一次特征。
        self._people: dict[str, dict[str, Any]] = {}
        self._last_described: dict[str, Any] | None = None
        self._people_registry_path: Path = DEFAULT_PEOPLE_REGISTRY_PATH
        self._person_match_threshold: float = DEFAULT_PERSON_MATCH_THRESHOLD
        self._person_top1_margin: float = DEFAULT_PERSON_TOP1_MARGIN
        self._person_max_templates: int = DEFAULT_PERSON_MAX_TEMPLATES
        # __init__ 在主进程也跑，从磁盘读 JSON 是只读 IO 不会卡 worker fork
        self._load_people_registry()

    # 把 pickle 不了的运行时字段在 __getstate__ 时剥掉。Module 父类已经处理了
    # 框架字段（_disposables/_loop 之类），这里只追加我们自己的。
    def __getstate__(self) -> dict[str, Any]:  # type: ignore[override]
        state: dict[str, Any] = super().__getstate__()  # type: ignore[no-untyped-call]
        for k in (
            "_lock",
            "_should_stop",
            "_thread",
            "_detector",
            "_reid",
            "_vl",
            "_anchor_base_emb",
            "_anchor_gallery",
            "_latest_image",
            "_latest_odom",  # PoseStamped 一般可 pickle，但 worker 进程要重新订阅，所以排除
            "_last_described",  # 含 Tensor，pickle 时排除
        ):
            state.pop(k, None)
        return state

    def __setstate__(self, state: dict[str, Any]) -> None:  # type: ignore[override]
        super().__setstate__(state)
        # worker 进程里被反序列化后，先把字段补回安全默认值；start() 会真正初始化
        self._lock = None
        self._should_stop = None
        self._thread = None
        self._detector = None
        self._reid = None
        self._vl = None
        self._anchor_base_emb = None
        self._anchor_gallery = []
        self._latest_image = None
        self._latest_odom = None
        # 花名册主进程已加载好（pickle 进来了 self._people 字典）；运行时辅助态另起
        self._last_described = None

    def _ensure_runtime_state(self) -> None:
        """worker 进程里第一次需要 lock/event 时才创建（pickle 安全）。"""
        if self._lock is None:
            self._lock = RLock()
        if self._should_stop is None:
            self._should_stop = Event()

    def _ensure_rerun(self) -> bool:
        """惰性初始化：连到 RerunBridgeModule 启动的 grpc proxy server，把 bbox 推过去。

        关键点：用 recording_id="dimos-main"——必须跟 bridge.py:start() 里的一致，
        这样 follow_me 进程 log 的 bbox 才会合并到同一个 recording，叠在 color_image 上。
        viewer mode 不是 native/web 时（比如 viewer="none"），grpc 端口可能不通，
        直接吞掉异常，主控制循环不受影响。
        """
        if self._rerun_ready:
            return True
        try:
            import rerun as rr

            from dimos.visualization.rerun.constants import RERUN_GRPC_PORT

            rr.init("dimos", recording_id="dimos-main", spawn=False)
            rr.connect_grpc(f"rerun+http://127.0.0.1:{RERUN_GRPC_PORT}/proxy")
            self._rerun_ready = True
            logger.info("follow_me rerun bbox stream connected")
            return True
        except Exception as e:
            logger.debug(f"rerun bbox stream init failed (will retry later): {e}")
            return False

    def _log_bbox_to_rerun(
        self,
        bbox: Bbox4,
        sim: float,
        est_dist: float | None,
    ) -> None:
        """把锁定的 bbox 推到 rerun viewer 的 color_image 视图上叠加显示。

        颜色根据置信度：sim 越高越绿，sim 在阈值附近偏黄/红，提示用户跟得稳不稳。
        rerun viewer 的 Spatial2DView 会自动把 entity_path 以 color_image 为前缀的
        子节点叠加在 image 上，所以 entity_path 用 'world/color_image/follow_lock'。
        """
        if not self._ensure_rerun():
            return
        try:
            import rerun as rr

            x1, y1, x2, y2 = bbox
            # 颜色映射：sim>=0.85 绿，0.65-0.85 黄，<0.65 红
            if sim >= 0.85:
                color = [0, 255, 80]
            elif sim >= 0.65:
                color = [255, 200, 0]
            else:
                color = [255, 60, 60]

            dist_str = "?" if est_dist is None else f"{est_dist:.2f}m"
            label = f"me sim={sim:.2f} {dist_str}"
            rr.log(
                "world/color_image/follow_lock",
                rr.Boxes2D(
                    array=[[x1, y1, x2 - x1, y2 - y1]],
                    array_format=rr.Box2DFormat.XYWH,
                    colors=[color],
                    labels=[label],
                ),
            )
        except Exception as e:
            # 单帧 log 失败不要让主循环挂掉
            logger.debug(f"rerun bbox log failed: {e}")
            self._rerun_ready = False  # 下次重新尝试 connect

    # Module 生命周期

    @rpc
    def start(self) -> None:
        super().start()
        self._ensure_runtime_state()
        self.register_disposable(Disposable(self.color_image.subscribe(self._on_color_image)))
        # odom 在 GO2Connection 里通过 _publish_tf -> self.odom.publish 推过来。
        # autoconnect 会自动把这个 Input 接到 GO2Connection.odom。
        self.register_disposable(Disposable(self.odom.subscribe(self._on_odom)))

    @rpc
    def stop(self) -> None:
        self._stop_following_internal()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=DEFAULT_THREAD_JOIN_TIMEOUT)
            self._thread = None

        for resource in (self._detector, self._reid, self._vl):
            if resource is None:
                continue
            try:
                resource.stop()
            except Exception as e:
                logger.warning(f"follow_me: failed to stop {type(resource).__name__}: {e}")

        super().stop()

    def _on_color_image(self, image: Image) -> None:
        # worker 进程冷启动早期可能 start() 还没把 _lock 建好，做个兜底
        if self._lock is None:
            self._ensure_runtime_state()
        with self._lock:  # type: ignore[union-attr]
            self._latest_image = image

    def _on_odom(self, odom: PoseStamped) -> None:
        # odom 来自 GO2Connection 的 base_link world 位姿。频率约 10-30Hz，
        # 这里只缓存最新一帧，组合 skill 用的时候快照一份就行。
        if self._lock is None:
            self._ensure_runtime_state()
        with self._lock:  # type: ignore[union-attr]
            self._latest_odom = odom

    # 暴露给 LLM agent 的 skills

    @skill
    def follow_me(self, description: str = "") -> str:
        """跟随用户。用户说"跟着我"/"跟我走"/"follow me" 时调用此技能。

        默认情况下会自动锚定画面里"最居中、最大"的人（通常就是面对机器人
        的用户本人）。如果用户给了外观描述（如"跟那个穿蓝色衣服的男生"），
        会先用视觉语言模型 (Qwen VL) 在画面里找匹配。

        锚定成功后，每帧用 TorchReID 跟 anchor 做特征匹配，**即使有其他人
        路过也不会跟错**。跟丢 5 秒后会自动通过对话告诉用户"看不见你了"
        并描述周围环境，然后狗会原地等，看到用户重新出现会自动重新锁定。

        Args:
            description: 可选。用户的外观文字描述。留空时自动选画面里最显眼的人。

        Returns:
            状态消息，包含是否锁定成功 / 锁定的描述。
        """
        # 关掉前一轮（如果有）
        self._ensure_runtime_state()
        self._stop_following_internal()
        if self._thread is not None:
            self._thread.join(timeout=DEFAULT_THREAD_JOIN_TIMEOUT)
        assert self._should_stop is not None
        self._should_stop.clear()

        assert self._lock is not None
        with self._lock:
            latest_image = self._latest_image
        if latest_image is None:
            return "现在还没收到相机画面，等几秒再试。"

        try:
            self._ensure_models_loaded()
        except Exception as e:
            return f"加载视觉模型失败：{e}"

        # 1) YOLO 找所有人
        try:
            detections = self._detect_persons(latest_image)
        except Exception as e:
            return f"YOLO 检测失败：{e}"
        if len(detections) == 0:
            return "画面里没看到人，请走到镜头前面，再让用户重发指令。"

        # 2) 选锚点 bbox
        anchor_bbox = self._pick_anchor_bbox(latest_image, detections, description)
        if anchor_bbox is None:
            return f"在画面里没找到匹配「{description}」的人。"

        # 3) 提 ReID embedding 作为 anchor base + 清空滚动模版库
        try:
            self._anchor_base_emb = self._reid_embed(latest_image, anchor_bbox)
        except Exception as e:
            return f"提取 ReID 特征失败：{e}"
        self._anchor_gallery = []

        self._anchor_description = description.strip() or "你"
        self._lost_since = None
        self._lost_message_sent = False
        # 把锚点 bbox 作为位置连续性的初始 _last_locked_bbox，IoU 兜底从一开始就可用
        self._last_locked_bbox = anchor_bbox
        self._continuity_frames_used = 0
        # 角速度 EMA 上一次的值清零，避免上一轮残留惯性甩头
        self._angular_z_prev = 0.0

        self._thread = Thread(target=self._follow_loop, daemon=True)
        self._thread.start()

        return (
            f"锁定「{self._anchor_description}」开始跟随。"
            f"max_v={self._visual_servo._max_linear_speed} m/s, "
            f"distance={self._visual_servo._target_distance} m, "
            f"dead_zone=±{self._distance_dead_zone} m, "
            f"backup={self._visual_servo._backup_speed_factor}×max, "
            f"freq={self._frequency} Hz, yolo_conf={self._yolo_conf}, "
            f"reid_gallery_max={self._reid_gallery_max}。"
            f"跟丢会主动报告。叫我停下用 'stop_following'。"
        )

    @skill
    def stop_following(self) -> str:
        """停止跟随用户。用户说"停下"/"别跟了" 时调用。"""
        self._stop_following_internal()
        self.cmd_vel.publish(Twist.zero())
        if self._thread is not None:
            self._thread.join(timeout=DEFAULT_THREAD_JOIN_TIMEOUT)
            self._thread = None
        return "已停止跟随，原地待机。"

    # 人物花名册（person registry）相关 skills
    # 用法：
    #   1) 用户："描述一下面前的人"            → describe_person()
    #   2) 用户："这是张三" / "他叫李四"        → register_person(name="张三")
    #   3) 用户："你记得谁"                     → list_known_people()
    #   4) 用户："跟着张三" / "去找李四"        → follow_person(name="张三")

    def _select_center_person(self, image: Image, detections: list[Bbox4]) -> Bbox4 | None:
        """画面里有多人时选"最居中且最大"的那个，作为 describe/register 默认目标。"""
        if not detections:
            return None
        cx_img = image.width / 2.0
        cy_img = image.height / 2.0

        def _score(b: Bbox4) -> float:
            x1, y1, x2, y2 = b
            cx = (x1 + x2) / 2.0
            cy = (y1 + y2) / 2.0
            area = max(0, (x2 - x1)) * max(0, (y2 - y1))
            dist = ((cx - cx_img) ** 2 + (cy - cy_img) ** 2) ** 0.5
            # 大且居中得分高
            return float(area / (1.0 + dist))

        return max(detections, key=_score)

    @skill
    def describe_person(self) -> str:
        """描述画面里的人长什么样（衣服、配饰）。

        用户问 "描述一下面前的人" / "他长什么样" / "看看那个人是谁" 时调用。
        如果画面里有多个人，会描述**最居中、最大**的那个（通常是面对机器人的用户）。
        本次抽出的 ReID 特征会暂存，紧接着用户说"这是张三"时 register_person
        可以直接接力把特征绑名字，不用再过一遍 YOLO/ReID。

        Returns:
            类似 "蓝色裤子，黑色上衣" 的一句描述。如果画面里没人会明确告知。
        """
        self._ensure_runtime_state()
        assert self._lock is not None
        with self._lock:
            latest_image = self._latest_image
        if latest_image is None:
            return "现在还没收到相机画面，等几秒再试。"
        try:
            self._ensure_models_loaded()
        except Exception as e:
            return f"加载视觉模型失败：{e}"

        try:
            detections = self._detect_persons(latest_image)
        except Exception as e:
            return f"YOLO 检测失败：{e}"
        if not detections:
            return "画面里没看到人，让 ta 走到镜头前再试。"

        target = self._select_center_person(latest_image, detections)
        assert target is not None
        try:
            emb = self._reid_embed(latest_image, target)
        except Exception as e:
            return f"ReID 特征提取失败：{e}"

        assert self._vl is not None
        try:
            description = self._vl.query(
                latest_image,
                "请用一句简短的中文描述画面里这个人的外观（衣服颜色、上衣下衣、有没有帽子或眼镜），"
                "不要描述背景，直接给描述。比如：'蓝色裤子，白色短袖，戴黑色帽子'。",
            ).strip()
        except Exception as e:
            return f"VL 描述失败：{e}"

        # 把"现在我在哪儿"也快照下来，这样紧接着 register_person 能把位姿绑给人物。
        # 之所以这里就快照而不是等 register 时再拿，是因为用户操作流可能间隔几秒，
        # 中间狗可能被遥控挪动了，此处的位姿才是"看到这个人时狗站的地方"。
        pose_snapshot: PoseStamped | None = None
        assert self._lock is not None
        with self._lock:
            if self._latest_odom is not None:
                pose_snapshot = self._latest_odom

        self._last_described = {
            "bbox": target,
            "emb": emb,
            "description": description,
            "pose": pose_snapshot,
        }

        multi_hint = (
            f"画面里有 {len(detections)} 个人，我描述最居中的这个：" if len(detections) > 1 else ""
        )
        pose_hint = ""
        if pose_snapshot is None:
            pose_hint = "（还没收到 odom，先把狗站稳让定位起来再录人）"
        return (
            f"{multi_hint}{description}\n"
            f"（已记下特征。如果要起名字，告诉我「这是 XX」我就用 register_person 录入。"
            f"{pose_hint}）"
        )

    @skill
    def register_person(self, name: str, description: str = "") -> str:
        """把当前画面里的人记到花名册里，绑定到一个名字上。

        用户说 "这是张三" / "他叫李四，记住" / "录入这位同事是王五" 时调用。
        建议先调 describe_person 看一眼再 register，这样特征已经暂存可以无缝接力。
        同名调用会**追加新模版**（多角度更稳，最多保留 6 个模版/人）。

        Args:
            name: 人的名字（必填）。例如 "张三"。
            description: 外观描述。留空会用上次 describe_person 的结果，
                或当场调 VL 重新描述一次。

        Returns:
            录入结果概要。
        """
        if not name or not name.strip():
            return "请给个名字，比如「这是张三」。"
        name = name.strip()

        self._ensure_runtime_state()
        try:
            self._ensure_models_loaded()
        except Exception as e:
            return f"加载视觉模型失败：{e}"

        emb: torch.Tensor | None = None
        desc = description.strip()
        # pose_snapshot：用来绑定到 entry["last_seen_pose"]，作为日后 goto_and_greet_person
        # 的导航目标。优先用 describe_person 暂存的那个 pose（因为那是真正"看到这个
        # 人时"狗的位置），否则用当前 odom。
        pose_snapshot: PoseStamped | None = None

        # 路径 1：上一句 describe_person 留了暂存，无缝接力
        if self._last_described is not None:
            emb = self._last_described["emb"]
            if not desc:
                desc = self._last_described["description"]
            pose_snapshot = self._last_described.get("pose")

        # 路径 2：没暂存（用户跳过 describe 直接 register），现场抽一次
        if emb is None:
            assert self._lock is not None
            with self._lock:
                latest_image = self._latest_image
            if latest_image is None:
                return "现在还没收到相机画面。"
            try:
                detections = self._detect_persons(latest_image)
            except Exception as e:
                return f"YOLO 检测失败：{e}"
            if not detections:
                return "画面里没看到人，先让 ta 站到镜头前。"
            target = self._select_center_person(latest_image, detections)
            assert target is not None
            try:
                emb = self._reid_embed(latest_image, target)
            except Exception as e:
                return f"ReID 特征提取失败：{e}"
            if not desc:
                assert self._vl is not None
                try:
                    desc = self._vl.query(
                        latest_image,
                        "用一句简短中文描述画面里那个人的外观（衣服颜色、上下衣），直接给描述。",
                    ).strip()
                except Exception as e:
                    desc = f"（VL 描述失败：{e}）"

        # 没拿到位姿就用当前 odom 兜底
        if pose_snapshot is None:
            assert self._lock is not None
            with self._lock:
                pose_snapshot = self._latest_odom

        # 入册
        entry = self._people.get(name) or {
            "name": name,
            "description": desc,
            "embeddings_b64": [],
            "registered_at": datetime.now().isoformat(timespec="seconds"),
        }
        entry["embeddings_b64"].append(_emb_to_b64(emb))
        # FIFO 截断
        if len(entry["embeddings_b64"]) > self._person_max_templates:
            entry["embeddings_b64"] = entry["embeddings_b64"][-self._person_max_templates :]
        # 用户显式传了 description 就覆盖，否则保留首次描述
        if description.strip() or not entry.get("description"):
            entry["description"] = desc
        # 位姿：拿到就更新（同名 register 视为"在新位置又看到 ta"，更新位置）；
        # 拿不到就保留旧值（如果旧值也没有，那 last_seen_pose 就缺失，goto 时会提示）
        if pose_snapshot is not None:
            entry["last_seen_pose"] = _pose_to_dict(pose_snapshot)
        entry["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self._people[name] = entry

        try:
            self._save_people_registry()
        except Exception as e:
            logger.warning(f"save people_registry failed: {e}")

        self._last_described = None
        n_tmpl = len(entry["embeddings_b64"])
        pose_msg = ""
        if "last_seen_pose" in entry:
            lp = entry["last_seen_pose"]
            pose_msg = f"，位置已记录 (x={lp['x']:.2f}, y={lp['y']:.2f})"
        else:
            pose_msg = "，但还没拿到位姿（goto_and_greet_person 暂时用不了）"
        return (
            f"记住「{name}」了：{entry['description']}（{n_tmpl} 个特征模版{pose_msg}）。"
            f"目前花名册有 {len(self._people)} 人：{'、'.join(self._people.keys())}。"
        )

    @skill
    def list_known_people(self) -> str:
        """列出花名册里已经记住的所有人。

        用户问 "你记得谁" / "花名册里有谁" / "认识哪些人" 时调用。
        """
        if not self._people:
            return "我还没记住任何人。让 ta 站到镜头前说「描述一下面前的人」+「这是XX」就能录入。"
        lines = []
        for name, entry in self._people.items():
            n_tmpl = len(entry.get("embeddings_b64", []))
            desc = entry.get("description", "")
            pose_part = ""
            if "last_seen_pose" in entry:
                lp = entry["last_seen_pose"]
                pose_part = f"，最后出现在 (x={lp['x']:.2f}, y={lp['y']:.2f})"
            lines.append(f"- {name}：{desc}（{n_tmpl} 模版{pose_part}）")
        return "我记得这些人：\n" + "\n".join(lines)

    @skill
    def forget_person(self, name: str) -> str:
        """从花名册里删掉一个人。用户说"删掉张三" / "忘掉李四" 时调用。"""
        name = (name or "").strip()
        if not name:
            return "要忘掉谁？给个名字。"
        if name not in self._people:
            return f"花名册里没有「{name}」。已知：{'、'.join(self._people.keys()) or '无'}"
        del self._people[name]
        try:
            self._save_people_registry()
        except Exception as e:
            logger.warning(f"save people_registry failed: {e}")
        return f"已忘掉「{name}」。剩下 {len(self._people)} 人。"

    def _match_bbox_to_registry(self, image: Image, bbox: Bbox4) -> list[tuple[str, float]]:
        """把一个 bbox 跟花名册里**每个人**算 max sim，返回按 sim 降序的 (name, sim) 列表。

        每个人有多个模版，取该人所有模版的 max。这样姚成彦录入时是正脸全身，
        现在他半身侧脸进来，也能从 6 个模版里找到最像那一个。
        """
        emb = self._reid_embed(image, bbox)
        scores: list[tuple[str, float]] = []
        for name, entry in self._people.items():
            templates = [_emb_from_b64(s) for s in entry.get("embeddings_b64", [])]
            if not templates:
                continue
            best = max(_cosine_sim(t, emb) for t in templates)
            scores.append((name, best))
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores

    @skill
    def identify_person(self) -> str:
        """看一眼画面里最居中的那个人，从花名册里反查 ta 是谁。

        用户说 "这是谁" / "面前的是谁" / "认识他吗" / "猜猜他叫什么" 时调用。
        这是 register_person 的**反向操作**——register 是"看着 ta + 给名字"录入；
        identify 是"看着 ta + 报名字"查询。

        判定逻辑：
        - 拿当前画面最居中那个人的 ReID 特征，跟花名册里每个人的所有模版
          (最多 6 个/人) 算 cosine sim，取每个人的 max sim 作为得分。
        - 最高分 ≥ 0.55 才算"可信地认出来"。
        - 而且最高分必须比第二高至少多 0.08，否则归为"长得有点像 XX 但不确定"
          （避免把陌生人勉强归到衣服颜色最像的那位）。

        Returns:
            类似 "这是张三（匹配度 0.62，对比次像 0.31）" 的一句话，认不出来
            会明确说 "认不出来"。
        """
        self._ensure_runtime_state()
        if not self._people:
            return "花名册是空的，没法认人。先用 register_person 录入几个人吧。"

        assert self._lock is not None
        with self._lock:
            latest_image = self._latest_image
        if latest_image is None:
            return "现在还没收到相机画面。"

        try:
            self._ensure_models_loaded()
        except Exception as e:
            return f"加载视觉模型失败：{e}"

        try:
            detections = self._detect_persons(latest_image)
        except Exception as e:
            return f"YOLO 检测失败：{e}"
        if not detections:
            return "画面里没看到人。"

        target = self._select_center_person(latest_image, detections)
        assert target is not None
        try:
            scores = self._match_bbox_to_registry(latest_image, target)
        except Exception as e:
            return f"ReID 匹配失败：{e}"

        if not scores:
            return "花名册里这些人都没存特征模版，没法对比。"

        top_name, top_sim = scores[0]
        runner_sim = scores[1][1] if len(scores) >= 2 else -1.0
        margin = top_sim - runner_sim

        # 分数明细日志（用户能在 dimos 终端看 servo 日志旁边的对比表）
        detail = "、".join(f"{n}={s:.2f}" for n, s in scores[:5])

        if top_sim < self._person_match_threshold:
            return (
                f"画面里这个人我认不出来（最高匹配「{top_name}」也只有 "
                f"{top_sim:.2f}，低于阈值 {self._person_match_threshold}）。"
                f"对比详情：{detail}。"
                f"如果是花名册里的人，让 ta 走近、正脸对着我再问一次；"
                f"或者直接告诉我 ta 是谁，我再用同名 register_person 多录一个角度。"
            )

        if len(scores) >= 2 and margin < self._person_top1_margin:
            return (
                f"长得像「{top_name}」（{top_sim:.2f}），但跟「{scores[1][0]}」"
                f"({runner_sim:.2f}) 差不多，差距 {margin:.2f} 太小不敢确定。"
                f"对比详情：{detail}。让 ta 走近一点再问一次。"
            )

        return (
            f"这是「{top_name}」（匹配度 {top_sim:.2f}，对比次像 {runner_sim:.2f}，"
            f"差距 {margin:.2f}）。对比详情：{detail}。"
        )

    @skill
    def follow_person(self, name: str) -> str:
        """按花名册的名字识别画面里的目标人物，识别成功就开始跟随。

        用户说 "跟着张三" / "去找李四" / "follow 王五" 时调用。
        前提：ta 必须先通过 register_person 录入过。

        逻辑：
        1. 在花名册里找 name 对应的 ReID 特征模版（最多 6 个）。
        2. YOLO 找当前画面里所有人，每个 bbox 抽 ReID 特征。
        3. 跟 name 的所有模版算 cosine sim，取最高的那个 bbox。
        4. 最高相似度 >= 0.55 才认为"画面里有 name"，启动跟随。
        5. 否则告诉用户没找到（也许 ta 还没进画面）。

        Args:
            name: 花名册里的名字。

        Returns:
            匹配结果 / 启动状态。
        """
        name = (name or "").strip()
        if not name:
            return "要跟谁？给个名字。"
        if name not in self._people:
            known = "、".join(self._people.keys()) or "无"
            return f"花名册里没有「{name}」。已知：{known}。先用 register_person 录入。"

        entry = self._people[name]
        b64_list = entry.get("embeddings_b64", [])
        templates = [_emb_from_b64(s) for s in b64_list]
        if not templates:
            return f"「{name}」在花名册里但没有特征模版，重新录入。"

        # 关掉上一轮 follow（如果有）
        self._ensure_runtime_state()
        self._stop_following_internal()
        if self._thread is not None:
            self._thread.join(timeout=DEFAULT_THREAD_JOIN_TIMEOUT)
        assert self._should_stop is not None
        self._should_stop.clear()

        assert self._lock is not None
        with self._lock:
            latest_image = self._latest_image
        if latest_image is None:
            return "现在还没收到相机画面。"
        try:
            self._ensure_models_loaded()
        except Exception as e:
            return f"加载视觉模型失败：{e}"

        try:
            detections = self._detect_persons(latest_image)
        except Exception as e:
            return f"YOLO 检测失败：{e}"
        if not detections:
            return f"画面里没看到人，先把「{name}」叫到镜头前。"

        # 在所有 bbox 里挑跟 name 最像的
        best_bbox: Bbox4 | None = None
        best_sim: float = -1.0
        best_emb: torch.Tensor | None = None
        for bbox in detections:
            try:
                emb = self._reid_embed(latest_image, bbox)
            except Exception:
                continue
            sim = max(_cosine_sim(t, emb) for t in templates)
            if sim > best_sim:
                best_sim = sim
                best_bbox = bbox
                best_emb = emb

        if best_bbox is None or best_sim < self._person_match_threshold:
            return (
                f"画面里没找到跟「{name}」很像的人（最高匹配 {best_sim:.2f} < "
                f"{self._person_match_threshold}）。花名册里 ta 的描述：{entry.get('description', '')}。"
                f"让 ta 走近一点或者正脸对着我再试。"
            )

        # 用花名册里的模版当 anchor 启动 follow_loop
        self._anchor_base_emb = templates[0]
        self._anchor_gallery = list(templates[1:])
        if best_emb is not None:
            self._anchor_gallery.append(best_emb)
        if len(self._anchor_gallery) > self._reid_gallery_max:
            self._anchor_gallery = self._anchor_gallery[-self._reid_gallery_max :]
        self._anchor_description = name
        self._lost_since = None
        self._lost_message_sent = False
        self._last_locked_bbox = best_bbox
        self._continuity_frames_used = 0
        self._angular_z_prev = 0.0

        self._thread = Thread(target=self._follow_loop, daemon=True)
        self._thread.start()

        return (
            f"在画面里认出「{name}」（匹配度 {best_sim:.2f}），开始跟。"
            f"花名册里 ta 的描述：{entry.get('description', '')}。"
        )

    # 复合 skill：去找某人 + 打招呼（导航 -> 视觉确认 -> 挥手）

    def _scan_frame_for_target(self, templates: list[torch.Tensor]) -> tuple[float, Bbox4] | None:
        """扫当前画面，跟一组人物模版比对，返回 (最高匹配 sim, 对应 bbox)。

        没拿到画面 / YOLO 没找到人 / ReID 算不出来 都返回 None。
        被 goto_and_greet_person 的"边走边看"和"到达后确认"两段复用。
        """
        assert self._lock is not None
        with self._lock:
            latest_image = self._latest_image
        if latest_image is None:
            return None
        try:
            detections = self._detect_persons(latest_image)
        except Exception as e:
            logger.debug(f"_scan_frame: YOLO 失败 {e}")
            return None
        if not detections:
            return None

        best_sim = -1.0
        best_bbox: Bbox4 | None = None
        for bbox in detections:
            try:
                emb = self._reid_embed(latest_image, bbox)
            except Exception:
                continue
            sim = max(_cosine_sim(t, emb) for t in templates)
            if sim > best_sim:
                best_sim = sim
                best_bbox = bbox

        if best_bbox is None:
            return None
        return best_sim, best_bbox

    @skill
    def goto_and_greet_person(self, name: str) -> str:
        """去找花名册里某个已注册的人，到达后视觉确认 + 挥手打招呼。

        这是"参观-记人-去找"工作流的最后一步，对应用户说 "去找张三"、"去叫张三过来"、
        "去张三那打个招呼"。**前提**：你必须先用 register_person("张三") 把 ta 录入过，
        而且当时 dimos 已经拿到了狗的位姿（list_known_people 能看到 "最后出现在 (x,y)"
        那一行才算有位置）。

        工作流（同步执行，时间可能几十秒～两分钟）：
        1. 从花名册查 name 的 last_seen_pose。
        2. 调 dimos 自带的 NavigationInterface.set_goal 发起 A* 导航走过去。
        3. **导航过程中 YOLO+ReID 一直开着扫描每一帧**：
           - 路上看到 name 而且 ta 离狗已经够近（bbox 高度 >= 画面 45%），
             或者匹配度非常高（>= 阈值 + 0.10）→ **提前取消导航 + 直接挥手**。
           - 这样目标人物在原坐标附近小幅移动（比如从工位站起来走两步）狗也能跟上。
        4. 走到 last_seen_pose 仍没看到 → 原地再扫几秒（identify timeout）。
        5. 视觉确认是 name 后，调狗端 sport_mod Hello (api_id=1016) 挥手。

        失败场景：
        - 花名册里没这个人 / 没位姿 → 直接报错让用户先 register_person 一次
        - 导航超时 / 走不通 → 取消 goal、原地停、返回失败原因
        - 到达后没找到 name → 不挥手，告诉用户 "走到了但没认出 ta，可能 ta 不在了"

        Args:
            name: 花名册里的名字。

        Returns:
            一句话概述整个过程的结果（用户能直接念给场景里的人听）。
        """
        name = (name or "").strip()
        if not name:
            return "要去找谁？给个名字。"
        if name not in self._people:
            known = "、".join(self._people.keys()) or "无"
            return f"花名册里没有「{name}」。已知：{known}。先用 register_person 录入一次再说。"

        entry = self._people[name]
        if "last_seen_pose" not in entry:
            return (
                f"「{name}」我记得长相但没记位置（可能录入时 odom 还没上线）。"
                f'先带狗到 ta 经常出现的地方，再调一次 register_person("{name}")。'
            )

        templates = [_emb_from_b64(s) for s in entry.get("embeddings_b64", [])]
        if not templates:
            return f"花名册里「{name}」没特征模版，没法做视觉确认。重新 register_person 一次。"

        # 视觉模型必须现在就 ready，否则边走边扫没意义
        try:
            self._ensure_models_loaded()
        except Exception as e:
            return f"加载视觉模型失败：{e}"

        # 1) RPC 句柄。早 fail，不然导航半截路才报"模块没连上"很丑
        try:
            nav = self._navigation
        except AttributeError:
            return (
                "Navigation 模块没连上，没法导航过去。"
                "这套蓝图必须带 ReplanningAStarPlanner（默认就有）。"
            )

        # 2) 发起导航
        goal_pose = _pose_from_dict(entry["last_seen_pose"])
        # 导航目标 frame 一般用 "map" 系，但我们录入时存的是 odom 给出的 frame
        # （GO2Connection 的 odom 默认是 "world"）。replanning_a_star 在内部用 tf
        # 把 world/map/base_link 互转，所以这里只要 frame_id 跟 odom 一致即可。
        logger.info(
            f"goto_and_greet_person: 走向 {name} 的位置 "
            f"({goal_pose.position.x:.2f}, {goal_pose.position.y:.2f}, "
            f"frame={goal_pose.frame_id})"
        )

        # 先把可能正在跑的 follow_loop 停掉，避免 cmd_vel 跟导航打架
        self._stop_following_internal()

        try:
            ok = nav.set_goal(goal_pose)
        except Exception as e:
            return f"set_goal 调用炸了：{e}"
        if not ok:
            return f"导航模块拒绝了目标（set_goal 返回 False）。检查地图是否覆盖到 {name} 那块。"

        # 3) 主循环：导航轮询 + 边走边视觉扫描
        timeout = DEFAULT_GOTO_TIMEOUT_SECONDS
        poll = DEFAULT_GOTO_POLL_INTERVAL
        scan_interval = DEFAULT_GOTO_SCAN_INTERVAL
        early_stop_sim = self._person_match_threshold + DEFAULT_GOTO_EARLY_STOP_SIM_BONUS
        early_stop_ratio = DEFAULT_GOTO_EARLY_STOP_BBOX_HEIGHT_RATIO

        # 把这一路上扫描看到的"最佳匹配"记下来：
        # - 如果中途触发了 early stop（铁定是 ta 而且离得够近），就直接拿这个匹配挥手
        # - 如果走到了 last_seen_pose 还没 early stop，到达后会用这个值作为继续比较的起点，
        #   不必从 -1.0 重新开始
        best_sim: float = -1.0
        best_seen_at: str = "未见"  # 用于结果文案："路上"/"到达后" 给用户感觉

        start = time.time()
        last_scan = 0.0
        last_logged_state: str | None = None
        arrived = False
        early_stopped = False

        while time.time() - start < timeout:
            now = time.time()

            # 3a) 边走边扫描（节流到 ~2.5Hz，YOLO+ReID 在 RTX 5060 上一帧 ~50ms 不阻塞控制环）
            if now - last_scan >= scan_interval:
                last_scan = now
                result = self._scan_frame_for_target(templates)
                if result is not None:
                    sim, bbox = result
                    bbox_h = bbox[3] - bbox[1]  # y2 - y1
                    # 用 camera_info.height 拿到画面高度，做"近不近"的判断
                    img_h = max(self._camera_info.height, 1)
                    bbox_ratio = bbox_h / img_h
                    if sim > best_sim:
                        best_sim, _best_bbox = sim, bbox
                        best_seen_at = "路上"
                        logger.info(
                            f"路上 scan「{name}」 sim={sim:.2f} bbox_h_ratio={bbox_ratio:.2f}"
                        )
                    # 满足任意 early stop 条件 -> 撤导航直接挥手
                    high_confidence = sim >= early_stop_sim
                    close_enough = (
                        sim >= self._person_match_threshold and bbox_ratio >= early_stop_ratio
                    )
                    if high_confidence or close_enough:
                        logger.info(
                            f"路上找到「{name}」(sim={sim:.2f}, bbox_h_ratio={bbox_ratio:.2f})，"
                            f"提前取消导航"
                        )
                        try:
                            nav.cancel_goal()
                        except Exception:
                            pass
                        # 让狗的速度滚动停下来（A* 那边发了最后一帧 cmd_vel 也得吃完）
                        time.sleep(0.5)
                        early_stopped = True
                        break

            # 3b) 导航 state 检查
            try:
                state = nav.get_state()
            except Exception as e:
                logger.warning(f"get_state 异常：{e}")
                state = None

            state_name = getattr(state, "value", str(state) if state is not None else "?")
            if state_name != last_logged_state:
                logger.info(f"导航 state={state_name} 已耗时 {time.time() - start:.1f}s")
                last_logged_state = state_name

            # planner 回到 idle = 它已经停了（要么到了、要么 fail）
            if state is not None and state == NavigationState.IDLE:
                # 等一拍让 is_goal_reached 稳定
                time.sleep(0.5)
                try:
                    arrived = bool(nav.is_goal_reached())
                except Exception as e:
                    logger.warning(f"is_goal_reached 异常：{e}")
                    arrived = False
                break
            time.sleep(poll)
        else:
            # while 没 break = 超时
            try:
                nav.cancel_goal()
            except Exception:
                pass
            return (
                f"走向「{name}」超时（{timeout}s 没到），已撤回目标。"
                f"可能路上被堵了，或者 last_seen_pose 在禁区。先 list_known_people 看看坐标合不合理。"
            )

        # 路上 early stop 也算"找到"，直接跳到挥手
        if not early_stopped and not arrived:
            return (
                f"导航到「{name}」失败（is_goal_reached=False）。可能路径被障碍卡住，"
                f"我已经停在原地。要不要先 stop_navigation 再手动遥控过去？"
            )

        # 4) 到达 / 提前停后，如果信心还不够，再多扫几秒兜底
        # （early_stopped 的 best_sim 一定已经 >= threshold，这里直接跳过额外扫描）
        if not early_stopped:
            logger.info("已到达目标点，原地继续扫描视觉确认……")
            identify_deadline = time.time() + DEFAULT_GOTO_IDENTIFY_TIMEOUT
            while time.time() < identify_deadline:
                result = self._scan_frame_for_target(templates)
                if result is not None:
                    sim, bbox = result
                    if sim > best_sim:
                        best_sim, _best_bbox = sim, bbox
                        best_seen_at = "到达后"
                # 早退：已经很有信心就别再扫了
                if best_sim >= self._person_match_threshold + DEFAULT_GOTO_EARLY_STOP_SIM_BONUS:
                    break
                time.sleep(0.3)

        identified = best_sim >= self._person_match_threshold
        if not identified:
            return (
                f"走到「{name}」上次出现的位置了，但画面里没认出 ta（最高匹配 {best_sim:.2f}）。"
                f"可能 ta 已经离开了。"
            )

        # 5) 挥手
        try:
            self._go2_connection.publish_request(
                "rt/api/sport/request",
                {"api_id": DEFAULT_GOTO_HELLO_API_ID},
            )
        except AttributeError:
            return (
                f"找到「{name}」({best_seen_at}识别，匹配度 {best_sim:.2f})，"
                f"但 GO2Connection 没连上挥不了手。"
            )
        except Exception as e:
            return (
                f"找到「{name}」({best_seen_at}识别，匹配度 {best_sim:.2f})，"
                f"但挥手指令发送失败：{e}"
            )

        logger.info(f"goto_and_greet_person({name}) 完成（{best_seen_at}），匹配度 {best_sim:.2f}")
        suffix = "（路上就提前看见 ta 了）" if best_seen_at == "路上" else ""
        return f"找到「{name}」啦，匹配度 {best_sim:.2f}，已向 ta 挥手打招呼{suffix}。"

    # 花名册 持久化

    def _load_people_registry(self) -> None:
        """启动时从 ~/.dimos/people_registry.json 加载花名册，文件不存在/损坏静默忽略。"""
        try:
            if not self._people_registry_path.exists():
                return
            with self._people_registry_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._people = data
                logger.info(
                    f"loaded {len(self._people)} person(s) from {self._people_registry_path}"
                )
        except Exception as e:
            logger.warning(f"load people_registry failed: {e}")
            self._people = {}

    def _save_people_registry(self) -> None:
        """把 self._people 写盘。embeddings 已经是 base64 字符串，直接 dump 即可。"""
        self._people_registry_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._people_registry_path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(self._people, f, ensure_ascii=False, indent=2)
        tmp.replace(self._people_registry_path)

    # 跟随主循环

    def _smooth_angular(self, x_norm: float, raw_angular_z: float) -> float:
        """对原始 angular_z 做"死区 + 边缘 boost + EMA 平滑"三连后处理。

        main 仓库的 VisualServoing2D.compute_twist 只算了基础 angular_z（=-x_norm*gain），
        但跟随场景下 bbox 中心每帧抖几像素就会让狗不停"小步调头"——肉眼看像在原地
        摇摆。这里手动加：

        - **横向死区**: |x_norm| < dead_zone 时 angular_z=0；超出后把 raw 整体往中心
          拉 dead_zone 大小，保证死区边界处平滑过渡（不会"突然全力转"）。
        - **边缘 boost**: |x_norm| >= edge_threshold 时把 raw 临时放大 edge_boost 倍，
          防止"人走快就出画面"。
        - **EMA 低通**: angular_z = α·new + (1-α)·prev，α=0.5 时半数惯性。接近 0 的
          尾部直接归零，省得拖小尾巴让狗一直微调。
        """
        if abs(x_norm) < self._angular_dead_zone:
            return 0.0

        # 把 raw 整体往中心拉 dead_zone（沿原符号），让死区边界 ≈ 0 平滑过渡
        sign = 1.0 if raw_angular_z >= 0 else -1.0
        adjusted_magnitude = max(
            0.0,
            abs(raw_angular_z) - self._angular_dead_zone * abs(self._visual_servo._angular_gain),
        )
        boosted = sign * adjusted_magnitude

        # 边缘 boost
        if abs(x_norm) >= self._angular_edge_threshold:
            boosted *= self._angular_edge_boost

        # clip 一下，免得 boost 把它放飞
        max_w = self._visual_servo._max_angular_speed
        boosted = float(np.clip(boosted, -max_w, max_w))

        # EMA
        alpha = self._angular_smoothing
        smoothed = alpha * boosted + (1.0 - alpha) * self._angular_z_prev
        if abs(smoothed) < 0.02:
            smoothed = 0.0
        self._angular_z_prev = smoothed
        return smoothed

    def _follow_loop(self) -> None:
        period = 1.0 / self._frequency
        next_time = time.monotonic()
        logger.info(
            "follow_me loop started",
            anchor=self._anchor_description,
            freq=self._frequency,
            reid_threshold=self._reid_threshold,
        )

        self._ensure_runtime_state()
        assert self._should_stop is not None and self._lock is not None

        try:
            while not self._should_stop.is_set():
                next_time += period

                with self._lock:
                    latest_image = self._latest_image
                if latest_image is None:
                    self.cmd_vel.publish(Twist.zero())
                    self._sleep_until(next_time)
                    continue

                # YOLO 找所有人
                try:
                    detections = self._detect_persons(latest_image)
                except Exception as e:
                    logger.warning(f"YOLO detect failed: {e}")
                    detections = []

                # ReID 选最像 anchor 的那个：每个 bbox 跟"base + gallery 所有模版"算 sim 取 max。
                # 这样姿态/视角变化（全身→下半身、转身、举手挡脸）也能匹配上，因为只要某一个
                # 历史成功匹配的模版相近就够了。
                templates: list[torch.Tensor] = []
                if self._anchor_base_emb is not None:
                    templates.append(self._anchor_base_emb)
                templates.extend(self._anchor_gallery)

                best_bbox: Bbox4 | None = None
                best_sim: float = -1.0
                best_emb: torch.Tensor | None = None
                for bbox in detections:
                    try:
                        emb = self._reid_embed(latest_image, bbox)
                        # 对每个模版算 sim，取最大值作为这个 bbox 的得分
                        bbox_sim = (
                            max(_cosine_sim(t, emb) for t in templates) if templates else -1.0
                        )
                    except Exception as e:
                        logger.debug(f"reid embed failed on bbox {bbox}: {e}")
                        continue
                    if bbox_sim > best_sim:
                        best_sim = bbox_sim
                        best_bbox = bbox
                        best_emb = emb

                now = time.monotonic()

                # 人数自适应阈值：
                #   画面 1 个人 → 阈值放宽到 0.35（兼容姿态/光照变化，避免姿态一变就跟丢）
                #   画面 ≥2 人  → 阈值用严格的 self._reid_threshold (0.55)，强制区分谁是谁
                # 陌生人 vs anchor 的 sim 一般 0.2-0.4，0.35 仍能过滤掉无关人闯入单人画面的情况
                n_persons = len(detections)
                effective_threshold = (
                    min(self._reid_threshold, 0.35) if n_persons == 1 else self._reid_threshold
                )

                # 三态判定：
                #   "reid": YOLO 找到且 ReID 阈值过了——主路径，正常更新 gallery
                #   "iou":  ReID 不达标但 bbox 和上一帧锁定框 IoU 够高——位置兜底，
                #           应对姿态突变 / 部分人体 / OSNet 失效。不更新 gallery 避免污染。
                #   "lost": 都没有——走丢失分支
                tracked: str = "lost"
                if best_bbox is not None and best_sim >= effective_threshold:
                    tracked = "reid"
                elif (
                    best_bbox is not None
                    and self._last_locked_bbox is not None
                    and self._continuity_frames_used < self._continuity_grace_frames
                    and _bbox_iou(best_bbox, self._last_locked_bbox) >= self._iou_continuity
                ):
                    tracked = "iou"

                if tracked != "lost":
                    # tracked 设成 reid/iou 的两条路径都已经 check 过 best_bbox is not None，
                    # 这里 assert 一下让 mypy narrow 类型
                    assert best_bbox is not None
                    # 找到 user（ReID 命中或 IoU 兜底）
                    self._lost_since = None
                    # 恢复防抖：刚发了"看不见"之后，必须**连续找到**至少 recovery_grace
                    # 秒才发"又看到"——避免 YOLO 单帧漏检 → 一帧找到 → 立刻又丢的鬼畜。
                    if self._lost_message_sent:
                        if self._recovered_since is None:
                            self._recovered_since = now
                        elif now - self._recovered_since >= self._recovery_grace:
                            self._send_message_as_user(
                                "（系统通知：刚刚跟丢的目标重新出现在画面里了，"
                                "已经恢复跟随。请用简短一句话告诉用户'又看到你了，继续跟'。）"
                            )
                            self._lost_message_sent = False
                            self._recovered_since = None
                    else:
                        self._recovered_since = None

                    # 把当前帧"高置信度地"加入模版库（FIFO），覆盖姿态/视角变化。
                    # 只有 ReID 主路径命中 + sim 比阈值高出 margin 才入库；IoU 兜底
                    # 不入库，避免位置连续但 ReID 不像的"擦边"案例污染模版。
                    if (
                        tracked == "reid"
                        and best_emb is not None
                        and best_sim >= self._reid_threshold + self._reid_gallery_update_margin
                        and self._reid_gallery_max > 0
                    ):
                        self._anchor_gallery.append(best_emb)
                        # FIFO 截断；base 在 templates 列表里单独保存，不会被挤掉
                        if len(self._anchor_gallery) > self._reid_gallery_max:
                            self._anchor_gallery = self._anchor_gallery[-self._reid_gallery_max :]

                    # 更新位置连续性状态：ReID 命中→重置计数；IoU 兜底→+1（防漂移用）
                    self._last_locked_bbox = best_bbox
                    if tracked == "reid":
                        self._continuity_frames_used = 0
                    else:
                        self._continuity_frames_used += 1

                    try:
                        # Bbox4 是 tuple[int, ...]，VisualServoing2D 要求 tuple[float, ...]，
                        # mypy 在 tuple 上不变（不像 Sequence），所以这里显式做一次 promote
                        bbox_f: tuple[float, float, float, float] = (
                            float(best_bbox[0]),
                            float(best_bbox[1]),
                            float(best_bbox[2]),
                            float(best_bbox[3]),
                        )
                        twist = self._visual_servo.compute_twist(bbox_f, latest_image.width)
                        # 距离死区：估出来的距离在 target ± dead_zone 范围内，
                        # 就只让狗调头对准你，不再前后蹭——这样合适距离时不会前后摇摆，
                        # 也避免 bbox 估距抖动一下就触发倒车。
                        est_dist = self._visual_servo._estimate_distance(bbox_f)
                        in_dead_zone = (
                            est_dist is not None
                            and self._distance_dead_zone > 0.0
                            and abs(est_dist - self._target_distance) < self._distance_dead_zone
                        )
                        # 角速度后处理（死区 + EMA + 边缘 boost）
                        bbox_center_x = (best_bbox[0] + best_bbox[2]) / 2.0
                        x_norm = self._visual_servo._get_normalized_x(bbox_center_x)
                        smoothed_w = self._smooth_angular(x_norm, twist.angular.z)

                        if in_dead_zone:
                            twist = Twist(
                                linear=Vector3(0.0, 0.0, 0.0),
                                angular=Vector3(0.0, 0.0, smoothed_w),
                            )
                        else:
                            twist = Twist(
                                linear=twist.linear,
                                angular=Vector3(0.0, 0.0, smoothed_w),
                            )
                        self.cmd_vel.publish(twist)
                        # 把锁定 bbox 推到 rerun viewer，叠在 color_image 上（每帧都推，
                        # rerun 内部会合批，不会阻塞）。颜色随 sim 变化，方便肉眼判断稳不稳。
                        self._log_bbox_to_rerun(best_bbox, best_sim, est_dist)
                        # 每秒打一条 servo 调试日志，方便确认距离/速度/模版库合不合理
                        if now - self._last_servo_log_time >= 1.0:
                            self._last_servo_log_time = now
                            bbox_w = best_bbox[2] - best_bbox[0]
                            dz_tag = " [dead-zone]" if in_dead_zone else ""
                            mode_tag = f" [{tracked}]"  # reid / iou，方便看是不是在靠兜底
                            # 画面人数 + 有效阈值，方便排查"乱认人"问题：
                            # n=1 时 thr 是放宽阈值 0.35，n>=2 是严格的 0.55
                            crowd_tag = f" n={n_persons} thr={effective_threshold:.2f}"
                            gallery_n = len(self._anchor_gallery)
                            logger.info(
                                f"servo sim={best_sim:.2f} gal={gallery_n} "
                                f"bbox_w={bbox_w}px "
                                f"est_dist={est_dist if est_dist is None else f'{est_dist:.2f}m'} -> "
                                f"v={twist.linear.x:+.2f} w={twist.angular.z:+.2f}"
                                f"{dz_tag}{mode_tag}{crowd_tag}"
                            )
                    except Exception as e:
                        logger.warning(f"visual_servo compute_twist failed: {e}")
                        self.cmd_vel.publish(Twist.zero())
                else:
                    # 没找到 user：原地停 + 计丢失时长
                    self.cmd_vel.publish(Twist.zero())
                    # 把 angular EMA 也归零，下次看到人时不会从上次惯性继续转
                    self._angular_z_prev = 0.0
                    # 跟丢时清掉 rerun 上次画的 bbox，否则用户看到的是一个"卡住的框"
                    if self._rerun_ready:
                        try:
                            import rerun as rr

                            rr.log("world/color_image/follow_lock", rr.Clear(recursive=False))
                        except Exception:
                            self._rerun_ready = False
                    # 一旦看不到就把"恢复倒计时"清零，下次重新看到要重新等够 recovery_grace
                    self._recovered_since = None
                    # 位置连续性兜底状态也清掉——人没看见了，原来记的 bbox 已经没用
                    self._last_locked_bbox = None
                    self._continuity_frames_used = 0
                    if self._lost_since is None:
                        self._lost_since = now

                    lost_duration = now - self._lost_since
                    # 通知冷却：避免反复进出画面把同一条"看不见"通知发个不停
                    cooldown_ok = (
                        self._last_lost_notify_time == 0.0
                        or now - self._last_lost_notify_time >= self._notify_cooldown
                    )
                    if (
                        lost_duration >= self._lost_grace
                        and not self._lost_message_sent
                        and cooldown_ok
                    ):
                        self._report_lost(latest_image)
                        self._lost_message_sent = True
                        self._last_lost_notify_time = now

                self._sleep_until(next_time)
        finally:
            self.cmd_vel.publish(Twist.zero())
            logger.info("follow_me loop exited")

    # 与 agent 的对话注入

    def _send_message_as_user(self, msg: str) -> None:
        """把一条 HumanMessage 注入到正在跑的 agent，agent 会用 LLM 处理后发给用户。"""
        try:
            self._agent_spec.add_message(HumanMessage(msg))
        except Exception as e:
            logger.warning(f"add_message failed: {e}")

    def _report_lost(self, image: Image) -> None:
        """跟丢了：调 Qwen VL 描述一帧画面，把"看不见你 + 环境描述"注入给 agent。"""
        try:
            self._ensure_models_loaded()
            assert self._vl is not None
            description = self._vl.query(
                image,
                "请用一句简短的话描述你看到的环境，比如：'前面是走廊，左边有桌子'。直接给描述，不要寒暄。",
            )
        except Exception as e:
            logger.warning(f"VL describe failed: {e}")
            description = "（看不清楚周围）"

        # 把"丢失 + 描述"作为 user 发给 agent 的话，agent 会用 LLM 回复
        injected = (
            f"（系统通知：我跟丢了「{self._anchor_description}」，已经原地停下。"
            f"我现在看到的画面是：{description.strip()} "
            f"请用一句话告诉用户你看不见 ta 了，描述一下你周围的环境，"
            f"并说你正在原地等 ta 回到画面里。）"
        )
        self._send_message_as_user(injected)
        logger.info("Sent lost-report to agent", description=description)

    # 视觉 helpers

    def _ensure_models_loaded(self) -> None:
        if self._detector is None:
            # 用纯 detection 模型（不是 pose），并降低 conf 阈值。
            # pose 模型对"狗相机机位低、看不到人头"这种场景天然劣势，
            # 站起来后 pose 模型经常 conf<0.3 把整人扔掉，跟随就完全断了。
            self._detector = YoloPersonDetector(
                model_name=self._yolo_model,
                conf=self._yolo_conf,
            )
        if self._reid is None:
            from dimos.models.embedding.treid import TorchReIDModel

            # main 仓的 TorchReIDModel 是 Configurable，无参构造走默认 config（osnet_x1_0）
            self._reid = TorchReIDModel()
        if self._vl is None:
            from dimos.models.vl.create import create as create_vl

            self._vl = create_vl("qwen")

    def _detect_persons(self, image: Image) -> list[Bbox4]:
        """YOLO 找所有人，返回 (x1, y1, x2, y2) 列表。"""
        assert self._detector is not None
        result = self._detector.process_image(image)
        bboxes: list[Bbox4] = []
        for det in result.detections:
            x1, y1, x2, y2 = det.bbox
            bboxes.append((int(x1), int(y1), int(x2), int(y2)))
        return bboxes

    def _pick_anchor_bbox(
        self,
        image: Image,
        detections: list[Bbox4],
        description: str,
    ) -> Bbox4 | None:
        """从所有 person 候选里选锚点：有描述用 VL 匹配，没描述选最居中+最大。"""
        if description.strip():
            try:
                from dimos.navigation.visual.query import get_object_bbox_from_image

                assert self._vl is not None
                bbox = get_object_bbox_from_image(self._vl, image, description)
                if bbox is not None:
                    x1, y1, x2, y2 = bbox
                    return (int(x1), int(y1), int(x2), int(y2))
            except Exception as e:
                logger.warning(f"VL pick failed, falling back to center+largest: {e}")

        # fallback：综合考虑"大小"和"距离画面中心"两个因子，越大越好、越居中越好
        img_cx = image.width / 2

        def score(b: Bbox4) -> float:
            x1, y1, x2, y2 = b
            cx = (x1 + x2) / 2
            area = float((x2 - x1) * (y2 - y1))
            return area - abs(cx - img_cx) * 5.0

        return max(detections, key=score) if detections else None

    def _reid_embed(self, image: Image, bbox: Bbox4) -> torch.Tensor:
        """裁出 bbox 区域，用 TorchReID 提一个 OSNet embedding。

        注意：dimos 里 TorchReIDModel.embed() 返回的是 Embedding 包装对象
        （base.py 里的 dimos.models.embedding.base.Embedding），里面才包着真正的
        torch.Tensor，不是裸 tensor。这里要解包，否则 torch.as_tensor(<Embedding>)
        会报 "Could not infer dtype of Embedding"，导致 follow_me 锁不上目标、不跟。
        """
        from dimos.models.embedding.base import Embedding as _Embedding

        assert self._reid is not None
        x1, y1, x2, y2 = bbox
        # 裁剪坐标做防御性 clamp
        x1 = max(0, x1)
        y1 = max(0, y1)
        x2 = min(image.width, x2)
        y2 = min(image.height, y2)
        w = max(1, x2 - x1)
        h = max(1, y2 - y1)
        # Image.crop(x, y, width, height)
        crop = image.crop(x1, y1, w, h)
        emb = self._reid.embed(crop)
        if isinstance(emb, list):
            emb = emb[0]
        # 新版 dimos：embed 返回 Embedding 包装对象，里面的 vector 才是 tensor/ndarray
        if isinstance(emb, _Embedding):
            return emb.to_torch().detach().cpu()
        if not isinstance(emb, torch.Tensor):
            emb = torch.as_tensor(emb)
        return emb.detach().cpu()

    def _sleep_until(self, t: float) -> None:
        delta = t - time.monotonic()
        if delta > 0:
            time.sleep(delta)

    def _stop_following_internal(self) -> None:
        if self._should_stop is not None:
            self._should_stop.set()


__all__ = ["FollowMeConfig", "FollowMeSkillContainer"]
