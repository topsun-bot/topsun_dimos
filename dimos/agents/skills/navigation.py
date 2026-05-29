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
import os
import threading
import time
from typing import Any, cast

from reactivex.disposable import Disposable

from dimos.agents.annotation import skill
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
from dimos.navigation.visual.query import (
    get_object_bbox_from_image,
    parse_simple_bbox_line,
    yaw_offset_from_bbox,
)
from dimos.perception.object_tracking_spec import ObjectTrackingSpec
from dimos.perception.spatial_memory_spec import SpatialMemorySpec
from dimos.robot.unitree.unitree_skill_container import UnitreeSkillContainer
from dimos.spec.mapping import MetricRelocalizationSpec
from dimos.types.door_memory_spec import SpatialLandmarkMemorySpec
from dimos.types.robot_location import RobotLocation
from dimos.types.spatial_record import RecordType, SpatialRecord
from dimos.utils.generic import extract_json_from_llm_response
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


@dataclass(frozen=True)
class PlanarFrameTransform:
    """SE2 transform that maps persisted-map coordinates into the current odom frame.

    ``(x, y, yaw)`` is the pose of the current odom origin expressed in the
    persisted-map frame.
    """

    x: float
    y: float
    yaw: float

    def to_current(self, x: float, y: float) -> tuple[float, float]:
        dx = x - self.x
        dy = y - self.y
        c = math.cos(-self.yaw)
        s = math.sin(-self.yaw)
        return (c * dx - s * dy, s * dx + c * dy)

    def yaw_to_current(self, yaw: float) -> float:
        return yaw - self.yaw

# navigate_with_text fallback step order (env: DIMOS_NAV_FALLBACK=object_room|semantic|room_first)
_NAV_FALLBACK_OBJECT_ROOM = (
    "landmark",  # object landmark → navigate + 360° scan; fall through if not found
    "room_sweep",  # visit every ROOM + 360° scan
)
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
    "object_room": _NAV_FALLBACK_OBJECT_ROOM,
    "semantic": _NAV_FALLBACK_SEMANTIC,
    "room_first": _NAV_FALLBACK_ROOM_FIRST,
}

# Shared rotation step for tag_room panorama and in-room 360° scan.
# Real Go2 hardware is imprecise on small angles — 100° steps are more reliable
# than 60°/90°. Override via DIMOS_ROTATION_STEP_DEG.
def _rotation_step_deg() -> float:
    import os

    return float(os.getenv("DIMOS_ROTATION_STEP_DEG", "90"))


def _panorama_rotations() -> int:
    """Number of in-place rotations after the initial heading (90 deg x 3 ~= full coverage)."""
    import os

    override = os.getenv("DIMOS_ROOM_SCAN_ROTATIONS")
    if override:
        return max(1, int(override))
    return 3

