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

import math
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
from dimos.navigation.visual.query import get_object_bbox_from_image
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

_VLM_OBJECT_LIST_PROMPT = (
    "列出图中所有可单独指认的物体（家具、电器、设备、装饰、人等）。\n"
    "【硬性要求】JSON 里每个 name 必须是 1–4 个汉字的中文名词。"
    "禁止英文：不要写 computer/desk/chair/monitor/table。\n"
    "正确示例：电脑、书桌、办公椅、灭火器、电视。\n"
    "Return ONLY a JSON array: "
    '[{"name": "<中文名>", "description": "<简短中文说明>"}]. '
    "跳过墙面、地面、天花板、门。若无物体，返回 []."
)

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
    _object_tracking: ObjectTrackingSpec | None = None
    _unitree_skill_container: UnitreeSkillContainer | None = None
    _frontier_explorer: WavefrontFrontierExplorer | None = None
    _memory_session_id: str = ""

    color_image: In[Image]
    odom: In[PoseStamped]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._skill_started = False
        self._memory_session_id = f"session_{int(time.time())}"

        self._vl_model = _create_vl_model()
        _log_vlm_runtime_config(self._vl_model)

    @rpc
    def start(self) -> None:
        super().start()
        self.register_disposable(Disposable(self.color_image.subscribe(self._on_color_image)))
        self.register_disposable(Disposable(self.odom.subscribe(self._on_odom)))
        self._skill_started = True

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
        if (
            "severe visual/odom drift" in nav_landmark_msg.lower()
            or "aborted" in nav_landmark_msg.lower()
            or "navigation skipped" in nav_landmark_msg.lower()
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
            self._rotate_scan_in_place()
            found = self._navigate_to_object(query, timeout=8.0)
            if found:
                return found
        return None

    def _rotate_scan_in_place(self) -> None:
        us = self._unitree_skill_container
        if us is None:
            logger.warning("360 scan: no UnitreeSkillContainer wired; pausing briefly instead")
            time.sleep(1.0)
            return
        try:
            us.relative_move(forward=0.0, left=0.0, degrees=360.0)
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
            return float(self._latest_odom.orientation.z)
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
                room_loc = RobotLocation(
                    name=room_name,
                    position=(ox, oy, float(self._latest_odom.position.z)),
                    rotation=(
                        float(self._latest_odom.orientation.x),
                        float(self._latest_odom.orientation.y),
                        float(self._latest_odom.orientation.z),
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
    ) -> int:
        stored = 0
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
                '"image_indices": [0, 2]}]. '
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
        rot = self._latest_odom.orientation if self._latest_odom else None
        position = (float(pos.x), float(pos.y), float(pos.z)) if pos else (0.0, 0.0, 0.0)
        rotation = (float(rot.x), float(rot.y), float(rot.z)) if rot else (0.0, 0.0, 0.0)
        stored = self._store_detected_objects(objects, position, rotation)
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
        idx = int(matched_indices[0])
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
                    found = self._navigate_to_object(query, timeout=15.0)
                    if found:
                        return found
                    return (
                        f"VLM found '{query}' in room '{room_name}' image; "
                        f"reached the room but could not track '{query}' in view. {nav_msg}"
                    )

        pos_x = float(meta.get("pos_x", 0))
        pos_y = float(meta.get("pos_y", 0))
        if abs(pos_x) < 1e-6 and abs(pos_y) < 1e-6:
            logger.warning(
                "[vlm_memory] no coordinates for '%s' (source=%s meta=%s)",
                query,
                source,
                meta,
            )
            return None

        goal_pose = PoseStamped(
            position=make_vector3(pos_x, pos_y, 0.0),
            orientation=Quaternion.from_euler(Vector3(0.0, 0.0, 0.0)),
            frame_id="map",
        )
        return self._navigate_to(
            goal_pose,
            f"Found '{query}' via VLM batch inspection of {len(candidates)} stored images "
            f"(source={source}, index={idx}).",
        )

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

        return self._navigate_to_landmark(
            target,
            arrival_action=effective_action,
            arrival_distance=arrival_distance,
            run_arrival_action=True,
        )

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
                goal_yaw = self._yaw_toward_point(target.position[0], target.position[1])
                standoff_pose = PoseStamped(
                    position=make_vector3(
                        target.position[0],
                        target.position[1],
                        target.position[2],
                    ),
                    orientation=Quaternion.from_euler(Vector3(0.0, 0.0, goal_yaw)),
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
            if run_arrival_action:
                return self._run_arrival_action(arrival_action, tname)
            return f"Arrived near '{tname}' standoff (arrival_action not run)."

        # Severe drift recovery: re-plan topology from current odom position
        logger.warning(
            "Severe drift on first attempt; re-planning topology from current odom for retry"
        )
        self._navigation.cancel_goal()
        time.sleep(0.5)

        err2 = _try_navigate()
        if err2 is None:
            tname = target.name or target.record_id
            if run_arrival_action:
                return self._run_arrival_action(arrival_action, tname)
            return f"Arrived near '{tname}' standoff after drift recovery (arrival_action not run)."

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
