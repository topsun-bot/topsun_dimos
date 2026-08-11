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

"""视觉回充模块共用的纯数据类型."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
from numpy.typing import NDArray


class RechargeState(str, Enum):
    """Lifecycle states for one visual docking attempt."""

    IDLE = "idle"
    ACQUIRE = "acquire"
    ALIGN_YAW = "align_yaw"
    ALIGN_LATERAL = "align_lateral"
    APPROACH = "approach"
    SETTLE = "settle"
    LIE_DOWN = "lie_down"
    VERIFY_CHARGE = "verify_charge"
    VISUAL_DOCKED = "visual_docked"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AutoRechargeState(str, Enum):
    """Navigation-integrated automatic recharge lifecycle."""

    MONITOR = "monitor"
    VALIDATE_DOCK = "validate_dock"
    CLAIM_TASK = "claim_task"
    STAGING_NAV = "staging_nav"
    ACQUIRE_FOR_SERVO = "acquire_for_servo"
    CLAIM_SERVO = "claim_servo"
    STOP_AND_OBSERVE = "stop_and_observe"
    VISUAL_SERVO = "visual_servo"
    RECOVERY_STOP = "recovery_stop"
    RECOVERY_BACKOFF = "recovery_backoff"
    RECOVERY_REACQUIRE = "recovery_reacquire"
    FINAL_SETTLE = "final_settle"
    LIE_DOWN = "lie_down"
    VERIFY_CHARGE = "verify_charge"
    STAND_UP_RETRY = "stand_up_retry"
    CHARGING_HOLD = "charging_hold"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AutoRechargeErrorCode(str, Enum):
    """Stable failure codes emitted by the integrated controller."""

    IMAGE_STALE = "input_image_stale"
    ODOM_STALE = "input_odom_stale"
    LOWSTATE_STALE = "input_lowstate_stale"
    COSTMAP_STALE = "costmap_stale"
    MARKER_POSE_UNSTABLE = "marker_pose_unstable"
    MARKER_EDGE_CLIPPED = "marker_edge_clipped"
    FRAME_MISMATCH = "frame_transform_unavailable"
    DOCK_GEOMETRY_INVALID = "dock_geometry_invalid"
    DOCK_TARGET_BLOCKED = "dock_target_blocked"
    STAGING_TARGET_BLOCKED = "staging_target_blocked"
    STAGING_CORRIDOR_BLOCKED = "staging_corridor_blocked"
    STAGING_TIMEOUT = "staging_timeout"
    TASK_OWNERSHIP_TIMEOUT = "task_ownership_timeout"
    SERVO_OWNERSHIP_TIMEOUT = "servo_ownership_timeout"
    MARKER_NOT_FOUND_AT_STAGE = "marker_not_found_at_stage"
    NEAR_FIELD_REACQUIRE_FAILED = "near_field_reacquire_failed"
    RECOVERY_CORRIDOR_BLOCKED = "recovery_corridor_blocked"
    RECOVERY_DISTANCE_EXCEEDED = "recovery_distance_exceeded"
    VISUAL_SERVO_TIMEOUT = "visual_servo_timeout"
    LIE_DOWN_FAILED = "lie_down_failed"
    STAND_UP_FAILED = "stand_up_failed"
    CHARGE_UNVERIFIED = "charge_unverified"
    TOTAL_TIMEOUT = "total_timeout"
    OPERATOR_CANCELLED = "operator_cancelled"


class RechargeErrorCode(str, Enum):
    """One terminal reason, latched when a task first fails."""

    IMAGE_STALE = "image_stale"
    LOWSTATE_STALE = "lowstate_stale"
    MARKER_NOT_FOUND = "marker_not_found"
    MARKER_LOST = "marker_lost"
    STATE_TIMEOUT = "state_timeout"
    TOTAL_TIMEOUT = "total_timeout"
    LIE_DOWN_FAILED = "lie_down_failed"
    CHARGE_UNVERIFIED = "charge_unverified"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class MarkerObservation:
    """一帧通过质量门的 AprilTag 观测 (相机光学坐标系, 单位 m).

    x_m/z_m 来自 PnP tvec; yaw_rad=atan2(x_m,z_m) 为码中心 bearing, 非码面旋转.
    observed_at 为宿主 monotonic 时间, 用于丢码恢复时区分新旧帧.
    """

    corners_px: NDArray[np.float64]
    x_m: float
    y_m: float
    z_m: float
    yaw_rad: float
    reprojection_error_px: float
    observed_at: float


@dataclass(frozen=True)
class DockObservation(MarkerObservation):
    """Quality-gated marker pose with geometry needed for staging and recovery."""

    marker_id: int
    image_width: int
    image_height: int
    rvec: NDArray[np.float64]
    tvec: NDArray[np.float64]
    min_corner_margin_px: float
    marker_side_px: float

    @property
    def bearing_rad(self) -> float:
        """Marker-centre bearing used by the first visual-servo version."""
        return self.yaw_rad


@dataclass(frozen=True)
class StableDockObservation:
    """Median pose and robust dispersion over a short image window."""

    observation: DockObservation
    valid_frames: int
    window_frames: int
    z_mad_m: float
    x_mad_m: float
    bearing_mad_rad: float
    normal_yaw_mad_rad: float


@dataclass(frozen=True)
class PlanarPose:
    """Pose in the odometry/costmap plane."""

    x: float
    y: float
    yaw: float
    frame_id: str
    observed_at: float


@dataclass(frozen=True)
class DockTarget:
    """Frozen marker, staging and final robot-centre poses in one frame."""

    marker_pose: PlanarPose
    staging_pose: PlanarPose
    final_pose: PlanarPose
    marker_normal_x: float
    marker_normal_y: float
    target_camera_z_m: float
    frozen_at: float


@dataclass(frozen=True)
class ReachabilityResult:
    """Result of a strict costmap/frame reachability check."""

    accepted: bool
    reason: str | None = None


@dataclass(frozen=True)
class DockError:
    """平面三自由度对接误差 (控制器内部用)."""

    forward_m: float  # z_m - target, 正=还需靠近桩
    lateral_m: float  # 即 x_m, 正=码在画面右侧
    yaw_rad: float  # 即 bearing, 正=码在画面右侧


@dataclass(frozen=True)
class RechargeCommand:
    """单次 tick 的速度指令 (4G 路径下为 WIRELESS_CONTROLLER 摇杆比例)."""

    forward_mps: float = 0.0  # 映射 ly, 硬下限 0.10
    lateral_mps: float = 0.0  # 映射 -lx
    yaw_rad_s: float = 0.0  # 映射 angular.z, connection 内 rx=-angular.z
    request_liedown: bool = False
    request_standup: bool = False
    pulse_duration_s: float = 0.0
    reason: str = ""


@dataclass(frozen=True)
class TerminalFailure:
    """Immutable diagnosis written once when a task reaches FAILED."""

    code: RechargeErrorCode
    state: RechargeState
    message: str
    elapsed_s: float
    dock_error: DockError | None


@dataclass(frozen=True)
class AutoRechargeFailure:
    """First terminal failure of an automatic recharge task."""

    code: AutoRechargeErrorCode
    state: AutoRechargeState
    message: str
    elapsed_s: float
    last_observation: DockObservation | None
