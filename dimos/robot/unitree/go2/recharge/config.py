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

"""Go2 本机 4G Remote WebRTC 视觉回充配置.

真机验证环境: ``demo_go2_4g_aruco_recharge.py --execute --allow-liedown --until-charge``.
码规格与官方 NX 包 ``jiangtao/aruco_recharge_20250624/aruco_config.yaml`` 一致:
DICT_APRILTAG_36h11, id=0, 边长 0.1075 m.

注意: 本文件的 ``*_mps`` / ``*_rad_s`` 字段名沿用 DimOS Twist 习惯,
但 4G Remote 路径下实际含义是 WebRTC WIRELESS_CONTROLLER 摇杆比例.

2026-08-05 成功样本 (``/tmp/go2_recharge_yaw_sign_fix.log``, yaw_sign=1.0):
  预检 z≈1.43 m; settle z≈0.44 m, yaw≈3.3°; 趴下前 z≈0.34 m, yaw≈9°;
  BMS 由未充 -2172 mA 在 4 s 内进入带 A (-1048 mA).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True)
class CalibrationProfile:
    """相机内参/畸变的一组不可拆标定.

    ``image_size`` 是该 K 矩阵对应的像素分辨率. WebRTC 下采样时只允许等比例缩放 K,
    不允许裁剪或改宽高比后继续用同一主点.
    """

    camera_matrix: NDArray[np.float64]
    dist_coeffs: NDArray[np.float64]
    distortion_model: Literal["plumb_bob", "equidistant"]
    image_size: tuple[int, int] = (1280, 720)


def official_pinhole_profile() -> CalibrationProfile:
    """读取宇树官方回充包里的 pinhole K/D 初值.

    这些参数来自官方 ``aruco_config.yaml`` 的 1280x720 标定; 真机 WebRTC 640x360
    视频由 ``ArucoRechargeVision`` 按像素比例缩放 K.
    """
    return CalibrationProfile(
        camera_matrix=np.array(
            [[797.6243, 1.6166, 649.3817], [0.0, 797.8026, 362.877], [0.0, 0.0, 1.0]],
            dtype=np.float64,
        ),
        dist_coeffs=np.array([-0.3890, 0.1762, -0.0453, 0.0031, -0.0015], dtype=np.float64),
        distortion_model="plumb_bob",
    )


@dataclass(frozen=True)
class RechargeConfig:
    """视觉对接状态机参数. 摇杆字段是 4G WIRELESS_CONTROLLER 比例, 不是 SI 速度."""

    marker_id: int = 0
    marker_dictionary: str = "DICT_APRILTAG_36h11"
    marker_length_m: float = 0.1075
    # 旧控制器兼容字段；正式 DockController 使用同一官方几何目标 0.35 m.
    target_camera_marker_distance_m: float = 0.35
    # 640x360 WebRTC 下流约 2 m 时边长约 25 px; 32 会把远距码整段丢掉.
    min_marker_side_px: float = 20.0
    max_reprojection_error_px: float = 2.5
    min_stable_frames: int = 5
    image_max_age_s: float = 0.25
    lowstate_max_age_s: float = 0.50
    marker_lost_abort_s: float = 2.5
    # 4G |rx|=0.20 时约 30s 转一圈 → ω≈0.21 rad/s; 用于「按丢码前偏差角等量反转」.
    estimated_yaw_rate_rad_s: float = 0.21
    # 按偏差角算脉冲时长: budget = |yaw_error|/rate * fraction, 避免最小角速度一直转过头.
    yaw_pulse_fraction: float = 0.85
    yaw_pulse_min_s: float = 0.15
    # 0.20 摇杆转满约一圈需要 ~30s; 短于半圈会在码还没扫到时超时.
    acquire_timeout_s: float = 30.0
    # 0 = 禁用该阶段超时 (现场调试 docking 时避免误杀).
    align_timeout_s: float = 0.0
    approach_timeout_s: float = 0.0
    settle_time_s: float = 0.50
    verify_charge_timeout_s: float = 15.0
    total_timeout_s: float = 180.0
    # 近场仍允许 yaw 微调 (脉冲转向, 不再一刀切禁转). 当前 controller 未直接用 z
    # 切换策略, 该值保留给后续近场限速/容差分段.
    near_field_z_m: float = 0.60
    # 0.25 rad ≈ 14.3°. 成功样本 settle 时 yaw≈3.3°, 趴下前 yaw≈9° 均在容差内.
    align_yaw_exit_rad: float = 0.25
    align_lateral_exit_m: float = 0.50
    approach_forward_exit_m: float = 0.13
    settle_forward_m: float = 0.15
    settle_lateral_m: float = 0.50
    settle_yaw_rad: float = 0.60
    # ---- 4G Remote 摇杆比例 (非 SI 单位) ----
    # velocity_api=False 时 Twist 直接映射 WIRELESS_CONTROLLER:
    #   linear.x -> ly, linear.y -> -lx, angular.z -> -rx, 范围约 [-1, 1].
    # 字段名保留 *_mps / *_rad_s 仅为历史兼容; 语义是摇杆偏转比例.
    # 硬下限见 jiangtao/run.md 「4G Remote 摇杆死区」; 低于此值设备不动.
    # 2026-08-05 三狗 odom 标定: |ly|>=0.10 才前进; |rx|>=0.20 才转向.
    min_yaw_rad_s: float = 0.20  # |rx| 下限 (20% 杆)
    min_forward_mps: float = 0.10  # |ly| 下限 (10% 杆)
    min_lateral_mps: float = 0.15  # |lx| 下限 (暂定)
    acquire_search_yaw_rad_s: float = 0.20
    max_forward_mps: float = 0.18
    max_lateral_mps: float = 0.15
    max_yaw_rad_s: float = 0.25
    max_linear_accel_mps2: float = 0.08
    max_yaw_accel_rad_s2: float = 0.30
    yaw_gain: float = 0.7
    lateral_gain: float = 0.5
    forward_gain: float = 0.3
    # 转向符号 (2026-08-05 真机标定, 曾用 -1.0 导致 14° 偏到 43°).
    # bearing=atan2(x,z): 码在画面右侧 → bearing>0 → 需顺时针右转 → Twist.angular.z<0.
    # 公式 raw=-yaw_sign*gain*error; yaw_sign=1.0 时 bearing>0 → angular.z=-min_yaw.
    # 经 connection: rx=-angular.z, 故发负 angular.z 即正 rx.
    yaw_sign: float = 1.0
    lateral_sign: float = 1.0
    calibration: CalibrationProfile = field(default_factory=official_pinhole_profile)

    # ---- 导航集成式自动回充 ----
    odom_max_age_s: float = 0.50
    costmap_max_age_s: float = 2.00
    task_ownership_timeout_s: float = 1.00
    servo_ownership_timeout_s: float = 1.00
    min_corner_margin_px: float = 12.0
    stability_window_frames: int = 7
    stability_min_valid_frames: int = 5
    stable_z_mad_m: float = 0.04
    stable_x_mad_m: float = 0.04
    stable_bearing_mad_rad: float = 0.0523598776  # 3 deg
    stable_normal_yaw_mad_rad: float = 0.13962634  # 8 deg
    near_stable_z_mad_m: float = 0.025

    marker_to_pad_center_m: float = 0.70
    camera_to_robot_center_m: float = 0.35
    target_camera_z_m: float = 0.35
    staging_camera_z_m: float = 0.75
    staging_base_distance_from_marker_m: float = 1.10
    final_base_distance_from_marker_m: float = 0.70

    auto_detect_min_z_m: float = 0.25
    auto_detect_max_z_m: float = 2.50
    direct_servo_max_bearing_rad: float = 0.610865238  # 35 deg
    direct_servo_max_abs_x_m: float = 0.80
    direct_servo_centerline_tolerance_m: float = 0.15
    direct_servo_max_z_m: float = 1.20
    staging_goal_tolerance_m: float = 0.15
    staging_timeout_s: float = 45.0
    acquire_for_servo_timeout_s: float = 8.0
    corridor_clearance_m: float = 0.35
    occupancy_threshold: int = 50
    stopped_linear_speed_mps: float = 0.03
    stopped_yaw_rate_rad_s: float = 0.05
    stopped_hold_s: float = 0.50

    final_z_min_m: float = 0.30
    final_z_max_m: float = 0.45
    final_bearing_soft_rad: float = 0.104719755  # 6 deg
    final_bearing_hard_rad: float = 0.261799388  # 15 deg
    final_stable_time_s: float = 1.0

    near_field_start_z_m: float = 0.55
    critical_close_z_m: float = 0.28
    recovery_z_target_m: float = 0.75
    recovery_z_min_m: float = 0.70
    recovery_z_max_m: float = 0.85
    recovery_min_backoff_m: float = 0.12
    recovery_max_single_backoff_m: float = 0.55
    recovery_max_total_backoff_m: float = 0.65
    recovery_reacquire_grace_s: float = 0.60
    recovery_timeout_s: float = 12.0
    max_recovery_attempts: int = 2
    visual_servo_timeout_s: float = 60.0

    coarse_forward_axis: float = 0.18
    medium_forward_axis: float = 0.14
    fine_forward_axis: float = 0.10
    backoff_axis: float = -0.10
    pulse_yaw_axis: float = 0.20
    coarse_forward_pulse_s: float = 0.40
    medium_forward_pulse_s: float = 0.30
    fine_forward_pulse_s: float = 0.20
    backoff_pulse_s: float = 0.20
    max_yaw_pulse_s: float = 0.50
    pulse_yaw_fraction: float = 0.65
    zero_settle_s: float = 0.25
    pre_liedown_zero_s: float = 0.50
    max_dock_attempts: int = 3