_VLM_OBJECT_LIST_PROMPT = (
    "列出图中所有可单独指认的物体（家具、电器、设备、装饰、人等）。\n"
    "【硬性要求】JSON 里每个 name 必须是 1–4 个汉字的中文名词。"
    "禁止英文：不要写 computer/desk/chair/monitor/table。\n"
    "正确示例：电脑、书桌、办公椅、灭火器、电视。\n"
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
    _metric_relocalization: MetricRelocalizationSpec | None = None
    _object_tracking: ObjectTrackingSpec | None = None
    _unitree_skill_container: UnitreeSkillContainer | None = None
    _frontier_explorer: WavefrontFrontierExplorer | None = None
    _memory_session_id: str = ""
    _persisted_to_current: PlanarFrameTransform | None = None

    color_image: In[Image]
    odom: In[PoseStamped]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._skill_started = False
        self._memory_session_id = f"session_{int(time.time())}"
        self._sweep_skip_rooms: set[str] = set()
        self._persisted_to_current = None

        self._vl_model = _create_vl_model()
        _log_vlm_runtime_config(self._vl_model)

    @rpc
    def start(self) -> None:
        super().start()
        self.register_disposable(Disposable(self.color_image.subscribe(self._on_color_image)))
        self.register_disposable(Disposable(self.odom.subscribe(self._on_odom)))
        self._skill_started = True
        threading.Thread(
            target=self._auto_metric_relocalize_once,
            daemon=True,
            name="metric-relocalize-startup",
        ).start()

    @rpc
    def stop(self) -> None:
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

    @rpc
    def set_persisted_to_current_transform(self, x: float, y: float, yaw: float) -> bool:
        """Set the transform produced by metric-map relocalization.

        The transform maps current odom coordinates into the persisted map frame.
        Persisted records are inverted through this transform before navigation.
        """
        self._persisted_to_current = PlanarFrameTransform(float(x), float(y), float(yaw))
        logger.info(
            "Set map_from_current transform.",
            x=round(float(x), 3),
            y=round(float(y), 3),
            yaw=round(float(yaw), 3),
        )
        return True

    @rpc
    def clear_persisted_to_current_transform(self) -> bool:
        self._persisted_to_current = None
        logger.info("Cleared map_from_current transform.")
        return True

    def _auto_metric_relocalize_once(self) -> None:
        if self._metric_relocalization is None:
            return

        # Wait for the persistent map and first lidar frames to arrive.
        time.sleep(float(os.getenv("DIMOS_METRIC_RELOCALIZE_STARTUP_DELAY_S", "8.0")))

        try:
            # 360° rotation first → builds a much larger local costmap with
            # distinctive features in all directions.
            result = self.relocalize_with_metric_map(scan=True, publish_on_success=True)
            logger.info("Startup metric relocalization result: %s", result)
        except Exception:
            logger.warning("Startup metric relocalization failed", exc_info=True)

    @skill
    def relocalize_with_metric_map(
        self,
        scan: bool = False,
        publish_on_success: bool = False,
        search_radius_m: float = 2.0,
        min_confidence: float = 0.55,
    ) -> str:
        """Match the current live costmap against the persisted map and set navigation transform."""
        if self._metric_relocalization is None:
            return "Metric relocalization is not available: CostMapper is not wired."

        if scan:
            self._rotate_scan_in_place()
            time.sleep(1.0)

        result = self._metric_relocalization.relocalize_current_map(
            search_radius_m=search_radius_m,
            min_confidence=min_confidence,
        )
        success = bool(result.get("success", False))
        confidence = float(result.get("confidence", 0.0))
        if not success:
            return f"Metric relocalization failed (confidence={confidence:.2f})."

        tx = float(result["map_from_current_x"])
        ty = float(result["map_from_current_y"])
        yaw = float(result["map_from_current_yaw"])
        self.set_persisted_to_current_transform(tx, ty, yaw)

        if publish_on_success:
            if abs(math.sin(yaw)) < 0.2:
                # Publish persistent map to A* with origin shifted to the current
                # odometry frame.  Safe when yaw ≈ 0 or π (within ~12°).
                self._metric_relocalization.publish_persistent_map(
                    shift_x=-tx, shift_y=-ty,
                )
            else:
                logger.warning(
                    "Relocalization yaw=%.1f° too far from 0/π — skipping "
                    "persistent-map publish (navigation may be limited to "
                    "live-costmap area)",
                    math.degrees(yaw),
                )

        return (
            "Metric relocalization succeeded: "
            f"map_from_current=({tx:.2f}, {ty:.2f}, yaw={yaw:.2f}), "
            f"confidence={confidence:.2f}."
        )

    def _should_transform_persisted_record(self, record: SpatialRecord) -> bool:
        if self._persisted_to_current is None:
            return False
        return not bool(record.session_id and record.session_id == self._memory_session_id)

    def _record_pose_in_navigation_frame(self, record: SpatialRecord) -> PoseStamped:
        x, y, z = record.position
        yaw = record.rotation[2]
        if self._should_transform_persisted_record(record):
            assert self._persisted_to_current is not None
            x, y = self._persisted_to_current.to_current(float(x), float(y))
            yaw = self._persisted_to_current.yaw_to_current(float(yaw))

        return PoseStamped(
            position=make_vector3(float(x), float(y), float(z)),
            orientation=Quaternion.from_euler(Vector3(0.0, 0.0, float(yaw))),
            frame_id="map",
        )

    def _pose_in_navigation_frame(self, pose: PoseStamped) -> PoseStamped:
        if self._persisted_to_current is None:
            return pose

        x, y = self._persisted_to_current.to_current(
            float(pose.position.x), float(pose.position.y)
        )
        yaw = self._persisted_to_current.yaw_to_current(float(pose.orientation.to_euler().z))
        return PoseStamped(
            position=make_vector3(x, y, float(pose.position.z)),
            orientation=Quaternion.from_euler(Vector3(0.0, 0.0, yaw)),
            frame_id=pose.frame_id,
        )

    def _set_navigation_goal(self, pose: PoseStamped) -> bool:
        return self._navigation.set_goal(pose)

    @skill
    def tag_location(self, location_name: str, num_photos: int = -1) -> str:
        """Tag this location with a name and store reference images.

        Default: panoramic capture — the robot rotates in place taking photos
        at each step (90° per step, enough steps to cover ~360°). Each frame
        is also scanned with VLM to detect objects.

        Pass ``num_photos=0`` for a single shot at the current heading.
        Pass ``num_photos≥2`` to override the number of panoramic steps.

        Each photo is stored as a room reference image for visual relocalization.

        Args:
            location_name (str): the name for the location (e.g. "office", "kitchen").
            num_photos (int): panoramic photos (-1=auto, 0=single, 2~12=manual).

        Returns:
            str: the outcome
        """

        if not self._skill_started:
            raise ValueError(f"{self} has not been started.")

        if not self._latest_odom:
            return "No odometry data received yet, cannot tag location."

        auto_panorama = num_photos < 0
        if not auto_panorama:
            num_photos = max(0, min(num_photos, 12))
        # Each entry: (image ndarray, position, rotation) at capture time.
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
            """Take one photo; queue frame for CLIP and spawn per-frame VLM thread."""
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
                img_copy = self._latest_image.data.copy()
                pos_tuple = (float(pos.x), float(pos.y), float(pos.z))
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

                # Per-frame async VLM: fire immediately rather than waiting for all frames.
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

        if auto_panorama or num_photos >= 2:
            angle_step = _rotation_step_deg()
            n_rotations = _panorama_rotations() if auto_panorama else (num_photos - 1)
            done = 1
            for _i in range(n_rotations):
                ok = self._rotate_in_place_degrees(angle_step)
                if not ok:
                    break
                time.sleep(0.3)  # let camera settle
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
            logger.warning(
                "[tag_location] VLM SKIPPED for %r: no camera frames captured "
                "(check color_image stream / simulation camera)",
                location_name,
            )

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
        """Rotate the robot in place by the given angle. Returns True on success."""
        us = self._unitree_skill_container
        if us is None:
            return False

        # Open-loop timed rotation — only when DIMOS_ROTATE_TIMED_FACTOR is set.
        if os.getenv("DIMOS_ROTATE_TIMED_FACTOR") and hasattr(us, "rotate_in_place_timed"):
            try:
                result = us.rotate_in_place_timed(degrees)
                if isinstance(result, bool):
                    return result
                return bool(result)
            except Exception:
                logger.exception("rotate_in_place_timed failed, falling back to TF-based")

        # TF-based closed-loop spin (improved: no more max_delta clamp).
        if hasattr(us, "rotate_in_place_degrees"):
            try:
                result = us.rotate_in_place_degrees(degrees)
                if isinstance(result, bool):
                    return result
                return bool(result)
            except Exception:
                logger.exception("rotate_in_place_degrees failed")
                return False

        # GO2 fallback: path-planned relative_move
        if hasattr(us, "relative_move"):
            try:
                msg = us.relative_move(forward=0.0, left=0.0, degrees=degrees)
                if isinstance(msg, str):
                    return "goal reached" in msg.lower()
                return bool(msg)
            except Exception:
                logger.exception("relative_move rotation failed")
                return False
        # G1 fallback: timed velocity move (yaw arg is rad/s, not degrees)
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
        """Tag the current room with 360° panoramic capture.

        Default: rotate 100° per step (``DIMOS_ROTATION_STEP_DEG``), enough steps
        to cover ~360°, with per-frame VLM object detection after each shot.

        Convenience wrapper that calls ``tag_location(name, num_photos=N)``.
        Pass ``num_photos=0`` (default) for auto; pass ``num_photos≥2`` to override
        the number of photos (still uses fixed step angle, not 360/N).
        """
        if num_photos <= 0:
            return self.tag_location(name, num_photos=-1)
        return self.tag_location(name, num_photos=max(2, min(num_photos, 12)))

    def _nav_fallback_strategy(self) -> str:
        import os

        name = os.getenv("DIMOS_NAV_FALLBACK", "object_room").lower().strip()
        if name not in _NAV_FALLBACK_STRATEGIES:
            logger.warning("Unknown DIMOS_NAV_FALLBACK=%r, using 'object_room'", name)
            return "object_room"
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
        msg = self._navigate_to_object(query, timeout=timeout)
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
        nav_lower = nav_landmark_msg.lower()
        if (
            "severe visual/odom drift" in nav_lower
            or "aborted" in nav_lower
            or "navigation skipped" in nav_lower
            or "timed out" in nav_lower
            or "could not visually acquire" in nav_lower
        ):
            logger.warning("[landmark] ⚠ Navigation failed or object not found, fall through")
            if is_object_landmark:
                meta = landmark_target.metadata or {}
                room = meta.get("room_name")
                if not room:
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

    def _nav_fallback_step_room_sweep(self, query: str) -> str | None:
        logger.info("[room_sweep] Sweeping room anchors for %r ...", query)
        msg = self._room_anchor_sweep_for_object(query)
        if msg:
            logger.info("[room_sweep] ✓ HIT: %s", msg)
            return f"{self._run_arrival_action('point', query)} ({msg})"
        else:
            logger.info("[room_sweep] ✗ MISS")
            return None

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

        Default (``DIMOS_NAV_FALLBACK=object_room``): object landmark → room sweep.
        All rooms are visited with a 360° scan; ends if the object is not found.

        Other strategies: ``semantic`` (full 6-layer chain) or ``room_first``.

        CALL THIS SKILL FOR ONE SUBJECT AT A TIME.
        Args:
            query: Text query (object name, room name, or description).
        """

        if not self._skill_started:
            raise ValueError(f"{self} has not been started.")

        logger.info("=" * 50)
        logger.info("NAVIGATE_WITH_TEXT START  query=%r", query)
        logger.info("=" * 50)

        self._sweep_skip_rooms = set()
        success_msg = self._run_navigate_fallback_chain(query)
        if success_msg:
            logger.info("=" * 50)
            logger.info("NAVIGATE_WITH_TEXT END  query=%r  result=HIT", query)
            logger.info("=" * 50)
            return success_msg

        logger.info("=" * 50)
        logger.info("NAVIGATE_WITH_TEXT END  query=%r  result=ALL_MISS", query)
        logger.info("=" * 50)
        strategy = self._nav_fallback_strategy()
        if strategy == "object_room":
            return (
                f"Could not find '{query}' (checked object landmark and swept all rooms)."
            )
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

    def _navigate_to_object(self, query: str, *, timeout: float = 30.0) -> str | None:
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
            # Check if navigator finished
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

            # Fast fallback: if tracking is consecutively lost for >5s, bail early
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

            if self._object_tracking.is_tracking():
                goal_set = True

            time.sleep(0.25)

        logger.warning("[L2]   ✗ Navigation to '%s' timed out after %.0fs", query, timeout)
        self._object_tracking.stop_track()
        return None

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

        obj_rec = self._resolve_landmark_from_query(query)
        obj_room: str | None = None
        if obj_rec is not None and obj_rec.record_type == RecordType.LANDMARK:
            meta = obj_rec.metadata or {}
            raw_room = meta.get("room_name")
            if raw_room:
                obj_room = str(raw_room)

        skip = self._sweep_skip_rooms
        logger.info(
            "[L4]   Sweeping %d room(s)%s ...",
            len(rooms),
            f" (skip already searched: {sorted(skip)})" if skip else "",
        )
        for ri, room in enumerate(rooms):
            rname = room.name or room.record_id
            if rname in skip:
                logger.info("[L4]   Room %d/%d: skip %r (already searched)", ri + 1, len(rooms), rname)
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
            )
            if "severe visual/odom drift" in nav_msg.lower():
                logger.warning("Room sweep: drift abort at %r; trying next room", rname)
                continue
            if "timed out" in nav_msg.lower() or "aborted" in nav_msg.lower():
                logger.warning("Room sweep: nav failed at %r; trying next room", rname)
                continue

            # stored_yaw only applies in the object's recorded room — using it
            # elsewhere makes the robot face the wrong direction.
            stored_yaw: float | None = None
            if obj_rec is not None and obj_rec.record_type == RecordType.LANDMARK and obj_room == rname:
                stored_yaw = obj_rec.rotation[2] if abs(obj_rec.rotation[2]) > 1e-6 else None

            result = self._scan_room_for_object(query, stored_yaw=stored_yaw)
            if result:
                return result
        return None

    def _rotate_scan_in_place(self) -> None:
        us = self._unitree_skill_container
        if us is None:
            logger.warning("360 scan: no UnitreeSkillContainer wired; pausing briefly instead")
            time.sleep(1.0)
            return
        try:
            if hasattr(us, "rotate_in_place_degrees"):
                us.rotate_in_place_degrees(360.0)
            elif hasattr(us, "relative_move"):
                us.relative_move(forward=0.0, left=0.0, degrees=360.0)
            elif hasattr(us, "move"):
                us.move(x=0.0, y=0.0, yaw=math.radians(90.0), duration=4.0)
        except Exception:
            logger.exception("360 scan rotation failed")

    def _planar_distance_to_pose(self, pose: PoseStamped) -> float:
        if self._latest_odom is None:
            return float("inf")
        dx = float(self._latest_odom.position.x) - float(pose.position.x)
        dy = float(self._latest_odom.position.y) - float(pose.position.y)
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
        if self._should_transform_persisted_record(target):
            return None

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

    def _servo_to_bbox(self, bbox: BBox) -> bool:
        """Rotate in place to centre the object horizontally in the camera view.

        Converts pixel bbox to 0-1000 coordinate space and computes the yaw
        offset via ``yaw_offset_from_bbox``, then rotates in place to face it.

        Returns True if the rotation succeeded (or was unnecessary).
        """
        if self._latest_image is None:
            return False
        h, w = self._latest_image.data.shape[:2]
        x1_px, y1_px, x2_px, y2_px = bbox
        # Scale pixel coords → 0-1000 (inverse of _scale_bbox_to_image Qwen branch)
        x1 = x1_px / w * 1000.0
        y1 = y1_px / h * 1000.0
        x2 = x2_px / w * 1000.0
        y2 = y2_px / h * 1000.0

        offset_rad = yaw_offset_from_bbox(x1, y1, x2, y2, self._camera_hfov_deg)
        offset_deg = math.degrees(offset_rad)

        if abs(offset_deg) < 3.0:
            logger.info("[servo] object already centred (offset=%.1f°)", offset_deg)
            return True

        # yaw_offset positive = object right of centre → turn clockwise (negative)
        turn_deg = -offset_deg
        logger.info(
            "[servo] turning %.1f° to face object (bbox offset=%.1f°)",
            turn_deg,
            offset_deg,
        )
        return self._rotate_in_place_degrees(turn_deg)

    def _detect_and_servo(self, target_name: str) -> str | None:
        """Detect *target_name* in the current frame and servo to face it.

        Returns a success message string, or None if the object wasn't found or
        the bbox was unreasonable for servoing.
        """
        if self._latest_image is None:
            return None
        try:
            bbox = self._get_bbox_for_current_frame(target_name)
        except Exception:
            logger.exception("[detect+servo] VLM query failed for '%s'", target_name)
            return None

        if bbox is None:
            return None

        if not self._bbox_reasonable_for_tracking(bbox):
            logger.warning(
                "[detect+servo] bbox for '%s' too large/small for servoing (%s) — skip",
                target_name,
                bbox,
            )
            return None

        logger.info(
            "[detect+servo] ✓ found '%s' at bbox=%s, servoing ...", target_name, bbox
        )
        if not self._servo_to_bbox(bbox):
            logger.warning("[detect+servo] servo rotation failed for '%s'", target_name)
            return None

        logger.info("[detect+servo] ✓ servoed to '%s'", target_name)
        return f"Visually acquired '{target_name}'"

    def _scan_room_for_object(
        self, target_name: str, *, stored_yaw: float | None = None
    ) -> str | None:
        """360° in-room search: optional stored bearing, then detect + N rotations."""
        step_deg = _rotation_step_deg()
        n_steps = _panorama_rotations()

        if stored_yaw is not None:
            euler = self._odom_euler_tuple()
            if euler is not None:
                diff = stored_yaw - euler[2]
                diff = math.atan2(math.sin(diff), math.cos(diff))
                diff_deg = math.degrees(diff)
                if abs(diff_deg) > 5.0:
                    logger.info(
                        "[room_scan] turning %.1f° to face '%s' (stored yaw=%.1f°)",
                        diff_deg,
                        target_name,
                        math.degrees(stored_yaw),
                    )
                    self._rotate_in_place_degrees(diff_deg)
                    time.sleep(0.5)

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
        for step in range(n_steps):
            self._rotate_in_place_degrees(step_deg)
            time.sleep(0.5)
            try:
                result = self._detect_and_servo(target_name)
                if result:
                    logger.info(
                        "[room_scan] ✓ '%s' found at scan step %d/%d",
                        target_name,
                        step + 1,
                        n_steps,
                    )
                    return result
            except Exception:
                logger.exception(
                    "[room_scan] detect-and-servo error for '%s' at step %d/%d",
                    target_name,
                    step + 1,
                    n_steps,
                )
        return None

    def _visual_acquire_object(
        self, target_name: str, stored_yaw: float | None = None
    ) -> str | None:
        """After reaching object coordinates, visually locate and face the object.

        If a stored bearing is available, rotate to face that direction first,
        then use VLM detection and visual servoing to centre the object in view.

        Args:
            target_name: The object name to search for.
            stored_yaw: If known, the world-frame yaw angle (radians) to face first.

        Returns:
            Success message if object was visually acquired, None otherwise.
        """
        result = self._scan_room_for_object(target_name, stored_yaw=stored_yaw)
        if result:
            return result
        logger.warning("[visual_acquire] ✗ '%s' not found visually", target_name)
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

    # ------------------------------------------------------------------ #
    #  Simple-format VLM helpers (per-frame, bbox-aware)                  #
    # ------------------------------------------------------------------ #

    _VLM_SIMPLE_BBOX_PROMPT = (
        "识别图中物品，最多8个。"
        "仅输出一行，格式：名称,x1,y1,x2,y2;名称,x1,y1,x2,y2;"
        "坐标为0-1000整数，相对原图。"
        "不要解释、不要JSON、不要换行，物品名称要求为中文。"
        "跳过墙面、地面、天花板。"
    )

    #: Camera horizontal field-of-view in degrees.  Override per robot subclass.
    _camera_hfov_deg: float = 90.0

    def _parse_simple_vlm_response(
        self,
        response: str,
        capture_yaw: float,
    ) -> list[dict[str, Any]]:
        """Parse the compact ``名称,x1,y1,x2,y2;`` response into internal object dicts.

        Each returned dict contains:
        - ``"name"`` — Chinese object name
        - ``"bbox"`` — [x1, y1, x2, y2] in 0-1000 coords
        - ``"yaw_offset"`` — horizontal angle offset from camera centre (radians)
        - ``"object_yaw"`` — absolute world yaw = capture_yaw + yaw_offset (radians)
        """
        items = parse_simple_bbox_line(response)
        result: list[dict[str, Any]] = []
        for item in items:
            x1, y1, x2, y2 = item["bbox"]
            offset = yaw_offset_from_bbox(x1, y1, x2, y2, self._camera_hfov_deg)
            item["yaw_offset"] = offset
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
        """Run VLM on a single captured frame and store detected objects.

        Uses the compact bbox output format (``名称,x1,y1,x2,y2;``) so that the
        horizontal offset of each detected object relative to the image centre
        line can be combined with the robot's capture heading to produce a more
        accurate world-frame yaw for each landmark.

        ``rotation[2]`` (yaw in radians from odom) is adjusted per-object by
        ``yaw_offset_from_bbox`` before the landmark is stored.
        """
        from dimos.msgs.sensor_msgs.Image import Image as DimosImage

        if self._vl_model is None:
            logger.warning("[VLM single-frame] no VLM model available, skipping room=%r", room_name)
            return

        image = image_data if isinstance(image_data, DimosImage) else DimosImage.from_numpy(image_data)
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
            name = _normalize_vlm_object_name(item.get("name", "").strip())
            if not name:
                continue
            obj_yaw = float(item.get("object_yaw", capture_yaw))
            obj_rotation = (rotation[0], rotation[1], obj_yaw)
            bbox = item.get("bbox", [])
            meta: dict[str, Any] = {
                "observed_position": list(position),
                "observed_rotation": list(obj_rotation),
                "room_name": room_name,
                "bbox_0to1000": bbox,
                "yaw_offset_deg": round(math.degrees(float(item.get("yaw_offset", 0.0))), 1),
                "capture_yaw_deg": round(math.degrees(capture_yaw), 1),
            }
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

    def _get_bbox_for_current_frame(self, query: str) -> BBox | None:
        if self._latest_image is None:
            return None

        return get_object_bbox_from_image(self._vl_model, self._latest_image, query)

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
                    if obj_rec is not None and obj_rec.record_type == RecordType.LANDMARK:
                        stored_yaw = (
                            obj_rec.rotation[2] if abs(obj_rec.rotation[2]) > 1e-6 else None
                        )
                    if stored_yaw is not None:
                        vis_msg = self._visual_acquire_object(query, stored_yaw)
                        if vis_msg:
                            return vis_msg
                    found = self._navigate_to_object(query, timeout=15.0)
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
            vis_msg = self._visual_acquire_object(query, stored_yaw)
            if vis_msg:
                return f"{prefix} ({vis_msg})"
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
        return self._navigate_to(goal_pose, message)

    @skill
    def stop_navigation(self) -> str:
        """Immediatly stop moving."""

        if not self._skill_started:
            raise ValueError(f"{self} has not been started.")

        self._cancel_goal_and_stop()

        return "Stopped"

    @skill
    def stop_all_motion(self) -> str:
        """Cancel navigation/tracking and recover the Unitree to a stable standing state."""

        if not self._skill_started:
            raise ValueError(f"{self} has not been started.")

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

        # Auto-fallback: if this is an object landmark and visual acquire failed,
        # sweep all other rooms instead of giving up.
        if (
            target.record_type == RecordType.LANDMARK
            and "could not visually acquire" in result.lower()
        ):
            logger.info(
                "[navigate_to_landmark] Visual acquire failed for %r, falling back to room sweep",
                name,
            )
            self._sweep_skip_rooms = set()
            meta = target.metadata or {}
            room = meta.get("room_name")
            if not room:
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
            return (
                f"{result} (fallback: swept all rooms — '{name}' not found in any known room)"
            )

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
        target_pose = self._record_pose_in_navigation_frame(target)
        target_x = float(target_pose.position.x)
        target_y = float(target_pose.position.y)
        target_z = float(target_pose.position.z)
        target_yaw = float(target_pose.orientation.to_euler().z)
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
                # Transform old-session waypoints into current odom frame
                # so the topology graph and navigation target share a frame.
                if self._should_transform_persisted_record(r):
                    tx, ty = self._persisted_to_current.to_current(
                        float(r.position[0]), float(r.position[1])
                    )
                    r = SpatialRecord(
                        name=r.name,
                        record_type=r.record_type,
                        position=(tx, ty, float(r.position[2])),
                        rotation=(
                            float(r.rotation[0]),
                            float(r.rotation[1]),
                            self._persisted_to_current.yaw_to_current(float(r.rotation[2])),
                        ),
                        session_id=r.session_id,
                        metadata=r.metadata,
                        state=r.state,
                    )
                topo.add_record(r)

            all_waypoints: list[Any] = []
            if self._latest_odom is not None:
                pos = self._latest_odom.position
                all_waypoints = topo.shortest_path(
                    float(pos.x), float(pos.y), target_x, target_y
                )
                logger.info(
                    "[L3]   Topology: %d waypoints from (%.2f, %.2f) → '%s' (%.2f, %.2f)",
                    len(all_waypoints),
                    pos.x,
                    pos.y,
                    target.name,
                    target_x,
                    target_y,
                )

            last_corr = [0.0]
            final_dest = PoseStamped(
                position=make_vector3(target_x, target_y, target_z),
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
                self._set_navigation_goal(segment_goal)
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
                obj_yaw = target_yaw
                if abs(obj_yaw) < 1e-6:
                    obj_yaw = self._yaw_toward_point(target_x, target_y)
                standoff_pose = PoseStamped(
                    position=make_vector3(
                        target_x,
                        target_y,
                        target_z,
                    ),
                    orientation=Quaternion.from_euler(Vector3(0.0, 0.0, obj_yaw)),
                    frame_id="map",
                )
            else:
                yaw = target_yaw
                offset_x = 1.5 * math.cos(yaw)
                offset_y = 1.5 * math.sin(yaw)
                standoff_pose = PoseStamped(
                    position=make_vector3(
                        target_x + offset_x,
                        target_y + offset_y,
                        target_z,
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
                    active_goal = standoff_pose
                else:
                    inch = self._inch_goal_toward(standoff_pose, effective_arrival_distance)
                    if inch is None:
                        # _inch_goal_toward returns None when already within
                        # arrival_distance + 2cm — close enough, stop inching.
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
                nav_idle = self._navigation.get_state() == NavigationState.IDLE
                since_last_replan = time.time() - _last_replan_time
                if (goal_changed or nav_idle) and since_last_replan >= _min_replan_interval:
                    logger.info(
                        "Approach replan #%d: goal_changed=%s nav_idle=%s since_last=%.1fs",
                        _stuck_replan_streak + 1,
                        goal_changed,
                        nav_idle,
                        since_last_replan,
                    )
                    self._set_navigation_goal(active_goal)
                    _last_active_goal_pos = cur_pos
                    _last_replan_time = time.time()
                elif goal_changed or nav_idle:
                    logger.debug(
                        "Approach replan skipped: since_last=%.1fs < min=%.1fs",
                        since_last_replan,
                        _min_replan_interval,
                    )

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

                # If the robot is close and keeps replanning (obstacle / veering)
                # without making progress, give up and proceed — close enough.
                if self._navigation.get_state() == NavigationState.IDLE and dist <= 2.0:
                    cur_pos_2d = (
                        self._latest_odom.position.x,
                        self._latest_odom.position.y,
                    ) if self._latest_odom else None
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

        # First attempt
        err = _try_navigate()
        if err is None:
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

        # Severe drift recovery: re-plan topology from current odom position
        logger.warning(
            "Severe drift on first attempt; re-planning topology from current odom for retry"
        )
        self._navigation.cancel_goal()
        time.sleep(0.5)

        err2 = _try_navigate()
        if err2 is None:
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
            return f"{base} (arrival_action not run)."

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
