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

"""Strict costmap gates before automatic recharge takes navigation ownership."""

from __future__ import annotations

import math

import numpy as np

from dimos.msgs.nav_msgs.OccupancyGrid import OccupancyGrid
from dimos.robot.unitree.go2.recharge.config import RechargeConfig
from dimos.robot.unitree.go2.recharge.types import (
    DockObservation,
    DockTarget,
    PlanarPose,
    ReachabilityResult,
)


def _disk_is_clear(
    costmap: OccupancyGrid,
    x_m: float,
    y_m: float,
    clearance_m: float,
    threshold: int,
) -> bool:
    centre = costmap.world_to_grid((x_m, y_m, 0.0))
    centre_x, centre_y = round(centre.x), round(centre.y)
    radius = max(1, math.ceil(clearance_m / costmap.resolution))
    for grid_y in range(centre_y - radius, centre_y + radius + 1):
        for grid_x in range(centre_x - radius, centre_x + radius + 1):
            if (grid_x - centre_x) ** 2 + (grid_y - centre_y) ** 2 > radius**2:
                continue
            if not (0 <= grid_x < costmap.width and 0 <= grid_y < costmap.height):
                return False
            value = int(costmap.grid[grid_y, grid_x])
            if value < 0 or value >= threshold:
                return False
    return True


def corridor_is_clear(
    costmap: OccupancyGrid,
    start: PlanarPose,
    end: PlanarPose,
    config: RechargeConfig,
) -> bool:
    """Sample a footprint-width corridor; unknown cells are intentionally blocked."""
    distance = math.hypot(end.x - start.x, end.y - start.y)
    samples = max(2, math.ceil(distance / max(costmap.resolution, 0.01)) + 1)
    for fraction in np.linspace(0.0, 1.0, samples):
        x_m = start.x + float(fraction) * (end.x - start.x)
        y_m = start.y + float(fraction) * (end.y - start.y)
        if not _disk_is_clear(
            costmap,
            x_m,
            y_m,
            config.corridor_clearance_m,
            config.occupancy_threshold,
        ):
            return False
    return True


def validate_dock_target(
    observation: DockObservation,
    robot_pose: PlanarPose,
    target: DockTarget,
    costmap: OccupancyGrid,
    config: RechargeConfig,
) -> ReachabilityResult:
    """Validate image envelope, frame agreement, endpoints and staging corridor."""
    if not config.auto_detect_min_z_m <= observation.z_m <= config.auto_detect_max_z_m:
        return ReachabilityResult(False, "marker_outside_auto_range")
    if observation.min_corner_margin_px < config.min_corner_margin_px:
        return ReachabilityResult(False, "marker_edge_clipped")
    if robot_pose.frame_id != costmap.frame_id or target.marker_pose.frame_id != costmap.frame_id:
        return ReachabilityResult(False, "frame_transform_unavailable")
    if not _disk_is_clear(
        costmap,
        target.final_pose.x,
        target.final_pose.y,
        config.corridor_clearance_m,
        config.occupancy_threshold,
    ):
        return ReachabilityResult(False, "dock_target_blocked")
    if not _disk_is_clear(
        costmap,
        target.staging_pose.x,
        target.staging_pose.y,
        config.corridor_clearance_m,
        config.occupancy_threshold,
    ):
        return ReachabilityResult(False, "staging_target_blocked")
    if not corridor_is_clear(costmap, robot_pose, target.staging_pose, config):
        return ReachabilityResult(False, "staging_corridor_blocked")
    return ReachabilityResult(True)
