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

import math

import numpy as np
from scipy.ndimage import grey_opening, label

from dimos.agents.skills.speak_skill_spec import SpeakSkillSpec
from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.core.stream import In
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2
from dimos.navigation.replanning_a_star.module_spec import ReplanningAStarPlannerSpec
from dimos.robot.unitree.go2.step_over_config import StepOverConfig
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


def _quat_to_yaw(qx: float, qy: float, qz: float, qw: float) -> float:
    """Proper quaternion to yaw conversion, handling non-zero roll/pitch."""
    siny_cosp = 2.0 * (qw * qz + qx * qy)
    cosy_cosp = 1.0 - 2.0 * (qy * qy + qz * qz)
    return math.atan2(siny_cosp, cosy_cosp)


def _pointcloud2_to_xyz(cloud: PointCloud2) -> np.ndarray:
    """Extract (N, 3) float32 [x, y, z] from a DimOS PointCloud2 message."""
    tcloud = cloud.pointcloud_tensor
    if "positions" not in tcloud.point:
        return np.zeros((0, 3), dtype=np.float32)
    return tcloud.point["positions"].numpy()


def _extract_roi_height_map(
    xyz: np.ndarray,
    robot_pose: tuple[float, float, float],  # (x, y, yaw) in world frame
    roi_start: float,
    roi_end: float,
    roi_half_width: float,
    cell_size: float = 0.05,
) -> np.ndarray | None:
    """Extract a 2D height map from points within the ROI strip ahead of the robot.

    Returns a (rows, cols) array of max z per cell. Cells with no points are -inf.
    Returns None if too few ROI points.
    """
    rx, ry, ryaw = robot_pose
    cos_yaw, sin_yaw = np.cos(ryaw), np.sin(ryaw)

    dx = xyz[:, 0] - rx
    dy = xyz[:, 1] - ry

    forward = dx * cos_yaw + dy * sin_yaw
    lateral = -dx * sin_yaw + dy * cos_yaw

    in_roi = (
        (forward >= roi_start)
        & (forward <= roi_end)
        & (np.abs(lateral) <= roi_half_width)
    )
    if np.sum(in_roi) < 10:
        return None

    fwd_roi = forward[in_roi]
    lat_roi = lateral[in_roi]
    z_roi = xyz[in_roi, 2]

    # Use ceil to avoid losing a cell on floating-point truncation.
    n_cols = max(1, math.ceil((roi_end - roi_start) / cell_size))
    n_rows = max(1, math.ceil(2 * roi_half_width / cell_size))
    height_map = np.full((n_rows, n_cols), -np.inf, dtype=np.float32)

    col_idx = np.clip(((fwd_roi - roi_start) / cell_size).astype(np.int32), 0, n_cols - 1)
    row_idx = np.clip(((lat_roi + roi_half_width) / cell_size).astype(np.int32), 0, n_rows - 1)

    for i in range(len(z_roi)):
        r, c = row_idx[i], col_idx[i]
        if z_roi[i] > height_map[r, c]:
            height_map[r, c] = z_roi[i]

    return height_map


def _estimate_ground_z(height_map: np.ndarray) -> float:
    """Estimate local ground height as the 10th percentile of valid cell max-z."""
    valid = height_map[height_map > -np.inf]
    if len(valid) == 0:
        return 0.0
    return float(np.percentile(valid, 10))


def _morphological_open(height_map: np.ndarray, iterations: int = 1) -> np.ndarray:
    """Remove isolated high spikes via grey-level morphological opening.

    Uses 3x3 erosion (min) followed by dilation (max) so that isolated high cells
    are pulled down to the surrounding ground level while large obstacles are preserved.
    """
    if iterations <= 0:
        return height_map
    valid = height_map > -np.inf
    work = np.where(valid, height_map, -1e6)
    footprint = np.ones((3, 3))
    for _ in range(iterations):
        work = grey_opening(work, footprint=footprint, mode="nearest")
    result = height_map.copy()
    result[valid] = work[valid]
    return result


