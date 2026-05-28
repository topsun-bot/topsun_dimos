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

from dataclasses import dataclass
import math
import threading
import time
from typing import Any, cast

from reactivex.disposable import Disposable

from dimos.agents.annotation import skill
from dimos.agents.skills.speak_skill_spec import SpeakSkillSpec
from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.core.stream import In, Out
from dimos.models.qwen.bbox import BBox
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Quaternion import Quaternion
from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.msgs.geometry_msgs.Vector3 import Vector3, make_vector3
from dimos.msgs.nav_msgs.OccupancyGrid import CostValues, OccupancyGrid
from dimos.msgs.sensor_msgs.Image import Image
from dimos.navigation.frontier_exploration.wavefront_frontier_goal_selector import (
    WavefrontFrontierExplorer,
)
from dimos.navigation.navigation_spec import NavigationInterfaceSpec
from dimos.navigation.topology import TopologyGraph
from dimos.navigation.visual.query import (
    get_object_bbox_from_image,
    get_object_detection_from_image,
    vlm_object_present_in_view,
)
from dimos.perception.object_tracking_spec import ObjectTrackingSpec
from dimos.perception.spatial_memory_spec import SpatialMemorySpec
from dimos.robot.unitree.unitree_skill_container import UnitreeSkillContainer
from dimos.types.door_memory_spec import SpatialLandmarkMemorySpec
from dimos.types.robot_location import RobotLocation
from dimos.types.spatial_record import RecordType, SpatialRecord
from dimos.utils.generic import extract_json_from_llm_response
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

# navigate_with_text fallback step order (env: DIMOS_NAV_FALLBACK=semantic|room_first)
_NAV_FALLBACK_SEMANTIC = (
    "landmark",  # L3: landmark JSON + topology + re-scan
    "in_frame",  # L2: VLM bbox + object tracking
    "room_sweep",  # L4: visit each ROOM + 360 scan
    "vlm_memory",  # L5: batch VLM on stored images
    "clip_map",  # L6: CLIP semantic map
    "tagged",  # L1: CLIP tagged location (rooms; last to reduce false positives)
)
_NAV_FALLBACK_ROOM_FIRST = (
    "tagged",
    "in_frame",
    "landmark",
    "room_sweep",
    "vlm_memory",
    "clip_map",
)
_NAV_FALLBACK_STRATEGIES = {
    "semantic": _NAV_FALLBACK_SEMANTIC,
    "room_first": _NAV_FALLBACK_ROOM_FIRST,
}
_NAV_TERMINAL_FAILURE_PREFIX = "NAVIGATION_FAILED:"
_OBJECT_VISUAL_NAV_TIMEOUT_S = 120.0
_OBJECT_LOCAL_SCAN_TIMEOUT_S = 120.0
_OBJECT_ROOM_SWEEP_TIMEOUT_S = 120.0
_VISUAL_SERVO_FORWARD_NO_PROGRESS_LIMIT = 20
_VISUAL_SERVO_ODOM_STALL_LIMIT = 3
_VISUAL_SERVO_ODOM_PROGRESS_EPS_M = 0.03
_VISUAL_SERVO_CENTERED_ERR = 0.18
_VISUAL_SERVO_CLOSE_ENOUGH_CENTERED_ERR = 0.20
_VISUAL_SERVO_RECENTER_ERR = 0.24
_VISUAL_SERVO_REDETECT_ERR = 0.55
_VISUAL_SERVO_ARRIVAL_AREA_RATIO = 0.16
_VISUAL_SERVO_ARRIVAL_HEIGHT_RATIO = 0.45
_VISUAL_SERVO_ARRIVAL_WIDTH_RATIO = 0.40
_VISUAL_SERVO_STALL_ARRIVAL_HEIGHT_RATIO = 0.35
_VISUAL_SERVO_SLOW_FORWARD_MPS = 0.25
_VISUAL_SERVO_MID_FORWARD_MPS = 0.45
_VISUAL_SERVO_NEAR_FORWARD_MPS = 0.45
_VISUAL_SERVO_FAST_FORWARD_MPS = 0.55
_LOCAL_SEARCH_SCAN_OFFSETS_DEG = (
    0.0,
    30.0,
    60.0,
    90.0,
    120.0,
    150.0,
    180.0,
    210.0,
    240.0,
    270.0,
    300.0,
    330.0,
)
_LOCAL_SEARCH_RESCAN_OFFSETS_DEG = _LOCAL_SEARCH_SCAN_OFFSETS_DEG[1:]


@dataclass(frozen=True)
class _ObjectNavigationResult:
    found: bool
    arrived: bool
    message: str | None = None
    failure_reason: str | None = None
    made_progress: bool = False
    requires_final_confirmation: bool = True


@dataclass(frozen=True)
class _VisualAcquireResult:
    message: str
    navigation_failed: bool = False


def _terminal_navigation_failure(message: str) -> str:
    return f"{_NAV_TERMINAL_FAILURE_PREFIX}{message}"


def _is_terminal_navigation_failure(message: str | None) -> bool:
    return bool(message and message.startswith(_NAV_TERMINAL_FAILURE_PREFIX))


def _strip_terminal_navigation_failure(message: str) -> str:
    if message.startswith(_NAV_TERMINAL_FAILURE_PREFIX):
        return message[len(_NAV_TERMINAL_FAILURE_PREFIX) :]
    return message


_VLM_OBJECT_LIST_PROMPT = (
    "列出图中所有可单独指认的物体（家具、电器、设备、装饰、人等）。\n"
    "【硬性要求】JSON 里每个 name 必须是 1–4 个汉字的中文名词。"
    "禁止英文：不要写 computer/desk/chair/monitor/table。\n"
    "正确示例：电脑、书桌、办公椅、灭火器、电视。\n"
    "description 必须尽量包含可见的颜色、形状/轮廓、材质、结构特征，"
    "例如：黑色长方形显示器、蓝色圆形塑料凳、白色金属置物架。"
    "如果颜色或材质不确定，用“颜色不明显”或“材质不确定”，不要编造。\n"
    "Return ONLY a JSON array: "
    '[{"name": "<中文名>", "description": "<简短中文说明>", "bbox": [x1, y1, x2, y2]}]. '
    "bbox 为物体在画面中的边界框（像素坐标）。跳过墙面、地面、天花板、门。若无物体，返回 []."
)

# Camera horizontal FOV in degrees for bbox-to-bearing conversion.
_CAMERA_HFOV_DEG = float(__import__("os").getenv("DIMOS_CAMERA_HFOV_DEG", "69"))

# Fallback when VLM still returns English object names (legacy tags / weak compliance).
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
    """Prefer Chinese landmark names; map common English VLM outputs."""
    name = raw.strip()
    if not name:
        return name
    mapped = _VLM_NAME_EN_TO_ZH.get(name.lower())
    if mapped:
        if mapped != name:
            logger.info("[VLM] normalized object name %r → %r", name, mapped)
        return mapped
    if name.replace(" ", "").isascii() and not any("\u4e00" <= c <= "\u9fff" for c in name):
        logger.warning(
            "[VLM] object name %r is still English — re-tag room after restart if this persists",
            name,
        )
    return name


def _create_vl_model() -> Any:
    """Create the VLM instance based on environment configuration.

    Key ↔ URL pairing is strict — DashScope key only goes to DashScope
    endpoint, OpenAI key only goes to OpenAI endpoint, never mixed.

    ======================== ===================== ============================
    Purpose                   Env var                Default
    ======================== ===================== ============================
    Provider                  DIMOS_VLM_PROVIDER     auto: DASHSCOPE_API_KEY
    Model name                DIMOS_VLM_MODEL_NAME   qwen3.6-plus / gpt-4o-mini
    ======================== ===================== ============================

    Provider auto-detection:
    DASHSCOPE_API_KEY       → dashscope (兼容模式)
    DIMOS_VLM_API_KEY      → openai
    OPENAI_API_KEY          → openai
    ALIBABA_API_KEY         → qwen
    """
    import os

    from dimos.models.vl.openai import OpenAIVlModel

    provider = os.getenv("DIMOS_VLM_PROVIDER", "").lower().strip()
    model_name = os.getenv("DIMOS_VLM_MODEL_NAME")

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
        # Use native DashScope MultiModalConversation API (not compatible-mode)
        from dimos.models.vl.dashscope import DashScopeVlModel

        model = DashScopeVlModel()
        if model_name:
            model.config.model_name = model_name
        return model

    if provider == "openai":
        model = OpenAIVlModel()
        if model_name:
            model.config.model_name = model_name
        return model

    if provider == "moondream":
        from dimos.models.vl.moondream import MoondreamVlModel

        return MoondreamVlModel()

    # Default: Qwen (legacy, ALIBABA_API_KEY)
    from dimos.models.vl.qwen import QwenVlModel

    model = QwenVlModel()
    if model_name:
        model.config.model_name = model_name
    return model


