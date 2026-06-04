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

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import RLock
from typing import Any

import numpy as np
from reactivex.disposable import Disposable

from dimos.agents.annotation import skill
from dimos.agents.skills.speak_skill_spec import SpeakSkillSpec
from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.core.stream import In, Out
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Quaternion import Quaternion
from dimos.msgs.geometry_msgs.Vector3 import Vector3, make_vector3
from dimos.msgs.sensor_msgs.CameraInfo import CameraInfo
from dimos.msgs.sensor_msgs.Image import Image
from dimos.navigation.navigation_spec import NavigationInterfaceSpec
from dimos.navigation.open_goal.wildos_inference import WildOSInference
from dimos.robot.unitree.unitree_skill_spec import UnitreeSkillSpec
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

# Fallback intrinsics used until the first CameraInfo message arrives on the stream.
_FALLBACK_CAMERA_K = np.array(
    [[819.553492, 0.0, 625.284099], [0.0, 820.646595, 336.808987], [0.0, 0.0, 1.0]],
    dtype=np.float64,
)
_FALLBACK_CAMERA_HEIGHT = 0.5  # meters above ground


class OpenGoalNavigationSkillContainer(Module):
    _latest_image: Image | None = None
    _latest_odom: PoseStamped | None = None
    _latest_camera_info: CameraInfo | None = None
    _skill_started: bool = False

    _navigation: NavigationInterfaceSpec
    _speak_skill: SpeakSkillSpec
    _unitree_skill: UnitreeSkillSpec

    color_image: In[Image]
    odom: In[PoseStamped]
    camera_info: In[CameraInfo]

    goal_pose: Out[PoseStamped]
    wildos_image: Out[Image]

    def __init__(self, debug_save_dir: str = "", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._skill_started = False

        self._open_goal_model: WildOSInference | None = None
        self._open_goal_lock = RLock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="open-goal")
        self._search_active = False
        self._search_target = ""
        self._open_goal_subscription: Any = None
        self._goal_sent = False
        self._last_goal_distance = float("inf")
        self._consecutive_no_detect = 0
        self._max_no_detect_frames = 10
        self._open_goal_frame_hz: float = 5.0
        self._last_process_time = 0.0
        self._target_confirmed: bool = False
        self._confirm_stable_count: int = 0
        self._search_done = threading.Event()
        self._search_succeeded = False

        # Debug: if set, saves final confirmed frames to this directory.
        self._debug_save_dir = debug_save_dir
        if self._debug_save_dir:
            Path(self._debug_save_dir).mkdir(parents=True, exist_ok=True)
        self._debug_frame_index = 0

    @rpc
    def start(self) -> None:
        super().start()
        self.register_disposable(Disposable(self.color_image.subscribe(self._on_color_image)))
        self.register_disposable(Disposable(self.odom.subscribe(self._on_odom)))
        self.register_disposable(Disposable(self.camera_info.subscribe(self._on_camera_info)))
        self._skill_started = True

    @rpc
    def stop(self) -> None:
        self._stop_open_goal_search()
        self._executor.shutdown(wait=False)
        super().stop()

    def _on_color_image(self, image: Image) -> None:
        self._latest_image = image
        if self._search_active and self._open_goal_model is not None:
            self._executor.submit(self._process_open_goal_frame, image)

    def _on_odom(self, odom: PoseStamped) -> None:
        self._latest_odom = odom

    def _on_camera_info(self, info: CameraInfo) -> None:
        self._latest_camera_info = info

    def _get_camera_k(self) -> np.ndarray:
        if self._latest_camera_info is not None:
            return self._latest_camera_info.get_K_matrix()
        return _FALLBACK_CAMERA_K

    # ---- Open-Goal Initialization ----

    def _ensure_open_goal_model(self) -> WildOSInference:
        if self._open_goal_model is None:
            self._open_goal_model = WildOSInference()
        return self._open_goal_model

    # ---- Open-Goal Search Skills ----

    @skill
    def search_target(self, target_name: str) -> str:
        """Search for an open-vocabulary target object using open-goal visual navigation.

        Uses the WildOS RADIO+SigLIP2 model to find the target in camera images,
        estimates its 3D position, and navigates to it. Blocks until the target
        is reached or the search is cancelled. On success, speaks and sits down
        automatically.

        Args:
            target_name: Name/description of the target object (e.g. "orange flag", "blue chair").

        Returns:
            str: Status message indicating search result.
        """
        if not self._skill_started:
            raise ValueError(f"{self} has not been started.")

        with self._open_goal_lock:
            if self._search_active:
                return f"Already searching for '{self._search_target}'. Call stop_search() first."

            self._search_active = True
            self._search_target = target_name
            self._goal_sent = False
            self._last_goal_distance = float("inf")
            self._target_confirmed = False
            self._confirm_stable_count = 0
            self._search_succeeded = False
            self._search_done.clear()

        try:
            model = self._ensure_open_goal_model()
            model.encode_text([target_name])
        except Exception as e:
            with self._open_goal_lock:
                self._search_active = False
                self._search_target = ""
            logger.exception("Failed to initialize open-goal model for target '%s'", target_name)
            return f"Open-goal search failed: could not encode text '{target_name}' — {e}"

        # Block until the search completes (target confirmed) or is cancelled.
        logger.info("Open-goal search started for '%s'; blocking until done.", target_name)
        self._search_done.wait()

        if self._search_succeeded:
            logger.info("Open-goal search succeeded for '%s'.", target_name)
            try:
                self._speak_skill.speak(
                    f"已找到目标{target_name}，坐下等待指令！", blocking=False
                )
            except Exception:
                logger.exception("speak failed after open-goal search")
            try:
                self._unitree_skill.execute_sport_command("Sit")
            except Exception:
                logger.exception("execute_sport_command('Sit') failed after open-goal search")
            return f"搜索完成，已到达目标'{target_name}'。"
        else:
            logger.info("Open-goal search for '%s' was cancelled.", target_name)
            return f"搜索'{target_name}'已被取消。"

    @skill
    def stop_search(self) -> str:
        """Stop the active open-goal target search."""
        with self._open_goal_lock:
            target = self._search_target
            was_active = self._search_active
            self._stop_open_goal_search()

        if was_active:
            return f"Stopped open-goal search for '{target}'."
        return "No active open-goal search to stop."

    @skill
    def stop_navigation(self) -> str:
        """Immediately stop moving."""
        if not self._skill_started:
            raise ValueError(f"{self} has not been started.")
        self._navigation.cancel_goal()
        return "Stopped"

    def _stop_open_goal_search(self) -> None:
        with self._open_goal_lock:
            self._search_active = False
            self._search_target = ""
            self._goal_sent = False
            self._last_goal_distance = float("inf")
            self._consecutive_no_detect = 0
            self._target_confirmed = False
            self._confirm_stable_count = 0
        try:
            self._navigation.cancel_goal()
        except Exception:
            pass
        self._search_done.set()

    # ---- Frame processing ----

    def _process_open_goal_frame(self, image: Image) -> None:
        """Process a camera frame through the open-goal pipeline when search is active."""
        now = time.time()
        min_interval = 1.0 / self._open_goal_frame_hz
        if now - self._last_process_time < min_interval:
            return
        self._last_process_time = now

        with self._open_goal_lock:
            if not self._search_active or self._open_goal_model is None:
                return

        rgb = image.data
        if rgb is None or not hasattr(rgb, "shape") or len(rgb.shape) < 2:
            return

        try:
            model = self._open_goal_model
            orig_shape = rgb.shape[:2]

            trav, _, ad_spatial = model.forward(rgb)

            if ad_spatial is None:
                return

            binary_mask = model.localize_object(ad_spatial, orig_shape)

            if np.sum(binary_mask) < 10:
                self._publish_wildos_image(rgb, binary_mask, None, trav, ts=image.ts)

                with self._open_goal_lock:
                    if not self._search_active:
                        return
                    self._consecutive_no_detect += 1
                    self._confirm_stable_count = 0
                    if (
                        self._consecutive_no_detect >= self._max_no_detect_frames
                        and self._goal_sent
                    ):
                        logger.warning(
                            "Lost target '%s' for %d frames; cancelling goal and re-encoding.",
                            self._search_target,
                            self._consecutive_no_detect,
                        )
                        try:
                            self._navigation.cancel_goal()
                        except Exception:
                            pass
                        self._goal_sent = False
                        self._consecutive_no_detect = 0
                        model.encode_text([self._search_target])
                return

            with self._open_goal_lock:
                self._consecutive_no_detect = 0

            pos_3d = model.estimate_3d_position(
                binary_mask, self._get_camera_k(), _FALLBACK_CAMERA_HEIGHT
            )
            if pos_3d is None:
                self._publish_wildos_image(rgb, binary_mask, None, trav, ts=image.ts)
                return

            goal_pose = self._camera_to_goal_pose(pos_3d)
            if goal_pose is None:
                self._publish_wildos_image(rgb, binary_mask, pos_3d, trav, ts=image.ts)
                return

            dist = self._distance_to_pose(goal_pose)

            with self._open_goal_lock:
                if not self._search_active:
                    return
                self._last_goal_distance = dist

                if dist < 1.0 and self._confirm_target_close(binary_mask, pos_3d):
                    self._confirm_stable_count += 1
                    if self._confirm_stable_count >= 3:
                        self._target_confirmed = True
                else:
                    self._confirm_stable_count = 0

                if self._target_confirmed:
                    logger.info(
                        "Open-goal search complete: reached '%s' (distance=%.1fm).",
                        self._search_target,
                        dist,
                    )
                    self._publish_wildos_image(rgb, binary_mask, pos_3d, trav, ts=image.ts,
                                               save_to_disk=True)
                    self._search_succeeded = True
                    self._stop_open_goal_search()
                    return

                self._goal_sent = True

            # --- Publish goal and navigate ---
            try:
                self.goal_pose.publish(goal_pose)
            except Exception:
                logger.debug("goal_pose stream not connected; skipping publish")

            self._publish_wildos_image(rgb, binary_mask, pos_3d, trav, ts=image.ts)

            self._navigation.set_goal(goal_pose)

        except Exception:
            logger.exception(
                "Open-goal frame processing error for target '%s'",
                self._search_target if hasattr(self, '_search_target') else "?",
            )

    # ---- Visualization ----

    def _publish_wildos_image(
        self,
        rgb: np.ndarray,
        mask: np.ndarray,
        pos_3d: tuple[float, float, float] | None,
        traversability: np.ndarray | None = None,
        ts: float = 0.0,
        save_to_disk: bool = False,
    ) -> None:
        """Render and publish debug visualization overlaid on the input frame."""
        try:
            viz = WildOSInference.render_debug_viz(rgb, mask, pos_3d, traversability)
        except Exception:
            return

        try:
            self.wildos_image.publish(Image(data=viz, ts=ts))
        except Exception:
            pass

        if self._debug_save_dir and save_to_disk:
            import cv2

            path = Path(self._debug_save_dir) / f"open_goal_{self._debug_frame_index:06d}.jpg"
            cv2.imwrite(str(path), cv2.cvtColor(viz, cv2.COLOR_RGB2BGR))
            self._debug_frame_index += 1

    # ---- Coordinate transforms ----

    def _camera_to_goal_pose(self, pos_3d: tuple[float, float, float]) -> PoseStamped | None:
        """Convert camera-optical 3D position to map-frame PoseStamped."""
        if self._latest_odom is None:
            return None

        odom = self._latest_odom
        cx, cy, cz = pos_3d

        # camera optical → base_link
        bx = 0.3 + cz
        by = -cx
        bz = -cy

        # base_link → map via odometry
        odom_pos = odom.position
        odom_quat = odom.orientation

        goal_world = odom_quat.rotate_vector(Vector3(bx, by, bz))
        world_x = odom_pos.x + goal_world.x
        world_y = odom_pos.y + goal_world.y
        world_z = odom_pos.z + goal_world.z

        return PoseStamped(
            position=make_vector3(world_x, world_y, world_z),
            orientation=Quaternion(0.0, 0.0, 0.0, 1.0),
            frame_id=odom.frame_id or "map",
        )

    # ---- Proximity confirmation (WildOS-native, no VLM) ----

    def _confirm_target_close(
        self, binary_mask: np.ndarray, pos_3d: tuple[float, float, float]
    ) -> bool:
        """Confirm target is nearby using WildOS mask coverage and 3D position.

        Returns True when the mask occupies a meaningful fraction of the image
        AND the estimated 3D position is in a plausible range.
        """
        mask_ratio = np.count_nonzero(binary_mask) / binary_mask.size
        if mask_ratio < 0.005:
            return False

        _, _, cz = pos_3d
        if cz < 0.1 or cz > 2.5:
            return False

        return True

    # ---- Distance ----

    def _distance_to_pose(self, pose: PoseStamped) -> float:
        if self._latest_odom is None:
            return float("inf")
        dx = self._latest_odom.position.x - pose.position.x
        dy = self._latest_odom.position.y - pose.position.y
        return float(np.sqrt(dx * dx + dy * dy))