def compute_step_over_geometry(
    height_map: np.ndarray,
    z_ground: float,
    ignore_noise_m: float = 0.05,
    max_step_height_m: float = 0.10,
    cell_size: float = 0.05,
) -> dict:
    """Analyze a 2.5D height map and return obstacle geometry.

    Returns dict with keys: h (max obstacle height above ground, m),
    d (ledge depth along path, m), width (lateral extent, m),
    gap (min gap between obstacles, m), num_obstacles (int).
    """
    result: dict = {
        "h": 0.0,
        "d": 0.0,
        "width": 0.0,
        "gap": float("inf"),
        "num_obstacles": 0,
    }

    valid = height_map > -np.inf
    if not valid.any():
        return result

    above_noise = (height_map - z_ground) > ignore_noise_m
    if not above_noise.any():
        return result

    h = float(np.max(height_map[above_noise]) - z_ground)
    result["h"] = h
    if h > max_step_height_m:
        return result

    _, n_cols = above_noise.shape
    col_has_obs = np.any(above_noise, axis=0)

    # Continuous obstacle depth from the first occupied column.
    occupied = np.where(col_has_obs)[0]
    if len(occupied) > 0:
        first_col = occupied[0]
        gaps_after = np.where(~col_has_obs[first_col:])[0]
        if len(gaps_after) > 0:
            contiguous_end = first_col + gaps_after[0]
        else:
            contiguous_end = n_cols
        result["d"] = float((contiguous_end - first_col) * cell_size)

    row_has_obs = np.any(above_noise, axis=1)
    result["width"] = float(np.sum(row_has_obs) * cell_size)

    labeled, n_obs = label(above_noise)
    result["num_obstacles"] = int(n_obs)
    if n_obs >= 2:
        col_ranges = []
        for lbl in range(1, n_obs + 1):
            obs_cols = np.where(np.any(labeled == lbl, axis=0))[0]
            if len(obs_cols) > 0:
                col_ranges.append((obs_cols[0], obs_cols[-1]))
        col_ranges.sort(key=lambda r: r[0])
        gaps = []
        for i in range(len(col_ranges) - 1):
            left_end = col_ranges[i][1]
            right_start = col_ranges[i + 1][0]
            gaps.append(float(right_start - left_end - 1) * cell_size)
        if gaps:
            result["gap"] = min(gaps)

    return result


def is_passable(
    geometry: dict,
    belly_clearance: float,
    cfg: StepOverConfig,
) -> bool:
    """Determine if an obstacle is safe to step over.

    Belly clearance g is the vertical gap between the robot's underbody
    and the obstacle top: g = body_bottom_height_m - h.
    """
    h = geometry["h"]
    if h <= cfg.ignore_noise_m:
        return True
    if h > cfg.max_step_height_m:
        return False
    if belly_clearance < cfg.min_belly_clearance_m:
        return False
    if geometry["d"] > cfg.max_ledge_depth_m:
        return False
    if geometry["num_obstacles"] >= 2 and geometry["gap"] < cfg.min_gap_width_m:
        return False

    w = geometry["width"]
    if w < cfg.isolated_obstacle_width_min_m:
        return True  # narrow spike — filtered as noise
    if w > cfg.isolated_obstacle_width_max_m:
        return False  # too wide — beyond isolated obstacle envelope

    return True