def _log_vlm_runtime_config(vl_model: Any) -> None:
    """Log which VLM backend is active (no secrets)."""
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
    _latest_image: Image | None = None
    _latest_odom: PoseStamped | None = None
    _latest_global_costmap: OccupancyGrid | None = None
    _skill_started: bool = False
    _similarity_threshold: float = 0.23
    # Visual relocalization vs odom (room reference images)
    _drift_soft_m: float = 0.3
    _drift_hard_m: float = 1.0
    _relocalize_interval_s: float = 3.0
    _room_visual_max_distance: float = 0.35

    _spatial_memory: SpatialMemorySpec
    _landmark_memory: SpatialLandmarkMemorySpec
    _navigation: NavigationInterfaceSpec
    _object_tracking: ObjectTrackingSpec | None = None
    _speak_skill: SpeakSkillSpec | None = None
    _unitree_skill_container: UnitreeSkillContainer | None = None
    _frontier_explorer: WavefrontFrontierExplorer | None = None
    _memory_session_id: str = ""
    _memory_blindspot_patrol_stop: bool = False
    _visual_servo_cancel_event: threading.Event

    color_image: In[Image]
    odom: In[PoseStamped]
    global_costmap: In[OccupancyGrid]
    tele_cmd_vel: Out[Twist]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._skill_started = False
        self._memory_session_id = f"session_{int(time.time())}"
        self._visual_servo_cancel_event = threading.Event()

        self._vl_model = _create_vl_model()
        _log_vlm_runtime_config(self._vl_model)

    @rpc
    def start(self) -> None:
        super().start()
        self.register_disposable(Disposable(self.color_image.subscribe(self._on_color_image)))
        self.register_disposable(Disposable(self.odom.subscribe(self._on_odom)))
        self.register_disposable(Disposable(self.global_costmap.subscribe(self._on_global_costmap)))
        self._skill_started = True

    @rpc
    def stop(self) -> None:
        self._cancel_visual_servo_motion()
        super().stop()

    def _on_color_image(self, image: Image) -> None:
        self._latest_image = image

    def _odom_euler_tuple(self) -> tuple[float, float, float] | None:
        if self._latest_odom is None:
            return None
        euler = self._latest_odom.orientation.to_euler()
        return (float(euler.x), float(euler.y), float(euler.z))

    def _on_odom(self, odom: PoseStamped) -> None:
        self._latest_odom = odom

    def _on_global_costmap(self, costmap: OccupancyGrid) -> None:
        self._latest_global_costmap = costmap

    def _memory_locations(self) -> list[dict[str, float | str]]:
        try:
            return self._spatial_memory.get_memory_locations()
        except Exception:
            logger.exception("Failed to read spatial memory locations")
            return []

    def _is_costmap_goal_cell_safe(
        self,
        costmap: OccupancyGrid,
        gx: int,
        gy: int,
        clearance_m: float,
    ) -> bool:
        if not (0 <= gx < costmap.width and 0 <= gy < costmap.height):
            return False
        if int(costmap.grid[gy, gx]) == CostValues.UNKNOWN:
            return False
        if int(costmap.grid[gy, gx]) >= CostValues.OCCUPIED:
            return False

        clearance_cells = max(1, math.ceil(clearance_m / max(costmap.resolution, 1e-6)))
        for ny in range(
            max(0, gy - clearance_cells), min(costmap.height, gy + clearance_cells + 1)
        ):
            for nx in range(
                max(0, gx - clearance_cells), min(costmap.width, gx + clearance_cells + 1)
            ):
                if (nx - gx) ** 2 + (ny - gy) ** 2 > clearance_cells**2:
                    continue
                if int(costmap.grid[ny, nx]) >= CostValues.OCCUPIED:
                    return False
        return True

    def _costmap_cell_has_unknown_neighbor(
        self,
        costmap: OccupancyGrid,
        gx: int,
        gy: int,
        radius_cells: int = 2,
    ) -> bool:
        for ny in range(max(0, gy - radius_cells), min(costmap.height, gy + radius_cells + 1)):
            for nx in range(max(0, gx - radius_cells), min(costmap.width, gx + radius_cells + 1)):
                if int(costmap.grid[ny, nx]) == CostValues.UNKNOWN:
                    return True
        return False

    def _memory_coverage_reason(
        self,
        x: float,
        y: float,
        memory_locations: list[dict[str, float | str]],
        coverage_radius_m: float,
        stale_after_sec: float,
        now: float,
    ) -> tuple[str | None, float | None]:
        nearest_distance: float | None = None
        for loc in memory_locations:
            try:
                lx = float(loc.get("pos_x", loc.get("x", 0.0)))
                ly = float(loc.get("pos_y", loc.get("y", 0.0)))
                ts = float(loc.get("timestamp", 0.0) or 0.0)
            except (TypeError, ValueError):
                continue
            distance = math.hypot(x - lx, y - ly)
            if nearest_distance is None or distance < nearest_distance:
                nearest_distance = distance
            if distance <= coverage_radius_m:
                if stale_after_sec <= 0 or ts <= 0 or now - ts <= stale_after_sec:
                    return None, distance

        if nearest_distance is not None and nearest_distance <= coverage_radius_m:
            return "stale", nearest_distance
        return "missing", nearest_distance

    def _find_nearest_memory_blindspot(
        self,
        search_radius_m: float = 5.0,
        coverage_radius_m: float = 1.0,
        stale_after_sec: float = 600.0,
        clearance_m: float = 0.35,
        exclude_recent_goals: list[tuple[float, float]] | None = None,
    ) -> dict[str, Any] | None:
        if self._latest_odom is None or self._latest_global_costmap is None:
            return None

        costmap = self._latest_global_costmap
        robot = self._latest_odom.position
        robot_grid = costmap.world_to_grid((robot.x, robot.y, robot.z))
        center_gx = round(robot_grid.x)
        center_gy = round(robot_grid.y)
        radius_cells = max(1, math.ceil(search_radius_m / max(costmap.resolution, 1e-6)))
        stride = max(1, round(0.25 / max(costmap.resolution, 1e-6)))
        now = time.time()
        memory_locations = self._memory_locations()
        recent = exclude_recent_goals or []

        best: dict[str, Any] | None = None
        for gy in range(
            max(0, center_gy - radius_cells),
            min(costmap.height, center_gy + radius_cells + 1),
            stride,
        ):
            for gx in range(
                max(0, center_gx - radius_cells),
                min(costmap.width, center_gx + radius_cells + 1),
                stride,
            ):
                world = costmap.grid_to_world((gx, gy, 0.0))
                distance_to_robot = math.hypot(world.x - float(robot.x), world.y - float(robot.y))
                if distance_to_robot > search_radius_m:
                    continue
                if distance_to_robot < max(0.5, coverage_radius_m * 0.5):
                    continue
                if any(
                    math.hypot(world.x - rx, world.y - ry) < coverage_radius_m for rx, ry in recent
                ):
                    continue
                if not self._is_costmap_goal_cell_safe(costmap, gx, gy, clearance_m):
                    continue

                reason, nearest_memory_distance = self._memory_coverage_reason(
                    float(world.x),
                    float(world.y),
                    memory_locations,
                    coverage_radius_m,
                    stale_after_sec,
                    now,
                )
                if reason is None:
                    continue

                is_frontier = self._costmap_cell_has_unknown_neighbor(costmap, gx, gy)
                target_type = "memory_frontier" if is_frontier else "memory_gap"
                score = distance_to_robot
                if is_frontier:
                    score -= 0.75
                if reason == "missing":
                    score -= 0.25

                candidate = {
                    "pose": PoseStamped(
                        position=make_vector3(float(world.x), float(world.y), float(robot.z)),
                        orientation=self._latest_odom.orientation,
                        frame_id=costmap.frame_id or self._latest_odom.frame_id,
                    ),
                    "distance_m": distance_to_robot,
                    "grid": (gx, gy),
                    "reason": reason,
                    "target_type": target_type,
                    "nearest_memory_distance_m": nearest_memory_distance,
                    "score": score,
                }
                if best is None or score < float(best["score"]):
                    best = candidate
        return best

    def _announce_object_found(self, target_name: str) -> None:
        speaker = self._speak_skill
        if speaker is None:
            logger.info(
                "Object found announcement skipped for '%s': no SpeakSkill wired", target_name
            )
            return
        try:
            speaker.speak("找到了", blocking=False)
        except Exception:
            logger.exception("Failed to announce object found for '%s'", target_name)

    @skill
    def tag_location(self, location_name: str, num_photos: int = 0) -> str:
        """Tag this location with a name and store reference images.

        If ``num_photos`` is 0 (default), takes a single photo at the current
        heading. If ``num_photos`` ≥ 2, the robot rotates 360° in place taking
        that many evenly-spaced photos — use this for room-level tagging so the
        room can be recognized from any approach angle.

        Each photo is stored as a room reference image for visual relocalization,
        and the current frame is also scanned with VLM to detect objects.

        Args:
            location_name (str): the name for the location (e.g. "office", "kitchen").
            num_photos (int): number of panoramic photos (0 = single shot, 2~12 = 360° capture).

        Returns:
            str: the outcome
        """

        if not self._skill_started:
            raise ValueError(f"{self} has not been started.")

        if not self._latest_odom:
            return "No odometry data received yet, cannot tag location."

        num_photos = max(0, min(num_photos, 12))
        # Each entry: (image ndarray, position, rotation) at capture time.
        captured_frames: list[
            tuple[Any, tuple[float, float, float], tuple[float, float, float]]
        ] = []

        has_cam = self._latest_image is not None and hasattr(self._latest_image, "data")
        cam_shape = getattr(self._latest_image.data, "shape", None) if has_cam else None  # type: ignore[union-attr]
        logger.info(
            "[tag_location] START name=%r num_photos=%d has_camera=%s image_shape=%s",
            location_name,
            num_photos,
            has_cam,
            cam_shape,
        )

        def _snap_one(name: str) -> bool:
            """Take one photo; always queue frame for VLM when camera data exists."""
            pos = self._latest_odom.position if self._latest_odom else None
            rot_tuple = self._odom_euler_tuple()
            if pos is None or rot_tuple is None:
                return False

            location = RobotLocation(
                name=name,
                position=(pos.x, pos.y, pos.z),
                rotation=rot_tuple,
            )
            self._spatial_memory.tag_location(location)

            image_saved = False
            has_frame = self._latest_image is not None and hasattr(self._latest_image, "data")
            if has_frame:
                captured_frames.append(
                    (
                        self._latest_image.data.copy(),  # type: ignore[union-attr]
                        (float(pos.x), float(pos.y), float(pos.z)),
                        rot_tuple,
                    )
                )
                try:
                    image_saved = self._spatial_memory.tag_location_with_image(
                        location,
                        self._latest_image.data,  # type: ignore[union-attr]
                    )
                except Exception:
                    logger.exception("Failed to store room reference image for '%s'", name)

            logger.info(
                "[tag_location] snap name=%r image_saved=%s captured_total=%d",
                name,
                image_saved,
                len(captured_frames),
            )
            return image_saved

        image_saved = _snap_one(location_name)
        position = self._latest_odom.position
        rot_tuple = self._odom_euler_tuple() or (0.0, 0.0, 0.0)
        logger.info(f"Tagged location '{location_name}' at ({position.x:.2f},{position.y:.2f})")

        # One ROOM landmark per name (record() merges by name; snaps only add CLIP images).
        room_rec = SpatialRecord(
            name=location_name,
            record_type=RecordType.ROOM,
            position=(float(position.x), float(position.y), float(position.z)),
            rotation=rot_tuple,
            session_id=self._memory_session_id,
        )
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

        if num_photos >= 2:
            angle_step = 360.0 / num_photos
            done = 1
            for _i in range(1, num_photos):
                ok = self._rotate_in_place_degrees(angle_step)
                if not ok:
                    break
                time.sleep(0.3)  # let camera settle
                _snap_one(location_name)
                done += 1
            logger.info("Panorama: %d/%d photos captured for '%s'", done, num_photos, location_name)

        if captured_frames:
            logger.info(
                "[tag_location] scheduling VLM batch: room=%r frames=%d (async thread)",
                location_name,
                len(captured_frames),
            )
            threading.Thread(
                target=self._detect_objects_panorama_batch_async,
                args=(captured_frames, location_name),
                daemon=True,
                name=f"vlm-panorama-{location_name}",
            ).start()
        else:
            logger.warning(
                "[tag_location] VLM batch SKIPPED for %r: no camera frames captured "
                "(check color_image stream / simulation camera)",
                location_name,
            )

        extra = f", {num_photos} panoramic photos" if num_photos >= 2 else ""
        suffix = ""
        if captured_frames:
            suffix += f", VLM batch on {len(captured_frames)} frame(s) in background"
            if not image_saved:
                suffix += " (CLIP room images failed — check Chroma/CLIP logs)"
            else:
                suffix += " (with CLIP room images)"
        return f"Tagged '{location_name}': ({position.x:.2f},{position.y:.2f}){extra}{suffix}."

    def _rotate_in_place_degrees(self, degrees: float) -> bool:
        """Rotate the robot in place by the given angle. Returns True on success."""
        us = self._unitree_skill_container
        if us is None:
            return False
        # GO2: UnitreeSkillContainer.relative_move(forward, left, degrees)
        if hasattr(us, "relative_move"):
            try:
                us.relative_move(forward=0.0, left=0.0, degrees=degrees)
                return True
            except Exception:
                logger.exception("relative_move rotation failed")
                return False
        # G1: UnitreeG1SkillContainer.move(x, y, yaw, duration)
        if hasattr(us, "move"):
            try:
                us.move(x=0.0, y=0.0, yaw=degrees)
                return True
            except Exception:
                logger.exception("move rotation failed")
                return False
        return False

    @skill
    def tag_room(self, name: str, num_photos: int = 6) -> str:
        """Tag the current room with 360° panoramic capture.

        Convenience wrapper that calls ``tag_location(name, num_photos=N)``.
        """
        return self.tag_location(name, num_photos=max(2, min(num_photos, 12)))

    def _nav_fallback_strategy(self) -> str:
        import os

        name = os.getenv("DIMOS_NAV_FALLBACK", "semantic").lower().strip()
        if name not in _NAV_FALLBACK_STRATEGIES:
            logger.warning("Unknown DIMOS_NAV_FALLBACK=%r, using 'semantic'", name)
            return "semantic"
        return name

    def _nav_fallback_step_tagged(self, query: str) -> str | None:
        logger.info("[tagged] Checking tagged locations for %r ...", query)
        msg = self._navigate_by_tagged_location(query)
        if msg:
            logger.info("[tagged] ✓ HIT: %s", msg)
        else:
            logger.info("[tagged] ✗ MISS")
        return msg

    def _nav_fallback_step_in_frame(self, query: str, *, timeout: float = 30.0) -> str | None:
        resolved = self._resolve_landmark_from_query(query)
        if resolved is not None and resolved.record_type == RecordType.ROOM:
            logger.info("[in_frame] skip — %r is a room name, not an in-frame object", query)
            return None
        logger.info("[in_frame] VLM bbox + tracking for %r (timeout=%.0fs) ...", query, timeout)
        msg = self._navigate_to_object(
            query,
            timeout=timeout,
            arrival_action="point",
            run_arrival_action=True,
        )
        if msg:
            logger.info("[in_frame] ✓ HIT: %s", msg)
        else:
            logger.info("[in_frame] ✗ MISS")
        return msg

    def _nav_fallback_step_landmark(self, query: str) -> str | None:
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
        nav_landmark_msg = self._navigate_to_landmark(
            landmark_target,
            arrival_action="point" if is_object_landmark else "stop",
            arrival_distance=0.5,
            run_arrival_action=is_object_landmark,
            enable_visual_drift=False,
        )
        if _is_terminal_navigation_failure(nav_landmark_msg):
            logger.warning("[landmark] ⚠ Visual navigation failed; stop fallback chain")
            return nav_landmark_msg
        if (
            "severe visual/odom drift" in nav_landmark_msg.lower()
            or "aborted" in nav_landmark_msg.lower()
            or "navigation skipped" in nav_landmark_msg.lower()
            or "could not visually acquire" in nav_landmark_msg.lower()
            or "local search failed" in nav_landmark_msg.lower()
        ):
            logger.warning("[landmark] ⚠ Navigation failed, fall through")
            return None

        if landmark_target.record_type == RecordType.ROOM:
            logger.info("[landmark] ✓ Reached room '%s'", landmark_target.name)
            return nav_landmark_msg

        logger.info(
            "[landmark] ✓ Reached object landmark '%s'; stop fallback chain", landmark_target.name
        )
        return nav_landmark_msg

    def _nav_fallback_step_room_sweep(self, query: str) -> str | None:
        logger.info("[room_sweep] Sweeping room anchors for %r ...", query)
        msg = self._room_anchor_sweep_for_object(query)
        if msg:
            logger.info("[room_sweep] ✓ HIT: %s", msg)
        else:
            logger.info("[room_sweep] ✗ MISS")
        return msg

    def _nav_fallback_step_vlm_memory(self, query: str) -> str | None:
        logger.info("[vlm_memory] Batch VLM on stored images for %r ...", query)
        msg = self._query_memory_images_with_vlm(query)
        if msg:
            logger.info("[vlm_memory] ✓ HIT: %s", msg)
        else:
            logger.info("[vlm_memory] ✗ MISS")
        return msg

    def _nav_fallback_step_clip_map(self, query: str) -> str | None:
        logger.info("[clip_map] CLIP semantic map for %r ...", query)
        msg = self._navigate_using_semantic_map(query)
        if msg:
            logger.info("[clip_map] ✓ HIT: %s", msg)
        else:
            logger.info("[clip_map] ✗ MISS")
        return msg

    def _run_navigate_fallback_chain(self, query: str) -> str | None:
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
            "landmark": lambda: self._nav_fallback_step_landmark(query),
            "room_sweep": lambda: self._nav_fallback_step_room_sweep(query),
            "vlm_memory": lambda: self._nav_fallback_step_vlm_memory(query),
            "clip_map": lambda: self._nav_fallback_step_clip_map(query),
        }

        for step_name in order:
            msg: str | None = steps[step_name]()
            if msg:
                return msg
        return None

    @skill
    def navigate_with_text(self, query: str) -> str:
        """Navigate using natural language (multi-stage fallback).

        Default order (``DIMOS_NAV_FALLBACK=semantic``): landmark memory → in-frame
        VLM tracking → room sweep → VLM on stored images → CLIP map → tagged room.

        Use ``DIMOS_NAV_FALLBACK=room_first`` for tagged rooms before landmarks.

        CALL THIS SKILL FOR ONE SUBJECT AT A TIME.
        Args:
            query: Text query (object name, room name, or description).
        """

        if not self._skill_started:
            raise ValueError(f"{self} has not been started.")

        logger.info("=" * 50)
        logger.info("NAVIGATE_WITH_TEXT START  query=%r", query)
        logger.info("=" * 50)

        success_msg = self._run_navigate_fallback_chain(query)
        if success_msg:
            if _is_terminal_navigation_failure(success_msg):
                failure_msg = _strip_terminal_navigation_failure(success_msg)
                logger.info("=" * 50)
                logger.info("NAVIGATE_WITH_TEXT END  query=%r  result=FAILED", query)
                logger.info("=" * 50)
                return failure_msg
            logger.info("=" * 50)
            logger.info("NAVIGATE_WITH_TEXT END  query=%r  result=HIT", query)
            logger.info("=" * 50)
            return success_msg

        logger.info("=" * 50)
        logger.info("NAVIGATE_WITH_TEXT END  query=%r  result=ALL_MISS", query)
        logger.info("=" * 50)
        return (
            f"Could not reach '{query}' (landmark, in-frame, room sweep, "
            f"VLM memory, CLIP map, or tagged location all missed)."
        )

    def _navigate_by_tagged_location(self, query: str) -> str | None:
        robot_location = self._spatial_memory.query_tagged_location(query)

        if not robot_location:
            return None

        # Guard: require at least some textual overlap between query and matched name.
        # Pure CLIP semantic matching is too loose (e.g. "电脑屏幕" matched "卧室").
        matched_name = robot_location.name.lower()
        query_lower = query.lower()
        if matched_name != query_lower:
            # Check character-level overlap for CJK, word-level for alphabetic
            if matched_name.replace(" ", "").isascii() and query_lower.replace(" ", "").isascii():
                q_words = set(query_lower.split())
                m_words = set(matched_name.split())
                overlap = q_words & m_words
            else:
                overlap = set(query_lower) & set(matched_name)
            if not overlap:
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
        logger.info(
            f"Navigating to pose: ({pose.position.x:.2f}, {pose.position.y:.2f}, {pose.position.z:.2f})"
        )
        self._navigation.set_goal(pose)

        return (
            f"{message}. Started navigating to that position. "
            f"To cancel movement call the 'stop_navigation' tool."
        )

    def _navigate_to_object_result(
        self,
        query: str,
        *,
        timeout: float = 30.0,
        max_attempts: int = 10,
        max_motion_updates: int = 10,
        vlm_query: str | None = None,
    ) -> _ObjectNavigationResult:
        if self._object_tracking is None:
            return _ObjectNavigationResult(
                found=False,
                arrived=False,
                failure_reason="object tracker is not wired",
            )

        last_failure: str | None = None
        found_once = False
        vlm_failures = 0
        motion_updates = 0
        previous_metrics: tuple[float, float, float] | None = None
        pending_motion_validation = False
        query_for_vlm = vlm_query or query
        while vlm_failures < max_attempts and motion_updates < max_motion_updates:
            try:
                bbox = self._get_bbox_for_current_frame(query_for_vlm)
            except Exception as exc:
                logger.error(f"Failed to get bbox for {query}", exc_info=True)
                return _ObjectNavigationResult(
                    found=False,
                    arrived=False,
                    failure_reason=f"VLM bbox query failed: {exc}",
                )

            if bbox is None:
                if not found_once:
                    logger.info("[L2]   VLM did not find %r in current frame", query)
                    return _ObjectNavigationResult(found=False, arrived=False)
                vlm_failures += 1
                last_failure = "VLM did not find the object again after a navigation retry"
                logger.info(
                    "[L2]   VLM did not find %r after visual adjustment (failure %d/%d)",
                    query,
                    vlm_failures,
                    max_attempts,
                )
                continue

            found_once = True
            current_metrics = self._bbox_visual_progress_metrics(bbox)
            if current_metrics is not None and previous_metrics is not None:
                prev_error, prev_area, prev_height = previous_metrics
                curr_error, curr_area, curr_height = current_metrics
                centered_better = curr_error <= prev_error - 0.015
                bigger_better = curr_area > prev_area * 1.05 or curr_height > prev_height + 0.02
                if centered_better or bigger_better:
                    logger.info(
                        "[L2]   Visual motion made progress for %r: "
                        "center_err %.2f→%.2f, area %.3f→%.3f, height %.2f→%.2f",
                        query,
                        prev_error,
                        curr_error,
                        prev_area,
                        curr_area,
                        prev_height,
                        curr_height,
                    )
                    vlm_failures = 0
                    pending_motion_validation = False
                elif pending_motion_validation:
                    vlm_failures += 1
                    logger.info(
                        "[L2]   Visual motion did not improve %r "
                        "(failure %d/%d): center_err %.2f→%.2f, "
                        "area %.3f→%.3f, height %.2f→%.2f",
                        query,
                        vlm_failures,
                        max_attempts,
                        prev_error,
                        curr_error,
                        prev_area,
                        curr_area,
                        prev_height,
                        curr_height,
                    )
                    pending_motion_validation = False
            if current_metrics is not None:
                previous_metrics = current_metrics
            if vlm_failures >= max_attempts:
                last_failure = (
                    f"visual motion did not improve the target after {max_attempts} failures"
                )
                break

            if not self._bbox_reasonable_for_tracking(bbox):
                vlm_failures += 1
                last_failure = f"VLM bbox is not suitable for tracking: {bbox}"
                logger.warning(
                    "[L2]   VLM bbox for %r too large/small for tracking (%s) — skip in_frame",
                    query,
                    bbox,
                )
                continue

            if self._bbox_close_enough_for_arrival(bbox):
                logger.info(
                    "[L2]   ✓ VLM bbox for %r is close enough; accepting arrival without "
                    "bbox navigation (%s)",
                    query,
                    bbox,
                )
                return _ObjectNavigationResult(
                    found=True,
                    arrived=True,
                    message=f"Visually confirmed '{query}' close enough",
                )

            logger.info(
                "[L2]   ✓ VLM found %r at bbox=%s, starting object tracking "
                "(motion update %d/%d, failures %d/%d) ...",
                query,
                bbox,
                motion_updates + 1,
                max_motion_updates,
                vlm_failures,
                max_attempts,
            )

            try:
                track_result = self._object_tracking.track(bbox)  # type: ignore[arg-type]
            except Exception as exc:
                logger.exception("[L2]   Object tracking failed to start for %r", query)
                vlm_failures += 1
                last_failure = f"object tracking failed to start: {exc}"
                continue
            if isinstance(track_result, dict) and track_result.get("status") != "tracking_started":
                logger.warning("[L2]   Tracker did not start for %r: %s", query, track_result)
                vlm_failures += 1
                last_failure = f"tracker did not start: {track_result}"
                continue

            servo_result = self._visual_servo_to_tracked_object(query, timeout=timeout)
            if servo_result.arrived:
                if (
                    not servo_result.requires_final_confirmation
                    or self._confirm_object_in_current_frame(query_for_vlm)
                ):
                    return servo_result
                last_failure = "final VLM confirmation failed after visual servo"
                logger.warning(
                    "[L2]   Final VLM confirmation failed for %r after visual servo success; "
                    "retrying",
                    query,
                )
                vlm_failures += 1
                time.sleep(0.5)
                continue
            last_failure = servo_result.failure_reason

            if servo_result.made_progress:
                motion_updates += 1
                pending_motion_validation = True
                logger.info(
                    "[L2]   Retrying VLM bbox navigation for %r after effective visual "
                    "adjustment: %s",
                    query,
                    last_failure,
                )
                time.sleep(0.5)
                continue

            vlm_failures += 1
            if vlm_failures < max_attempts:
                logger.info(
                    "[L2]   Retrying VLM bbox navigation for %r after failure (failure %d/%d): %s",
                    query,
                    vlm_failures,
                    max_attempts,
                    last_failure,
                )
                time.sleep(0.5)

        if motion_updates >= max_motion_updates:
            last_failure = (
                f"visual adjustment did not converge after {max_motion_updates} effective moves"
            )

        return _ObjectNavigationResult(
            found=found_once or last_failure is not None,
            arrived=False,
            failure_reason=last_failure or "object navigation failed",
        )

    def _navigate_to_object(
        self,
        query: str,
        *,
        timeout: float = 30.0,
        vlm_query: str | None = None,
        announce: bool = True,
        arrival_action: str = "stop",
        run_arrival_action: bool = False,
    ) -> str | None:
        result = self._navigate_to_object_result(query, timeout=timeout, vlm_query=vlm_query)
        if not result.arrived:
            return None
        action_msg = self._run_arrival_action(arrival_action, query) if run_arrival_action else None
        if announce:
            self._announce_object_found(query)
        if action_msg is not None:
            if result.message:
                return f"{action_msg} ({result.message})"
            return action_msg
        return result.message

    def _stop_visual_servo_motion(self) -> None:
        try:
            self.tele_cmd_vel.publish(Twist.zero())
        except AttributeError:
            logger.debug("Skipping visual servo stop: tele_cmd_vel is not wired")

    def _get_visual_servo_cancel_event(self) -> threading.Event:
        event = getattr(self, "_visual_servo_cancel_event", None)
        if event is None:
            event = threading.Event()
            self._visual_servo_cancel_event = event
        return event

    def _cancel_visual_servo_motion(self) -> None:
        self._get_visual_servo_cancel_event().set()
        self._stop_visual_servo_motion()

    def _publish_visual_servo_motion(
        self,
        *,
        forward_mps: float,
        yaw_radps: float,
        duration_s: float,
    ) -> None:
        # The project-level Twist convention uses linear.x for forward/backward
        # (see keyboard_teleop). Send repeated short commands so the 0.2s
        # connection watchdog does not stop the robot mid-pulse.
        twist = Twist(
            linear=Vector3(forward_mps, 0.0, 0.0),
            angular=Vector3(0.0, 0.0, yaw_radps),
        )
        end_time = time.monotonic() + max(0.0, duration_s)
        cancel_event = self._get_visual_servo_cancel_event()
        while time.monotonic() < end_time and not cancel_event.is_set():
            try:
                self.tele_cmd_vel.publish(twist)
            except AttributeError:
                logger.debug("Skipping visual servo motion: tele_cmd_vel is not wired")
                break
            time.sleep(0.05)
        self._stop_visual_servo_motion()

    def _visual_servo_to_tracked_object(
        self,
        query: str,
        *,
        timeout: float,
    ) -> _ObjectNavigationResult:
        if self._object_tracking is None:
            return _ObjectNavigationResult(
                found=True,
                arrived=False,
                failure_reason="object tracker is not wired",
            )
        cancel_event = self._get_visual_servo_cancel_event()
        cancel_event.clear()
        if self._latest_image is None or not hasattr(self._latest_image, "data"):
            self._object_tracking.stop_track()
            return _ObjectNavigationResult(
                found=True,
                arrived=False,
                failure_reason="camera image is not available for visual servoing",
            )

        image_h, image_w = self._latest_image.data.shape[:2]
        start_time = time.time()
        tracking_lost_at: float | None = None
        last_horizontal_error = 0.0
        waiting_bbox_logged = False
        recovery_moves = 0
        forward_no_progress_count = 0
        forward_odom_stall_count = 0
        last_forward_metrics: tuple[float, float, float] | None = None
        centered_visible_since: float | None = None
        centered_timeout_bbox: BBox | None = None
        centered_timeout_error = 0.0
        centered_timeout_height = 0.0
        last_failure = "visual servoing did not converge"

        while time.time() - start_time < timeout:
            if cancel_event.is_set():
                last_failure = "visual servoing was cancelled"
                logger.info("[L2]   Visual servo for '%s' cancelled", query)
                break
            if not self._object_tracking.is_tracking():
                if tracking_lost_at is None:
                    tracking_lost_at = time.time()
                    logger.info(
                        "[L2]   Tracking lost for %r, starting 5s grace period ...",
                        query,
                    )
                elif time.time() - tracking_lost_at > 5.0:
                    if recovery_moves < 3 and abs(last_horizontal_error) > 0.05:
                        recovery_yaw = max(-0.28, min(0.28, -last_horizontal_error * 0.35))
                        logger.warning(
                            "[L2]   Tracking lost >5s; recovery yaw %.2frad/s toward last "
                            "known target side",
                            recovery_yaw,
                        )
                        try:
                            self._publish_visual_servo_motion(
                                forward_mps=0.0,
                                yaw_radps=recovery_yaw,
                                duration_s=0.7,
                            )
                        except Exception as exc:
                            logger.exception("[L2]   Visual recovery movement failed for %r", query)
                            last_failure = f"visual recovery movement failed: {exc}"
                            break
                        recovery_moves += 1
                        tracking_lost_at = time.time()
                    else:
                        last_failure = "tracking was lost for more than 5 seconds"
                        logger.warning("[L2]   ✗ Tracking lost >5s")
                        break
                time.sleep(0.25)
                continue

            tracking_lost_at = None
            recovery_moves = 0
            try:
                bbox = self._object_tracking.get_latest_bbox()
            except Exception as exc:
                logger.exception("[L2]   Failed to read tracker bbox for %r", query)
                last_failure = f"failed to read tracker bbox: {exc}"
                break
            if bbox is None:
                if not waiting_bbox_logged:
                    logger.info("[L2]   Waiting for tracker bbox for %r ...", query)
                    waiting_bbox_logged = True
                time.sleep(0.25)
                continue
            waiting_bbox_logged = False
            try:
                tracked_bbox: BBox = (
                    float(bbox[0]),
                    float(bbox[1]),
                    float(bbox[2]),
                    float(bbox[3]),
                )
            except (IndexError, TypeError, ValueError) as exc:
                logger.warning("[L2]   Invalid tracker bbox for %r: %s", query, bbox)
                last_failure = f"invalid tracker bbox: {exc}"
                break

            if self._bbox_close_enough_for_arrival(tracked_bbox):
                logger.info("[L2]   ✓ Visual servo reached '%s' with bbox=%s", query, tracked_bbox)
                self._object_tracking.stop_track()
                self._stop_visual_servo_motion()
                return _ObjectNavigationResult(
                    found=True,
                    arrived=True,
                    message=f"Successfully arrived at '{query}'",
                    requires_final_confirmation=False,
                )

            x1, y1, x2, y2 = tracked_bbox
            center_x = (x1 + x2) / 2.0
            horizontal_error = (center_x - image_w / 2.0) / max(1.0, image_w / 2.0)
            last_horizontal_error = horizontal_error
            height_ratio = max(0.0, min(1.0, (y2 - y1) / float(image_h)))
            if abs(horizontal_error) <= _VISUAL_SERVO_CENTERED_ERR and height_ratio >= 0.25:
                if centered_visible_since is None:
                    centered_visible_since = time.time()
                centered_timeout_bbox = tracked_bbox
                centered_timeout_error = horizontal_error
                centered_timeout_height = height_ratio
            else:
                centered_visible_since = None
                centered_timeout_bbox = None

            abs_horizontal_error = abs(horizontal_error)
            if abs_horizontal_error > _VISUAL_SERVO_REDETECT_ERR:
                forward = 0.0
                yaw = max(-0.40, min(0.40, -horizontal_error * 0.50))
                recenter_after_turn = True
            elif abs_horizontal_error > _VISUAL_SERVO_RECENTER_ERR:
                forward = 0.0
                yaw = max(-0.35, min(0.35, -horizontal_error * 0.65))
                recenter_after_turn = False
            elif abs_horizontal_error > _VISUAL_SERVO_CENTERED_ERR:
                forward = _VISUAL_SERVO_SLOW_FORWARD_MPS
                yaw = max(-0.28, min(0.28, -horizontal_error * 0.65))
                recenter_after_turn = False
            else:
                forward = (
                    _VISUAL_SERVO_NEAR_FORWARD_MPS
                    if height_ratio > 0.42
                    else _VISUAL_SERVO_FAST_FORWARD_MPS
                )
                yaw = max(-0.18, min(0.18, -horizontal_error * 0.40))
                recenter_after_turn = False

            if forward > 0.0:
                metrics = self._bbox_visual_progress_metrics(tracked_bbox)
                if metrics is not None and last_forward_metrics is not None:
                    _prev_error, prev_area, prev_height = last_forward_metrics
                    _curr_error, curr_area, curr_height = metrics
                    forward_made_progress = (
                        curr_area > prev_area * 1.04 or curr_height > prev_height + 0.02
                    )
                    if forward_made_progress:
                        forward_no_progress_count = 0
                    else:
                        forward_no_progress_count += 1
                        logger.info(
                            "[L2]   Forward visual servo made no visible progress for '%s' "
                            "(%d/%d): area %.3f→%.3f, height %.2f→%.2f",
                            query,
                            forward_no_progress_count,
                            _VISUAL_SERVO_FORWARD_NO_PROGRESS_LIMIT,
                            prev_area,
                            curr_area,
                            prev_height,
                            curr_height,
                        )
                if metrics is not None:
                    last_forward_metrics = metrics
            else:
                forward_no_progress_count = 0
                forward_odom_stall_count = 0
                last_forward_metrics = None

            logger.info(
                "[L2]   Visual servo '%s': bbox=%s err=%.2f height=%.2f "
                "cmd=(forward=%.2fm/s, yaw=%.2frad/s)",
                query,
                bbox,
                horizontal_error,
                height_ratio,
                forward,
                yaw,
            )
            odom_before = self._latest_odom
            try:
                self._publish_visual_servo_motion(
                    forward_mps=forward,
                    yaw_radps=yaw,
                    duration_s=1.0 if forward > 0.0 else 0.6,
                )
            except Exception as exc:
                logger.exception("[L2]   Visual servo movement failed for %r", query)
                last_failure = f"visual servo movement failed: {exc}"
                break
            if forward > 0.0 and odom_before is not None and self._latest_odom is not None:
                odom_delta = self._pose_delta_xy(odom_before, self._latest_odom)
                if (
                    odom_delta < _VISUAL_SERVO_ODOM_PROGRESS_EPS_M
                    and abs(horizontal_error) <= _VISUAL_SERVO_CENTERED_ERR
                    and height_ratio >= _VISUAL_SERVO_STALL_ARRIVAL_HEIGHT_RATIO
                ):
                    forward_odom_stall_count += 1
                    logger.info(
                        "[L2]   Forward command did not move odom for '%s' "
                        "(%d/%d): delta=%.3fm, err=%.2f, height=%.2f",
                        query,
                        forward_odom_stall_count,
                        _VISUAL_SERVO_ODOM_STALL_LIMIT,
                        odom_delta,
                        horizontal_error,
                        height_ratio,
                    )
                else:
                    forward_odom_stall_count = 0
                if forward_odom_stall_count >= _VISUAL_SERVO_ODOM_STALL_LIMIT:
                    logger.info(
                        "[L2]   ✓ Treating '%s' as reached: forward commands were issued "
                        "but odom did not advance while target stayed centered",
                        query,
                    )
                    self._object_tracking.stop_track()
                    self._stop_visual_servo_motion()
                    return _ObjectNavigationResult(
                        found=True,
                        arrived=True,
                        message=(
                            f"Reached '{query}' as close as possible; forward commands did "
                            "not move the robot"
                        ),
                        requires_final_confirmation=False,
                    )
            if (
                forward > 0.0
                and forward_no_progress_count >= _VISUAL_SERVO_FORWARD_NO_PROGRESS_LIMIT
            ):
                if abs(last_horizontal_error) > _VISUAL_SERVO_CENTERED_ERR:
                    last_failure = (
                        "forward motion made no visible progress while target was still off-center"
                    )
                    logger.info(
                        "[L2]   Forward visual servo stalled for '%s' while off-center "
                        "(err=%.2f); retrying VLM detection",
                        query,
                        last_horizontal_error,
                    )
                    self._object_tracking.stop_track()
                    self._stop_visual_servo_motion()
                    return _ObjectNavigationResult(
                        found=True,
                        arrived=False,
                        failure_reason=last_failure,
                        made_progress=True,
                    )
                logger.info(
                    "[L2]   ✓ Treating '%s' as reached: target is centered but repeated "
                    "forward commands made no visible progress",
                    query,
                )
                self._object_tracking.stop_track()
                self._stop_visual_servo_motion()
                return _ObjectNavigationResult(
                    found=True,
                    arrived=True,
                    message=(
                        f"Reached '{query}' as close as possible; forward motion made no "
                        "visible progress"
                    ),
                    requires_final_confirmation=False,
                )
            if recenter_after_turn:
                last_failure = "target was off-center; rotated once and will retry VLM detection"
                logger.info(
                    "[L2]   Recentered view for '%s' with one bounded yaw pulse; "
                    "retrying VLM detection",
                    query,
                )
                self._object_tracking.stop_track()
                self._stop_visual_servo_motion()
                return _ObjectNavigationResult(
                    found=True,
                    arrived=False,
                    failure_reason=last_failure,
                    made_progress=True,
                )
            time.sleep(0.2)

        self._object_tracking.stop_track()
        self._stop_visual_servo_motion()
        if time.time() - start_time >= timeout:
            stable_centered_s = (
                time.time() - centered_visible_since if centered_visible_since is not None else 0.0
            )
            if centered_timeout_bbox is not None and stable_centered_s >= 5.0:
                logger.warning(
                    "[L2]   Visual servo for '%s' reached timeout after %.0fs, but target "
                    "stayed centered and visible for %.1fs (bbox=%s err=%.2f height=%.2f); "
                    "accepting pending final VLM confirmation",
                    query,
                    timeout,
                    stable_centered_s,
                    centered_timeout_bbox,
                    centered_timeout_error,
                    centered_timeout_height,
                )
                return _ObjectNavigationResult(
                    found=True,
                    arrived=True,
                    message=(
                        f"Visually kept '{query}' centered near timeout; "
                        "accepting after final VLM confirmation"
                    ),
                )
            last_failure = f"visual servoing timed out after {timeout:.0f}s"
            logger.warning("[L2]   ✗ Visual servo to '%s' timed out after %.0fs", query, timeout)
        return _ObjectNavigationResult(found=True, arrived=False, failure_reason=last_failure)

    def _resolve_landmark_from_query(self, query: str) -> SpatialRecord | None:
        q = query.strip()
        if not q:
            return None
        target = self._landmark_memory.resolve_by_query(q)
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
        name = room_name.strip()
        if not name:
            return None
        hit = self._landmark_memory.resolve_by_query(name)
        if hit is not None and hit.record_type == RecordType.ROOM:
            return hit
        for rec in self._landmark_memory.query_by_type(RecordType.ROOM):
            if rec.name == name:
                return rec
        return None

    def _room_anchor_sweep_for_object(self, query: str) -> str | None:
        rooms = self._landmark_memory.query_by_type(RecordType.ROOM)
        if not rooms:
            logger.info("[L4]   No room-type landmarks — sweep skipped")
            return None
        # Look up stored object bearing + description if available
        obj_rec = self._resolve_landmark_from_query(query)
        stored_yaw: float | None = None
        description: str | None = None
        if obj_rec is not None and obj_rec.record_type == RecordType.LANDMARK:
            stored_yaw = obj_rec.rotation[2] if abs(obj_rec.rotation[2]) > 1e-6 else None
            description = obj_rec.state or None
        logger.info("[L4]   Sweeping %d room(s) ...", len(rooms))
        for ri, room in enumerate(rooms):
            rname = room.name or room.record_id
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
            )
            if "severe visual/odom drift" in nav_msg.lower():
                logger.warning("Room sweep: drift abort at %r; trying next room", rname)
                continue
            # Use stored bearing if available, then VLM acquire + track
            if stored_yaw is not None:
                vis_msg = self._visual_acquire_object(query, stored_yaw, description=description)
                if vis_msg:
                    return vis_msg
            self._rotate_scan_in_place()
            found = self._navigate_to_object(
                query,
                timeout=_OBJECT_ROOM_SWEEP_TIMEOUT_S,
                arrival_action="point",
                run_arrival_action=True,
            )
            if found:
                return found
        return None

    def _rotate_scan_in_place(self, steps: int = 6, settle_s: float = 0.2) -> None:
        """Spin in place by stepping through ``steps`` rotations of 360/steps degrees.

        A single ``relative_move(degrees=360)`` call is a no-op: the planner
        normalises yaw modulo 2π, so the goal yaw equals the start yaw and the
        robot does not actually rotate. Stepping by 60° (default) avoids the
        wrap-around so the robot really sweeps a full circle.
        """
        us = self._unitree_skill_container
        if us is None:
            logger.warning("360 scan: no UnitreeSkillContainer wired; pausing briefly instead")
            time.sleep(1.0)
            return
        step_deg = 360.0 / float(max(1, steps))
        for _ in range(steps):
            try:
                if hasattr(us, "relative_move"):
                    us.relative_move(forward=0.0, left=0.0, degrees=step_deg)
                elif hasattr(us, "move"):
                    us.move(x=0.0, y=0.0, yaw=step_deg)
                else:
                    return
            except Exception:
                logger.exception("360 scan rotation failed")
                return
            time.sleep(settle_s)

    def _planar_distance_to_pose(self, pose: PoseStamped) -> float:
        if self._latest_odom is None:
            return float("inf")
        dx = float(self._latest_odom.position.x) - float(pose.position.x)
        dy = float(self._latest_odom.position.y) - float(pose.position.y)
        return math.hypot(dx, dy)

    def _pose_delta_xy(self, before: PoseStamped, after: PoseStamped) -> float:
        dx = float(after.position.x) - float(before.position.x)
        dy = float(after.position.y) - float(before.position.y)
        return math.hypot(dx, dy)

    def _yaw_toward_point(self, gx: float, gy: float) -> float:
        """Heading (rad) from current odom to map point (gx, gy)."""
        if self._latest_odom is None:
            return 0.0
        dx = gx - float(self._latest_odom.position.x)
        dy = gy - float(self._latest_odom.position.y)
        if math.hypot(dx, dy) < 1e-6:
            return float(self._latest_odom.orientation.to_euler().z)
        return math.atan2(dy, dx)

    def _vlm_query_for_object_record(self, target_name: str, target: SpatialRecord) -> str:
        description = (target.state or "").strip()
        if not description:
            return target_name
        query = f"{target_name}，外观特征：{description}"
        logger.info("[VLM] richer object query for '%s': %s", target_name, query)
        return query

    def _rotate_to_yaw(self, yaw: float, *, min_degrees: float = 5.0) -> bool:
        """Rotate in place to an absolute map yaw using the current odom heading."""
        euler = self._odom_euler_tuple()
        if euler is None:
            return False
        diff = yaw - euler[2]
        diff = math.atan2(math.sin(diff), math.cos(diff))
        diff_deg = math.degrees(diff)
        if abs(diff_deg) < min_degrees:
            return True
        return self._rotate_in_place_degrees(diff_deg)

    def _scan_yaws_for_object(
        self,
        target_name: str,
        *,
        center_yaw: float,
        offsets_deg: tuple[float, ...],
        timeout_per_view: float = _OBJECT_LOCAL_SCAN_TIMEOUT_S,
        stop_on_found_failure: bool = True,
        vlm_query: str | None = None,
    ) -> str | None:
        result = self._scan_yaws_for_object_result(
            target_name,
            center_yaw=center_yaw,
            offsets_deg=offsets_deg,
            timeout_per_view=timeout_per_view,
            stop_on_found_failure=stop_on_found_failure,
            vlm_query=vlm_query,
        )
        return result.message if result is not None else None

    def _scan_yaws_for_object_result(
        self,
        target_name: str,
        *,
        center_yaw: float,
        offsets_deg: tuple[float, ...],
        timeout_per_view: float = _OBJECT_LOCAL_SCAN_TIMEOUT_S,
        stop_on_found_failure: bool = True,
        vlm_query: str | None = None,
    ) -> _VisualAcquireResult | None:
        """Rotate through a small yaw fan and try VLM+tracking at each view."""
        last_found_failure: _VisualAcquireResult | None = None
        for offset_deg in offsets_deg:
            yaw = center_yaw + math.radians(offset_deg)
            logger.info(
                "[local_search] scanning '%s' at yaw %.1f° (offset %.0f°)",
                target_name,
                math.degrees(yaw),
                offset_deg,
            )
            self._rotate_to_yaw(yaw)
            time.sleep(0.3)
            result = self._navigate_to_object_result(
                target_name,
                timeout=timeout_per_view,
                vlm_query=vlm_query,
            )
            if result.arrived:
                return _VisualAcquireResult(f"Visually acquired '{target_name}' during local scan")
            if result.found:
                message = (
                    f"Visually found '{target_name}' during local scan, "
                    f"but navigation failed after repeated VLM retries: {result.failure_reason}"
                )
                last_found_failure = _VisualAcquireResult(message, navigation_failed=True)
                logger.warning("[local_search] %s", message)
                if stop_on_found_failure:
                    return last_found_failure
                continue
        if last_found_failure is not None:
            logger.info(
                "[local_search] continuing after visual failures for '%s'; "
                "trying alternate viewpoints",
                target_name,
            )
        return None

    def _rank_local_search_candidates(
        self,
        candidates: list[tuple[float, float, float]],
        *,
        robot_x: float,
        robot_y: float,
        max_attempts: int,
    ) -> list[tuple[float, float, float]]:
        """Pick nearby viewpoints while spreading attempts around the landmark."""
        ranked: list[tuple[float, float, float]] = []
        remaining = candidates[:]
        while remaining and len(ranked) < max_attempts:
            if not ranked:
                best = min(remaining, key=lambda p: math.hypot(p[0] - robot_x, p[1] - robot_y))
            else:

                def score(p: tuple[float, float, float]) -> float:
                    distance = math.hypot(p[0] - robot_x, p[1] - robot_y)
                    min_angle_gap = min(
                        abs(math.atan2(math.sin(p[2] - used[2]), math.cos(p[2] - used[2])))
                        for used in ranked
                    )
                    return distance - 0.7 * min_angle_gap

                best = min(remaining, key=score)
            ranked.append(best)
            remaining.remove(best)
        return ranked

    def _local_search_for_object_near_landmark(
        self,
        target: SpatialRecord,
        target_name: str,
        *,
        radius_m: float = 3.0,
    ) -> str | None:
        result = self._local_search_for_object_near_landmark_result(
            target,
            target_name,
            radius_m=radius_m,
        )
        return result.message if result is not None else None

    def _local_search_for_object_near_landmark_result(
        self,
        target: SpatialRecord,
        target_name: str,
        *,
        radius_m: float = 3.0,
    ) -> _VisualAcquireResult | None:
        """Search nearby viewpoints when an object is not visible at its saved coordinate."""
        if self._latest_odom is None:
            return None

        tx, ty, tz = target.position
        logger.info(
            "[local_search] searching for '%s' within %.1fm of saved landmark (%.2f, %.2f)",
            target_name,
            radius_m,
            tx,
            ty,
        )

        vlm_query = self._vlm_query_for_object_record(target_name, target)
        stored_yaw = target.rotation[2] if abs(target.rotation[2]) > 1e-6 else None
        center_yaw = stored_yaw if stored_yaw is not None else self._yaw_toward_point(tx, ty)
        initial_offsets = (
            _LOCAL_SEARCH_RESCAN_OFFSETS_DEG
            if stored_yaw is not None
            else _LOCAL_SEARCH_SCAN_OFFSETS_DEG
        )
        found = self._scan_yaws_for_object_result(
            target_name,
            center_yaw=center_yaw,
            offsets_deg=initial_offsets,
            timeout_per_view=_OBJECT_LOCAL_SCAN_TIMEOUT_S,
            stop_on_found_failure=False,
            vlm_query=vlm_query,
        )
        if found:
            return found

        rx = float(self._latest_odom.position.x)
        ry = float(self._latest_odom.position.y)
        radii = (0.8, 1.5, 2.3, radius_m)
        angles = tuple(i * math.pi / 4.0 for i in range(8))
        candidates: list[tuple[float, float, float]] = []
        seen_cells: set[tuple[float, float]] = set()
        for search_radius in radii:
            if search_radius <= 0.0 or search_radius > radius_m:
                continue
            for angle in angles:
                sx = float(tx) + search_radius * math.cos(angle)
                sy = float(ty) + search_radius * math.sin(angle)
                key = (round(sx, 2), round(sy, 2))
                if key in seen_cells:
                    continue
                seen_cells.add(key)
                candidates.append((sx, sy, angle))

        ranked_candidates = self._rank_local_search_candidates(
            candidates,
            robot_x=rx,
            robot_y=ry,
            max_attempts=10,
        )
        for idx, (sx, sy, _) in enumerate(ranked_candidates, start=1):
            yaw = math.atan2(float(ty) - sy, float(tx) - sx)
            search_pose = PoseStamped(
                position=make_vector3(sx, sy, float(tz)),
                orientation=Quaternion.from_euler(Vector3(0.0, 0.0, yaw)),
                frame_id="map",
            )
            logger.info(
                "[local_search] viewpoint %d/%d for '%s' at (%.2f, %.2f)",
                idx,
                len(ranked_candidates),
                target_name,
                sx,
                sy,
            )
            self._navigation.set_goal(search_pose)
            _, severe = self._wait_goal_with_relocalize(
                search_pose,
                [0.0],
                time.time() + 10.0,
                self._relocalize_interval_s,
                destination_pose=search_pose,
                enable_visual_drift=False,
            )
            self._navigation.cancel_goal()
            if severe:
                return None

            found = self._scan_yaws_for_object_result(
                target_name,
                center_yaw=yaw,
                offsets_deg=_LOCAL_SEARCH_SCAN_OFFSETS_DEG,
                timeout_per_view=_OBJECT_LOCAL_SCAN_TIMEOUT_S,
                stop_on_found_failure=False,
                vlm_query=vlm_query,
            )
            if found:
                return _VisualAcquireResult(
                    f"{found.message} from a nearby viewpoint",
                    navigation_failed=found.navigation_failed,
                )

        logger.info("[local_search] '%s' not found within %.1fm", target_name, radius_m)
        return None

    def _room_name_at_position(self, x: float, y: float, *, radius: float = 2.5) -> str | None:
        """ROOM landmark whose anchor is near (x, y)."""
        best: str | None = None
        best_d = radius
        for rec in self._landmark_memory.query_by_type(RecordType.ROOM):
            d = math.hypot(rec.position[0] - x, rec.position[1] - y)
            if d < best_d:
                best_d = d
                best = rec.name
        return best

    def _coordinate_frame_stale_reason(self, target: SpatialRecord) -> str | None:
        """Detect persisted coordinates that no longer match the current odom frame."""
        if self._latest_odom is None:
            return None

        if target.session_id and target.session_id == self._memory_session_id:
            return None

        if target.session_id and target.session_id != self._memory_session_id:
            logger.info(
                "Landmark '%s' was recorded in session %s; current session is %s",
                target.name,
                target.session_id,
                self._memory_session_id,
            )

        if self._latest_image is None or not hasattr(self._latest_image, "data"):
            return None

        try:
            visual_room = self._spatial_memory.query_location_by_image(self._latest_image.data)
        except Exception:
            logger.debug("stale coordinate visual check failed", exc_info=True)
            return None
        if visual_room is None:
            return None

        distance = float(visual_room.metadata.get("distance", 1.0))
        if distance > self._room_visual_max_distance:
            return None

        room_rec = self._resolve_room_landmark(visual_room.name)
        if room_rec is None:
            return None

        ox = float(self._latest_odom.position.x)
        oy = float(self._latest_odom.position.y)
        drift = math.hypot(ox - room_rec.position[0], oy - room_rec.position[1])
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
        """Optionally shift the active goal from room-image relocalization.

        Each successful visual match also feeds the room reference set:
        the current frame is stored as another reference image for the
        matched room, growing coverage organically as the robot moves.

        Returns (possibly updated goal, severe_drift).
        """
        if not enable_visual_drift:
            return (active_goal, False)

        now = time.time()
        if now - last_corr[0] < interval:
            return (active_goal, False)
        last_corr[0] = now

        # Approaching a landmark: odom is naturally far from the room tag until arrival.
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

        conf = float(loc.metadata.get("distance", 1.0))
        if conf > self._room_visual_max_distance:
            return (active_goal, False)

        # Odometry pre-filter: if CLIP says we're in a room whose tagged
        # position is >5m from current odometry, it's almost certainly a
        # false positive — CLIP confused two similar-looking rooms.
        vx = float(loc.position[0])
        vy = float(loc.position[1])
        ox = float(self._latest_odom.position.x)
        oy = float(self._latest_odom.position.y)
        clip_to_odom = math.hypot(ox - vx, oy - vy)

        room_name = loc.name

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

        if delta < self._drift_soft_m:
            return (active_goal, False)

        if delta < self._drift_hard_m:
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

        logger.warning("Severe drift Δ=%.2fm between odom and visual room match; cancelling", delta)
        self._navigation.cancel_goal()
        return (active_goal, True)

    def _inch_goal_toward(
        self,
        standoff_pose: PoseStamped,
        arrival_distance: float,
    ) -> PoseStamped | None:
        if self._latest_odom is None:
            return None
        rx = float(self._latest_odom.position.x)
        ry = float(self._latest_odom.position.y)
        gx = float(standoff_pose.position.x)
        gy = float(standoff_pose.position.y)
        dx, dy = gx - rx, gy - ry
        dist = math.hypot(dx, dy)
        if dist <= arrival_distance + 0.02:
            return None
        if dist < 1e-6:
            return None
        ux, uy = dx / dist, dy / dist
        v = max(0.15, min(0.6, dist * 0.4))
        step = min(v * 0.35, max(0.0, dist - arrival_distance * 0.85))
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
    ) -> tuple[PoseStamped, bool]:
        """Wait until goal reached, deadline, or severe drift. Returns (goal, severe_drift)."""
        ag: PoseStamped = active_goal
        while time.time() < segment_deadline:
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
        return (ag, False)

    def _run_arrival_action(self, action: str, target_name: str) -> str:
        a = (action or "stop").lower().strip()
        self._navigation.cancel_goal()
        if a in ("stop", "none", ""):
            return f"Reached '{target_name}' (arrival_action=stop)."
        us = self._unitree_skill_container
        if us is None:
            logger.warning("arrival_action=%s but UnitreeSkillContainer is not wired", action)
            return (
                f"Reached '{target_name}' (arrival_action={action!r} skipped: no gesture module)."
            )
        if a in ("point", "present"):
            point_out = us.execute_sport_command("Hello")
            time.sleep(0.5)
            recovery_out = us.execute_sport_command("RecoveryStand")
            return (
                f"Reached '{target_name}', executing arrival_action={action!r}: "
                f"{point_out} {recovery_out}"
            )

        if a in ("sit_point", "sit_and_point", "sit_point_experimental"):
            sit_out = us.execute_sport_command("Sit")
            time.sleep(1.0)
            point_out = us.execute_sport_command("Hello")
            time.sleep(1.0)
            recovery_out = us.execute_sport_command("RecoveryStand")
            return (
                f"Reached '{target_name}', executing arrival_action={action!r}: "
                f"{sit_out} {point_out} {recovery_out}"
            )

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
            return f"Reached '{target_name}' (unknown arrival_action={action!r})."
        out = us.execute_sport_command(cmd)
        return f"Reached '{target_name}', executing arrival_action={action!r}: {out}"

    def _vlm_sees_in_view(self, query: str) -> BBox | None:
        """Bbox-returning VLM check (used by :meth:`_navigate_to_object` to
        seed the OpenCV tracker — the bbox is required there).

        For yes/no presence questions where the bbox is irrelevant
        (:meth:`_visual_acquire_object`, :meth:`verify_object_in_view`), use
        :meth:`_vlm_object_present_in_view` instead — it is a single VLM call
        with stricter prompting and supports a free-text disambiguation hint.
        """
        if self._latest_image is None:
            return None
        try:
            return self._get_bbox_for_current_frame(query)
        except Exception:
            logger.exception("[visual_acquire] VLM bbox check failed for %r", query)
            return None

    def _vlm_object_present_in_view(self, query: str, *, description: str | None = None) -> bool:
        """Single-call yes/no presence check on the latest camera frame.

        Args:
            query: Object name (e.g. ``"水桶"``).
            description: Optional disambiguation hint, typically the stored
                landmark ``state`` (``"桌上的透明矿泉水瓶"``). Helps the VLM
                tell similar objects apart (水瓶 vs 水桶, 书桌 vs 会议桌).
        """
        if self._latest_image is None:
            return False
        try:
            return vlm_object_present_in_view(
                self._vl_model, self._latest_image, query, description=description
            )
        except Exception:
            logger.exception("[visual_acquire] VLM presence check failed for %r", query)
            return False

    def _visual_acquire_object(
        self,
        target_name: str,
        stored_yaw: float | None = None,
        *,
        description: str | None = None,
        announce: bool = True,
    ) -> str | None:
        """After arriving at the landmark, confirm the target is in the camera.

        Pure visual-confirmation flow (no walking, no tracker):
          1. (Optional) face the stored bearing if it differs from current yaw.
          2. VLM-check the current frame. If found → success.
          3. Otherwise rotate 60° in place, settle, VLM-check again.
             Repeat up to 6 times (full 360° sweep).
          4. If all 6 steps complete without sighting → failure (return None).

        Sighting is established purely from the VLM bbox detection — the
        OpenCV tracker (used by :meth:`_navigate_to_object`) is irrelevant
        for "does the camera see it?" and its init is fragile on low-contrast
        bboxes, so we do not require tracker init success here.

        Args:
            target_name: The object name to search for.
            stored_yaw: World-frame yaw (radians) where the object was
                originally observed, if known.
            description: Optional disambiguation hint passed to the VLM (the
                stored landmark ``state``, e.g. ``"桌上的透明矿泉水瓶"``).
                Reduces same-radical confusions (水瓶 vs 水桶) at no extra
                latency cost — it just rides along on the same VLM call.

        Returns:
            Success message if the camera sees the object, else ``None``.
        """
        if stored_yaw is not None:
            euler = self._odom_euler_tuple()
            if euler is not None:
                diff = math.atan2(
                    math.sin(stored_yaw - euler[2]),
                    math.cos(stored_yaw - euler[2]),
                )
                diff_deg = math.degrees(diff)
                if abs(diff_deg) > 5.0:
                    logger.info(
                        "[visual_acquire] turning %.1f° to face '%s' (stored yaw=%.1f°)",
                        diff_deg,
                        target_name,
                        math.degrees(stored_yaw),
                    )
                    self._rotate_in_place_degrees(diff_deg)
                    time.sleep(0.5)

        if self._vlm_object_present_in_view(target_name, description=description):
            logger.info("[visual_acquire] ✓ '%s' found in current view", target_name)
            if announce:
                self._announce_object_found(target_name)
            return f"Visually acquired '{target_name}'"

        logger.info(
            "[visual_acquire] '%s' not in current view; starting 6×60° rotational scan",
            target_name,
        )
        steps = 6
        step_deg = 60.0
        for i in range(steps):
            if not self._rotate_in_place_degrees(step_deg):
                logger.warning(
                    "[visual_acquire] rotation step %d/%d failed for '%s'",
                    i + 1,
                    steps,
                    target_name,
                )
                return None
            time.sleep(0.3)  # let the camera publish a fresh frame
            if self._vlm_object_present_in_view(target_name, description=description):
                rotated = int(step_deg * (i + 1))
                logger.info(
                    "[visual_acquire] ✓ '%s' found at step %d/%d (%d° rotated)",
                    target_name,
                    i + 1,
                    steps,
                    rotated,
                )
                if announce:
                    self._announce_object_found(target_name)
                return f"Visually acquired '{target_name}' after {rotated}° scan"

        logger.warning("[visual_acquire] ✗ '%s' not found after full 6×60° scan", target_name)
        return None

    def _try_acquire_object_in_view(
        self,
        target_name: str,
        stored_yaw: float | None = None,
        vlm_query: str | None = None,
    ) -> str | None:
        result = self._try_acquire_object_in_view_result(
            target_name,
            stored_yaw=stored_yaw,
            vlm_query=vlm_query,
        )
        return result.message if result is not None else None

    def _try_acquire_object_in_view_result(
        self,
        target_name: str,
        stored_yaw: float | None = None,
        vlm_query: str | None = None,
    ) -> _VisualAcquireResult | None:
        """Try the current view only; local search handles wider scanning."""
        if stored_yaw is not None:
            euler = self._odom_euler_tuple()
            if euler is not None:
                diff = stored_yaw - euler[2]
                diff = math.atan2(math.sin(diff), math.cos(diff))
                diff_deg = math.degrees(diff)
                if abs(diff_deg) > 5.0:
                    logger.info(
                        "[visual_acquire_once] turning %.1f° to face '%s' (stored yaw=%.1f°)",
                        diff_deg,
                        target_name,
                        math.degrees(stored_yaw),
                    )
                    self._rotate_in_place_degrees(diff_deg)
                    time.sleep(0.5)

        try:
            result = self._navigate_to_object_result(
                target_name,
                timeout=_OBJECT_VISUAL_NAV_TIMEOUT_S,
                vlm_query=vlm_query,
            )
            if result.arrived:
                logger.info("[visual_acquire_once] ✓ '%s' found in current view", target_name)
                return _VisualAcquireResult(f"Visually acquired '{target_name}'")
            if result.found:
                logger.warning(
                    "[visual_acquire_once] '%s' found, but navigation failed: %s",
                    target_name,
                    result.failure_reason,
                )
                return _VisualAcquireResult(
                    (
                        f"Visually found '{target_name}', but navigation failed after "
                        f"repeated VLM retries: {result.failure_reason}"
                    ),
                    navigation_failed=True,
                )
        except Exception:
            logger.exception("[visual_acquire_once] VLM acquire error for '%s'", target_name)
        return None

    def _parse_vlm_object_list_response(self, response: str | None) -> list[dict[str, Any]]:
        parsed = extract_json_from_llm_response(response or "")
        if parsed is None:
            return []
        if isinstance(parsed, list):
            return [o for o in parsed if isinstance(o, dict)]
        if isinstance(parsed, dict):
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
        """Use the capture pose for the frame where VLM saw the object."""
        indices = obj.get("image_indices")
        if frame_poses and isinstance(indices, list) and indices:
            try:
                idx = int(indices[0])
            except (TypeError, ValueError):
                idx = -1
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
        extra_metadata: dict[str, Any] | None = None,
    ) -> int:
        stored = 0
        hfov_rad = math.radians(_CAMERA_HFOV_DEG)
        for obj in objects:
            name = _normalize_vlm_object_name((obj.get("name") or "").strip())
            if not name:
                continue
            desc = (obj.get("description") or "").strip()
            indices = obj.get("image_indices")
            obj_pos, obj_rot, view_idx = self._pose_for_vlm_object(
                obj, position, rotation, frame_poses
            )
            if indices:
                desc = f"{desc} (views {indices})".strip() if desc else f"views {indices}"

            # Compute object bearing from bbox if available
            bbox = obj.get("bbox")
            # Single frame without explicit image_indices defaults to view 0
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
                            # Normalize to pixel coords (0-1 fraction, 0-1000, or absolute)
                            mx = max(x1, y1, x2, y2)
                            if mx <= 1.0:
                                x1, x2 = x1 * w, x2 * w
                            elif mx <= 1000.0 and min(x1, y1, x2, y2) >= 0.0:
                                x1, x2 = x1 / 1000.0 * w, x2 / 1000.0 * w
                            if 0 <= x1 < x2 <= w * 2:
                                cx = (x1 + x2) / 2.0
                                angle_offset = (cx / w - 0.5) * hfov_rad
                                robot_yaw = float(obj_rot[2])
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
                    logger.debug("bbox-to-bearing failed for '%s'", name, exc_info=True)

            meta: dict[str, Any] = {
                "observed_position": list(obj_pos),
                "observed_rotation": list(obj_rot),
            }
            if extra_metadata:
                meta.update(extra_metadata)
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
        """One multimodal VLM request containing all images. Raises on API failure."""
        from dimos.msgs.sensor_msgs.Image import Image as DimosImage

        if not images:
            logger.warning("[VLM] query_batch skipped: empty image list")
            return ""
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
            logger.exception(
                "[VLM] query_batch FAILED after %.1fs (model=%s)",
                time.time() - t0,
                model_cls,
            )
            raise
        elapsed = time.time() - t0
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

    def _detect_objects_panorama_batch_async(
        self,
        captures: list[tuple[Any, tuple[float, float, float], tuple[float, float, float]]],
        room_name: str,
    ) -> None:
        """Run one VLM call over all panoramic frames; store deduplicated objects."""
        n = len(captures)
        frames = [c[0] for c in captures]
        frame_poses = [(c[1], c[2]) for c in captures]
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
            prompt = _VLM_OBJECT_LIST_PROMPT + ' Include "image_indices": [0] for each object.'
        else:
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

    def _detect_objects_in_current_view(
        self, extra_metadata: dict[str, Any] | None = None
    ) -> tuple[int, list[str], str]:
        if self._latest_image is None:
            return 0, [], "No camera image available."
        if not hasattr(self._latest_image, "data"):
            return 0, [], "Camera image has no pixel data."

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
            return 0, [], f"VLM call failed: {exc}"

        objects = self._parse_vlm_object_list_response(response)
        if not objects:
            return 0, [], f"No objects parsed from VLM response: {(response or '')[:200]}"

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
            extra_metadata=extra_metadata,
        )
        names = [
            _normalize_vlm_object_name((o.get("name") or "").strip())
            for o in objects
            if (o.get("name") or "").strip()
        ]
        if stored:
            return stored, names, f"Detected {stored} object(s): {', '.join(names)}."
        return 0, names, "No nameable objects detected."

    @skill
    def verify_object_in_view(self, name: str) -> str:
        """Re-confirm a specific object is currently visible in the camera.

        Asks the VLM a single yes/no question about the current frame; if it
        misses, rotate in 6 x 60-degree steps with another yes/no after each
        step. The stored landmark description (``state``, e.g.
        ``"桌上的透明矿泉水瓶"``) — if any — is forwarded to the VLM as a
        disambiguation hint, which catches same-radical confusions like
        水瓶 ↔ 水桶 or 书桌 ↔ 会议桌 without an extra round-trip.

        Call this whenever you need to verify physical presence — DO NOT rely
        on dialogue memory. The physical world can change between requests
        (objects can be moved by humans). For example, if you JUST navigated
        to "展示板" but the user asks again to find "展示板", call this skill
        to confirm it is still there — never reply "we are already there"
        without verifying.

        This skill does NOT navigate. If verification fails, follow up with
        ``navigate_with_text`` to search elsewhere.

        Args:
            name: The object name (typically Chinese, e.g. "展示板") to look
                for in the current camera view.

        Returns:
            str: ``"YES: ..."`` if the object is visible (with rotation amount
            if a scan was needed), or ``"NO: ..."`` if not seen after the full
            sweep, including a hint to search elsewhere.
        """
        if not self._skill_started:
            raise ValueError(f"{self} has not been started.")
        if self._latest_image is None:
            return "NO: no camera image available."

        logger.info("[verify_object_in_view] START name=%r", name)
        description: str | None = None
        try:
            obj_rec = self._resolve_landmark_from_query(name)
        except Exception:
            obj_rec = None
        if obj_rec is not None and obj_rec.record_type == RecordType.LANDMARK:
            description = obj_rec.state or None
            if description:
                logger.info("[verify_object_in_view] using stored description=%r", description)

        result = self._visual_acquire_object(name, stored_yaw=None, description=description)
        if result is not None:
            return f"YES: {result}. The camera currently sees '{name}'."
        return (
            f"NO: '{name}' is NOT visible in the camera after a full 6x60deg scan. "
            "The object may have been moved or is no longer here. "
            f"Use `navigate_with_text({name!r})` to search other locations, or "
            "`detect_objects_in_view` to list what is actually here."
        )

    @skill
    def detect_objects_in_view(self) -> str:
        """Detect nameable objects in the current camera frame using VLM.

        Each detected object is stored in landmark memory with a **Chinese name**.
        Navigate with ``navigate_with_text("电脑")`` (preferred) or
        ``navigate_to_landmark("电脑")`` when the exact name is known.

        Returns:
            str: comma-separated list of detected object names, or a status message.
        """
        if not self._skill_started:
            raise ValueError(f"{self} has not been started.")
        stored, names, message = self._detect_objects_in_current_view()
        if stored:
            return f"Detected {stored} object(s): {', '.join(names)}. Stored in landmark memory."
        return message

    def _bbox_reasonable_for_tracking(self, bbox: BBox) -> bool:
        if self._latest_image is None or not hasattr(self._latest_image, "data"):
            return True
        h, w = self._latest_image.data.shape[:2]
        x1, y1, x2, y2 = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
        bw, bh = x2 - x1, y2 - y1
        if bw < 24 or bh < 24:
            return False
        if bw * bh > 0.55 * w * h:
            return False
        return True

    def _bbox_close_enough_for_arrival(self, bbox: BBox) -> bool:
        if self._latest_image is None or not hasattr(self._latest_image, "data"):
            return False
        h, w = self._latest_image.data.shape[:2]
        x1, y1, x2, y2 = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
        bw, bh = x2 - x1, y2 - y1
        if bw <= 0 or bh <= 0:
            return False
        bbox_area_ratio = (bw * bh) / float(w * h)
        bbox_height_ratio = bh / float(h)
        bbox_width_ratio = bw / float(w)
        center_x = (x1 + x2) / 2.0
        abs_center_error = abs((center_x - w / 2.0) / max(1.0, w / 2.0))
        large_enough = (
            bbox_area_ratio >= _VISUAL_SERVO_ARRIVAL_AREA_RATIO
            or bbox_height_ratio >= _VISUAL_SERVO_ARRIVAL_HEIGHT_RATIO
            or bbox_width_ratio >= _VISUAL_SERVO_ARRIVAL_WIDTH_RATIO
        )
        return large_enough and abs_center_error <= _VISUAL_SERVO_CLOSE_ENOUGH_CENTERED_ERR

    def _bbox_visual_progress_metrics(self, bbox: BBox) -> tuple[float, float, float] | None:
        if self._latest_image is None or not hasattr(self._latest_image, "data"):
            return None
        h, w = self._latest_image.data.shape[:2]
        x1, y1, x2, y2 = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
        bw, bh = x2 - x1, y2 - y1
        if bw <= 0 or bh <= 0:
            return None
        center_x = (x1 + x2) / 2.0
        abs_center_error = abs((center_x - w / 2.0) / max(1.0, w / 2.0))
        area_ratio = (bw * bh) / float(w * h)
        height_ratio = bh / float(h)
        return abs_center_error, area_ratio, height_ratio

    def _get_bbox_for_current_frame(self, query: str) -> BBox | None:
        if self._latest_image is None:
            return None

        return get_object_bbox_from_image(self._vl_model, self._latest_image, query)

    def _confirm_object_in_current_frame(self, query: str) -> bool:
        if self._latest_image is None:
            logger.warning("[L2]   Cannot confirm %r: no current camera image", query)
            return False

        detection = get_object_detection_from_image(self._vl_model, self._latest_image, query)
        if detection is None:
            logger.warning(
                "[L2]   Final VLM confirmation rejected %r: no matching detection", query
            )
            return False

        logger.info(
            "[L2]   Final VLM confirmation accepted %r: name=%r description=%r "
            "confidence=%s match=%s bbox=%s",
            query,
            detection.name,
            detection.description,
            detection.confidence,
            detection.match,
            detection.bbox,
        )
        return True

    def _query_memory_images_with_vlm(self, query: str) -> str | None:
        """Way 1: ask VLM to inspect stored memory images for the target object.

        Collects candidate images from spatial memory and room references,
        sends them all in ONE batch VLM call, then navigates to the first
        confirmed location.
        """
        import numpy as np

        candidates: list[
            tuple[np.ndarray, dict[str, Any], str]
        ] = []  # (image, metadata, source_tag)

        # Source A: spatial-memory frames matching the text query
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

        # Source B: room reference images (one per room for diversity)
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

        # Single batch VLM call: "which of these N images contains X?"
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

        # Navigate to the first confirmed match
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
            room_name = str(meta.get("room_name", ""))
            if room_name:
                room_rec = self._resolve_room_landmark(room_name)
                if room_rec is not None:
                    logger.info(
                        "[vlm_memory] '%s' in room '%s' → navigate to room then re-scan",
                        query,
                        room_name,
                    )
                    nav_msg = self._navigate_to_landmark(
                        room_rec,
                        arrival_action="stop",
                        arrival_distance=0.6,
                        run_arrival_action=False,
                        enable_visual_drift=False,
                    )
                    if "severe visual/odom drift" in nav_msg.lower():
                        logger.warning("[vlm_memory] drift at room '%s'", room_name)
                        return None
                    # Use stored bearing if object was previously tagged
                    obj_rec = self._resolve_landmark_from_query(query)
                    stored_yaw: float | None = None
                    description: str | None = None
                    if obj_rec is not None and obj_rec.record_type == RecordType.LANDMARK:
                        stored_yaw = (
                            obj_rec.rotation[2] if abs(obj_rec.rotation[2]) > 1e-6 else None
                        )
                        description = obj_rec.state or None
                    if stored_yaw is not None:
                        vis_msg = self._visual_acquire_object(
                            query, stored_yaw, description=description
                        )
                        if vis_msg:
                            return vis_msg
                    found = self._navigate_to_object(
                        query,
                        timeout=_OBJECT_VISUAL_NAV_TIMEOUT_S,
                        arrival_action="point",
                        run_arrival_action=True,
                    )
                    if found:
                        return found
                    return (
                        f"VLM found '{query}' in room '{room_name}' image; "
                        f"reached the room but could not track '{query}' in view. {nav_msg}"
                    )

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
        # Block until arrival (cap at 120s)
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
        # Try visual acquire if object has stored bearing
        obj_rec = self._resolve_landmark_from_query(query)
        if obj_rec is not None and obj_rec.record_type == RecordType.LANDMARK:
            stored_yaw = obj_rec.rotation[2] if abs(obj_rec.rotation[2]) > 1e-6 else None
            vis_msg = self._visual_acquire_object(
                query, stored_yaw, description=obj_rec.state or None, announce=False
            )
            self._announce_object_found(query)
            if vis_msg:
                return f"{prefix} ({vis_msg})"
        else:
            self._announce_object_found(query)
        return prefix

    def _navigate_using_semantic_map(self, query: str) -> str | None:
        results = self._spatial_memory.query_by_text(query)

        if not results:
            logger.info("[L6]   CLIP text search returned 0 results")
            return None

        best_match = results[0]
        dist = best_match.get("distance", 1.0)
        similarity = 1.0 - dist
        logger.info("[L6]   Top CLIP match: distance=%.4f similarity=%.4f", dist, similarity)

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
        # "找到了"指的是在语义地图中找到了匹配, 导航结果由 _navigate_to 异步完成
        self._announce_object_found(query)
        return self._navigate_to(goal_pose, message)

    @staticmethod
    def _remember_memory_blindspot_goal(
        recent_goals: list[tuple[float, float]], pose: PoseStamped
    ) -> None:
        recent_goals.append((float(pose.position.x), float(pose.position.y)))
        if len(recent_goals) > 20:
            recent_goals.pop(0)

    def _wait_for_memory_blindspot_goal(
        self,
        timeout_sec: float,
        stuck_timeout_sec: float,
        progress_epsilon_m: float,
    ) -> str:
        deadline = time.time() + max(0.0, timeout_sec)
        last_progress_time = time.time()
        last_progress_position = (
            (
                float(self._latest_odom.position.x),
                float(self._latest_odom.position.y),
                float(self._latest_odom.position.z),
            )
            if self._latest_odom is not None
            else None
        )
        while time.time() < deadline:
            if self._memory_blindspot_patrol_stop:
                self._navigation.cancel_goal()
                return "stopped"
            if self._navigation.is_goal_reached():
                return "reached"
            if self._latest_odom is not None and last_progress_position is not None:
                current_position = (
                    float(self._latest_odom.position.x),
                    float(self._latest_odom.position.y),
                    float(self._latest_odom.position.z),
                )
                moved = math.dist(current_position, last_progress_position)
                if moved >= progress_epsilon_m:
                    last_progress_time = time.time()
                    last_progress_position = current_position
                elif time.time() - last_progress_time >= stuck_timeout_sec:
                    self._navigation.cancel_goal()
                    return "stuck"
            time.sleep(0.2)
        self._navigation.cancel_goal()
        return "timeout"

    @skill
    def explore_memory_blindspot(
        self,
        search_radius_m: float = 5.0,
        coverage_radius_m: float = 1.0,
        stale_after_sec: float = 600.0,
    ) -> str:
        """Find a nearby reachable area with missing or stale spatial memory and navigate there.

        Args:
            search_radius_m: Radius around the robot to search in meters.
            coverage_radius_m: Memory coverage radius in meters.
            stale_after_sec: Treat memory older than this as stale. Use 0 to disable staleness.
        """
        if not self._skill_started:
            raise ValueError(f"{self} has not been started.")
        if self._latest_odom is None:
            return "No odometry data received yet, cannot explore memory blind spots."
        if self._latest_global_costmap is None:
            return "No global costmap received yet, cannot explore memory blind spots."

        target = self._find_nearest_memory_blindspot(
            search_radius_m=search_radius_m,
            coverage_radius_m=coverage_radius_m,
            stale_after_sec=stale_after_sec,
        )
        if target is None:
            return (
                f"No reachable memory blind spot found within {search_radius_m:.1f}m. "
                "Nearby spatial memory coverage looks healthy."
            )

        pose = cast("PoseStamped", target["pose"])
        ok = self._navigation.set_goal(pose)
        if not ok:
            return "Found a memory blind spot, but navigation rejected the goal."

        return (
            f"Found a {target['target_type']} ({target['reason']}) "
            f"{float(target['distance_m']):.1f}m away at "
            f"({pose.position.x:.2f}, {pose.position.y:.2f}). "
            "Started navigating there to collect spatial memory."
        )

    @skill
    def patrol_memory_blindspots(
        self,
        search_radius_m: float = 8.0,
        coverage_radius_m: float = 1.0,
        stale_after_sec: float = 600.0,
        max_goals: int = 0,
        max_duration_sec: float = 120.0,
        goal_timeout_sec: float = 90.0,
        stuck_timeout_sec: float = 15.0,
        progress_epsilon_m: float = 0.25,
        cooldown_sec: float = 2.0,
        recognize_on_arrival: bool = True,
        include_object_summary: bool = True,
    ) -> str:
        """Explore areas that are not yet covered by spatial memory.

        Use this when the user asks the robot to explore unexplored areas,
        inspect unvisited places, fill spatial-memory gaps, refresh memory coverage,
        or build spatial memory over time. The robot navigates to safe observation
        points near spatial-memory-uncovered areas, records new spatial memory, and
        can recognize objects on arrival.

        Args:
            search_radius_m: Radius around the robot to search in meters.
            coverage_radius_m: Memory coverage radius in meters.
            stale_after_sec: Treat memory older than this as stale. Use 0 to disable staleness.
            max_goals: Optional target count limit. 0 means only use max_duration_sec.
            max_duration_sec: Maximum patrol duration.
            goal_timeout_sec: Per-goal timeout.
            stuck_timeout_sec: Cancel and try another target if odometry makes no progress this long.
            progress_epsilon_m: Minimum movement counted as progress toward the current target.
            cooldown_sec: Delay between goals.
            recognize_on_arrival: Run VLM object recognition after reaching a target.
            include_object_summary: Include objects found during this patrol in the return text.
        """
        if not self._skill_started:
            raise ValueError(f"{self} has not been started.")
        if self._latest_odom is None:
            return "No odometry data received yet, cannot patrol memory blind spots."
        if self._latest_global_costmap is None:
            return "No global costmap received yet, cannot patrol memory blind spots."

        started_at = time.time()
        deadline = started_at + max(0.0, max_duration_sec)
        visited = 0
        timed_out = 0
        stuck = 0
        failed = 0
        max_failures = 3
        stop_reason = "max_duration_sec reached"
        recent_goals: list[tuple[float, float]] = []
        run_id = f"memory_blindspot_{int(started_at)}"
        objects_by_target: list[tuple[str, list[str]]] = []
        self._memory_blindspot_patrol_stop = False

        while time.time() < deadline:
            if self._memory_blindspot_patrol_stop:
                stop_reason = "stop command received"
                break
            if max_goals > 0 and visited >= max_goals:
                stop_reason = f"max_goals={max_goals} reached"
                break

            target = self._find_nearest_memory_blindspot(
                search_radius_m=search_radius_m,
                coverage_radius_m=coverage_radius_m,
                stale_after_sec=stale_after_sec,
                exclude_recent_goals=recent_goals,
            )
            if target is None:
                stop_reason = (
                    f"no reachable memory-uncovered target remained within {search_radius_m:.1f}m"
                )
                break

            pose = cast("PoseStamped", target["pose"])
            target_label = f"{target['target_type']}@({pose.position.x:.2f},{pose.position.y:.2f})"
            if not self._navigation.set_goal(pose):
                self._remember_memory_blindspot_goal(recent_goals, pose)
                failed += 1
                if failed >= max_failures:
                    stop_reason = "navigation rejected too many memory blindspot goals"
                    break
                continue

            remaining_sec = max(0.0, deadline - time.time())
            if remaining_sec <= 0:
                stop_reason = "max_duration_sec reached"
                break

            status = self._wait_for_memory_blindspot_goal(
                min(goal_timeout_sec, remaining_sec),
                stuck_timeout_sec,
                progress_epsilon_m,
            )
            if status == "stopped":
                stop_reason = "stop command received"
                break
            if status in {"timeout", "stuck"}:
                self._remember_memory_blindspot_goal(recent_goals, pose)
                failed += 1
                if status == "stuck":
                    stuck += 1
                else:
                    timed_out += 1
            else:
                visited += 1
                failed = 0
                self._remember_memory_blindspot_goal(recent_goals, pose)
                if recognize_on_arrival:
                    stored, names, _ = self._detect_objects_in_current_view(
                        {
                            "source": "memory_blindspot_explorer",
                            "exploration_run_id": run_id,
                            "target_type": str(target["target_type"]),
                            "target_reason": str(target["reason"]),
                            "target_pose": [
                                float(pose.position.x),
                                float(pose.position.y),
                                float(pose.position.z),
                            ],
                        }
                    )
                    if stored and names:
                        objects_by_target.append((target_label, names))

            if failed >= max_failures:
                stop_reason = "too many consecutive navigation failures"
                break
            time.sleep(min(max(0.0, cooldown_sec), max(0.0, deadline - time.time())))

        elapsed = time.time() - started_at
        lines = [
            "Memory-driven exploration finished: "
            f"visited {visited} goal(s), timed out {timed_out}, "
            f"stuck {stuck}, elapsed {elapsed:.0f}s.",
            f"Stopped because {stop_reason}.",
        ]
        if include_object_summary:
            if objects_by_target:
                lines.append("New objects found in explored areas:")
                for target_label, names in objects_by_target:
                    lines.append(f"- {target_label}: {', '.join(names)}")
            else:
                lines.append("No new objects were recognized in explored areas.")
        return "\n".join(lines)

    @skill
    def stop_navigation(self) -> str:
        """Immediately stop moving."""

        if not self._skill_started:
            raise ValueError(f"{self} has not been started.")

        self._memory_blindspot_patrol_stop = True
        self._cancel_goal_and_stop()

        return "Stopped"

    @skill
    def stop_all_motion(self) -> str:
        """Cancel navigation/tracking and recover the Unitree to a stable standing state."""

        if not self._skill_started:
            raise ValueError(f"{self} has not been started.")

        self._memory_blindspot_patrol_stop = True
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
        """Emergency stop alias for stop_all_motion."""
        return self.stop_all_motion()

    @skill
    def stop_movement(self) -> str:
        """Stop movement alias for stop_all_motion."""
        return self.stop_all_motion()

    @skill
    def find_room_visually(self) -> str:
        """Identify which room the robot is currently in by comparing the camera
        view against previously stored room reference images.

        This works purely through visual similarity (CLIP embeddings) — no
        coordinates are used, so it survives robot restarts and SLAM resets.

        Call this when the human asks "where am I?", "which room is this?",
        or when you need to re-localize after a restart.

        Returns:
            str: Room name with confidence, or a message if no match found.
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
        """Navigate to a previously recorded landmark by name.

        Uses the landmark memory to find the recorded position and plans
        the best route through known waypoints.

        Args:
            name: The name of the landmark to navigate to.
            arrival_action: ``stop`` | ``sit`` | ``wave`` — optional Unitree sport hook at goal.
            arrival_distance: Planar distance (m) to standoff before triggering arrival_action.
        """
        if not self._skill_started:
            raise ValueError(f"{self} has not been started.")

        target = self._landmark_memory.resolve_by_query(name)
        if target is None:
            return (
                f"No landmark found matching '{name}'. "
                f"Use query_landmarks to see available landmarks (names are in Chinese)."
            )

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
        return _strip_terminal_navigation_failure(result)

    def _navigate_to_landmark(
        self,
        target: SpatialRecord,
        *,
        arrival_action: str = "stop",
        arrival_distance: float = 0.5,
        run_arrival_action: bool = True,
        relocalize_interval: float | None = None,
        enable_visual_drift: bool = False,
    ) -> str:
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
            # Object coords come from VLM capture pose — do not relocalize via room CLIP.
            enable_visual_drift = False
            expected_room = None
        elif target.record_type == RecordType.ROOM:
            expected_room = target.name
        else:
            expected_room = self._room_name_at_position(target.position[0], target.position[1])

        def _try_navigate() -> str | None:
            """Single attempt at waypoint traversal + approach. Returns None on success,
            or an error string on severe drift."""
            nonlocal interval
            all_records = self._landmark_memory.get_all()
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
                active = segment_goal
                while time.time() < deadline:
                    active, severe = self._periodic_visual_drift_correction(
                        active,
                        last_corr,
                        interval,
                        destination_pose=final_dest,
                        expected_room_name=expected_room,
                        enable_visual_drift=enable_visual_drift,
                    )
                    if severe:
                        return (
                            "Navigation aborted: severe visual/odom drift detected "
                            "(>1m vs room reference). Retry navigate_with_text or re-tag rooms."
                        )
                    if self._navigation.is_goal_reached():
                        break
                    time.sleep(0.25)

            if is_object_landmark:
                # Prefer stored object yaw; fall back to approach bearing
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
            effective_arrival_distance = arrival_distance
            accepted_safe_arrival = False
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
                    active_goal = standoff_pose
                else:
                    inch = self._inch_goal_toward(standoff_pose, effective_arrival_distance)
                    active_goal = inch if inch is not None else standoff_pose

                self._navigation.set_goal(active_goal)
                _, severe = self._wait_goal_with_relocalize(
                    active_goal,
                    last_corr,
                    time.time() + 15.0,
                    interval,
                    destination_pose=standoff_pose,
                    expected_room_name=expected_room,
                    enable_visual_drift=enable_visual_drift,
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
                    accepted_safe_arrival = True
                    break

            self._navigation.cancel_goal()
            if dist > effective_arrival_distance and not accepted_safe_arrival:
                stale_reason = self._coordinate_frame_stale_reason(target)
                if stale_reason:
                    return f"Navigation timed out for '{target.name}': {stale_reason}"
                return (
                    f"Navigation timed out before reaching '{target.name}' "
                    f"(remaining distance {dist:.2f}m)."
                )
            return None  # Success

        def _post_arrival_message(*, retry: bool) -> str:
            """Common arrival logic: visual acquire → conditionally run arrival_action."""
            tname = target.name or target.record_id
            vis_msg = ""
            visual_nav_failed = False
            if is_object_landmark:
                stored_yaw = target.rotation[2] if abs(target.rotation[2]) > 1e-6 else None
                tag = "Visual acquire (retry)" if retry else "Visual acquire"
                vlm_query = self._vlm_query_for_object_record(tname, target)
                visual_result = self._try_acquire_object_in_view_result(
                    tname,
                    stored_yaw,
                    vlm_query=vlm_query,
                )
                if visual_result is not None:
                    vis_msg = visual_result.message
                    visual_nav_failed = visual_result.navigation_failed
                if vis_msg:
                    logger.info("[L3] %s: %s", tag, vis_msg)
                else:
                    logger.warning("[L3] %s: '%s' not found in view", tag, tname)
                    local_result = self._local_search_for_object_near_landmark_result(
                        target,
                        tname,
                    )
                    if local_result is not None:
                        vis_msg = local_result.message
                        visual_nav_failed = local_result.navigation_failed
                    if vis_msg:
                        logger.info("[L3] Local search%s: %s", " (retry)" if retry else "", vis_msg)
                    else:
                        logger.warning(
                            "[L3] Local search%s: '%s' not found nearby",
                            " (retry)" if retry else "",
                            tname,
                        )
            if run_arrival_action:
                if is_object_landmark and (not vis_msg or visual_nav_failed):
                    if vis_msg:
                        return _terminal_navigation_failure(vis_msg)
                    suffix = " after drift recovery" if retry else ""
                    return (
                        f"Arrived at stored '{tname}' coordinate{suffix} but the camera "
                        f"could not visually acquire '{tname}'. The original landmark may "
                        "be a VLM hallucination, the object may have moved, or the stored "
                        "bearing may be inaccurate. Local search failed; arrival_action skipped."
                    )
                action_msg = self._run_arrival_action(arrival_action, tname)
                if is_object_landmark:
                    self._announce_object_found(tname)
                if vis_msg:
                    return f"{action_msg} ({vis_msg})"
                return action_msg

            base = (
                f"Arrived near '{tname}' standoff after drift recovery"
                if retry
                else f"Arrived near '{tname}' standoff"
            )
            if is_object_landmark:
                self._announce_object_found(tname)
            if vis_msg:
                return f"{base} ({vis_msg})"
            if is_object_landmark:
                return (
                    f"{base} (could not visually acquire '{tname}'; "
                    "local search failed; arrival_action not run)."
                )
            return f"{base} (arrival_action not run)."

        # First attempt
        err = _try_navigate()
        if err is None:
            return _post_arrival_message(retry=False)

        # Severe drift recovery: re-plan topology from current odom position
        logger.warning(
            "Severe drift on first attempt; re-planning topology from current odom for retry"
        )
        self._navigation.cancel_goal()
        time.sleep(0.5)

        err2 = _try_navigate()
        if err2 is None:
            return _post_arrival_message(retry=True)

        return (
            "Navigation aborted: severe visual/odom drift persisted after re-plan. "
            "Retry navigate_with_text or re-tag rooms."
        )

    @skill
    def clear_all_memory(self) -> str:
        """Clear all landmark and spatial memory (rooms, objects, CLIP images).

        Use before a fresh mapping session. Does not require restart.
        """
        if not self._skill_started:
            raise ValueError(f"{self} has not been started.")

        lm_n = self._landmark_memory.clear_all()
        spatial_stats: dict[str, int] = {}
        if hasattr(self._spatial_memory, "clear_all"):
            spatial_stats = self._spatial_memory.clear_all()
        logger.info(
            "[clear_all_memory] landmarks=%d spatial=%s",
            lm_n,
            spatial_stats,
        )
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
        """
        if not self._skill_started:
            raise ValueError(f"{self} has not been started.")

        from dimos.types.spatial_record import RecordType

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

        if not records:
            return (
                f"No landmarks found matching '{query}'. "
                f"Try query_landmarks objects or use navigate_with_text."
            )

        # Group by type for readability
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
        self._cancel_visual_servo_motion()
        self._navigation.cancel_goal()

    def _get_goal_pose_from_result(self, result: dict[str, Any]) -> PoseStamped | None:
        similarity = 1.0 - (result.get("distance") or 1)
        if similarity < self._similarity_threshold:
            logger.warning(
                f"Match found but similarity score ({similarity:.4f}) is below threshold ({self._similarity_threshold})"
            )
            return None

        metadata = result.get("metadata")
        if not metadata:
            return None
        first = metadata[0]
        pos_x = first.get("pos_x", 0)
        pos_y = first.get("pos_y", 0)
        theta = first.get("rot_z", 0)

        return PoseStamped(
            position=make_vector3(pos_x, pos_y, 0),
            orientation=Quaternion.from_euler(make_vector3(0, 0, theta)),
            frame_id="map",
        )