class StepOverModule(Module):
    """Auto-detect low obstacles ahead and decide to step over or stop-and-speak.

    Subscribes to *global_map* (voxel PointCloud2) and *odom* (PoseStamped).
    Every incoming point cloud triggers a 2.5D geometric analysis of a forward
    ROI strip. The module requires *stable_frames_required* consecutive passable
    frames before allowing passage, and *blocked_stable_frames* consecutive
    non-passable frames before canceling the goal.

    This is an **automatic** navigation component — not an LLM-callable skill.
    """

    global_map: In[PointCloud2]
    odom: In[PoseStamped]

    _planner: ReplanningAStarPlannerSpec
    _speak_skill: SpeakSkillSpec | None = None  # optional — only in agentic blueprints
    config: StepOverConfig

    _current_pose: PoseStamped | None = None
    _enabled: bool = True
    _stable_count: int = 0
    _blocked_count: int = 0
    _last_passable: bool = True
    _last_spoke_at: float = 0.0  # monotonic timestamp to throttle speech

    async def handle_odom(self, msg: PoseStamped) -> None:
        self._current_pose = msg

    async def handle_global_map(self, msg: PointCloud2) -> None:
        if not self._enabled or self._current_pose is None:
            return

        cfg = self.config
        xyz = _pointcloud2_to_xyz(msg)
        if len(xyz) == 0:
            return

        pose = self._current_pose.pose
        qx = pose.orientation.x
        qy = pose.orientation.y
        qz = pose.orientation.z
        qw = pose.orientation.w
        yaw = _quat_to_yaw(qx, qy, qz, qw)
        robot_pose = (pose.position.x, pose.position.y, float(yaw))

        # Early skip: no voxels within analyze_distance ahead.
        rx, ry, ryaw = robot_pose
        cos_yaw, sin_yaw = np.cos(ryaw), np.sin(ryaw)
        dx = xyz[:, 0] - rx
        dy = xyz[:, 1] - ry
        forward = dx * cos_yaw + dy * sin_yaw
        in_analyze_range = (forward >= 0.0) & (forward <= cfg.analyze_distance_m)
        if not np.any(in_analyze_range):
            return

        roi_half_width = (self.config.g.robot_width / 2) + cfg.roi_width_margin_m
        height_map = _extract_roi_height_map(
            xyz, robot_pose, cfg.roi_start_m, cfg.roi_end_m, roi_half_width
        )
        if height_map is None:
            return

        height_map = _morphological_open(height_map, cfg.morph_open_iterations)

        z_ground = _estimate_ground_z(height_map)
        geometry = compute_step_over_geometry(
            height_map, z_ground, cfg.ignore_noise_m, cfg.max_step_height_m
        )

        body_bottom = cfg.body_bottom_height_m
        belly_clearance = body_bottom - geometry["h"]
        passable = is_passable(geometry, belly_clearance, cfg)

        logger.info(
            f"StepOver: h={geometry['h']:.3f} d={geometry['d']:.3f} "
            f"g={belly_clearance:.3f} width={geometry['width']:.3f} "
            f"n_obs={geometry['num_obstacles']} passable={passable} "
            f"stable={self._stable_count} blocked={self._blocked_count}"
        )

        # Gate blocking actions: only trigger when the obstacle's leading edge
        # is within execute_distance_m, measured from obstacle cells (not raw points).
        above_noise = (height_map - z_ground) > cfg.ignore_noise_m
        col_has_obs = np.any(above_noise, axis=0) if above_noise.any() else np.array([])
        obstacle_in_execute = False
        if col_has_obs.any():
            occupied = np.where(col_has_obs)[0]
            cell_size = 0.05
            leading_forward = cfg.roi_start_m + occupied[0] * cell_size
            obstacle_in_execute = leading_forward <= cfg.execute_distance_m

        # Temporal filter on passable side.
        if not self._last_passable and passable:
            self._stable_count = 1
        elif passable:
            self._stable_count += 1
        else:
            self._stable_count = 0

        # Temporal filter on blocked side: debounce before canceling.
        if self._last_passable and not passable:
            self._blocked_count = 1
        elif not passable:
            self._blocked_count += 1
        else:
            self._blocked_count = 0

        self._last_passable = passable

        # Passable and stable: allow passage.
        if passable and self._stable_count >= cfg.stable_frames_required:
            return  # allow passage — obstacle is step-overable

        # Not passable and stable: block, but only within execute_distance.
        if not passable and self._blocked_count >= cfg.stable_frames_required:
            if obstacle_in_execute:
                self._handle_blocked()

    def _handle_blocked(self) -> None:
        """Cancel navigation every call; speak only throttled."""
        logger.warning(f"StepOver: blocked — '{self.config.blocked_speech_text}'")
        self._planner.cancel_goal()

        # Throttle speech: speak at most once per 3 seconds.
        now = self._current_pose.ts if self._current_pose else 0.0
        if self._speak_skill is not None and (now - self._last_spoke_at > 3.0):
            self._last_spoke_at = now
            self._speak_skill.speak(self.config.blocked_speech_text, blocking=False)

    @rpc
    def enable(self) -> str:
        """Enable step-over detection (on by default)."""
        self._enabled = True
        self._stable_count = 0
        self._blocked_count = 0
        return "StepOverModule enabled"

    @rpc
    def disable(self) -> str:
        """Disable step-over detection."""
        self._enabled = False
        self._stable_count = 0
        self._blocked_count = 0
        return "StepOverModule disabled"
