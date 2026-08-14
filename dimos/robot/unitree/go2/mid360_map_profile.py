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

"""Canonical geometry-affecting profile for the Orin Mid360 navigation stack."""

from typing import Any

from dimos.hardware.sensors.lidar.pointlio.module import PointLioConfig
from dimos.robot.unitree.go2.mid360_navigation_source import (
    Go2Mid360NavigationSourceConfig,
)

MID360_MAP_PROFILE_SCHEMA_VERSION = 1
MID360_DEFAULT_MAP_VOXEL_SIZE_M = 0.05
MID360_DEFAULT_GLOBAL_MAP_EMIT_EVERY = 10

_POINTLIO_FIELDS = {
    "device_model",
    "frequency",
    "msr_freq",
    "pointcloud_freq",
    "odom_freq",
    "con_frame",
    "con_frame_num",
    "cut_frame",
    "cut_frame_time_interval",
    "time_lag_imu_to_lidar",
    "lidar_type",
    "scan_line",
    "scan_rate",
    "timestamp_unit",
    "blind",
    "point_filter_num",
    "use_imu_as_input",
    "prop_at_freq_of_imu",
    "check_satu",
    "init_map_size",
    "space_down_sample",
    "satu_acc",
    "satu_gyro",
    "acc_norm",
    "plane_thr",
    "filter_size_surf",
    "filter_size_map",
    "ivox_grid_resolution",
    "ivox_nearby_type",
    "cube_side_length",
    "det_range",
    "fov_degree",
    "imu_en",
    "start_in_aggressive_motion",
    "extrinsic_est_en",
    "imu_time_inte",
    "lidar_meas_cov",
    "acc_cov_input",
    "vel_cov",
    "gyr_cov_input",
    "gyr_cov_output",
    "acc_cov_output",
    "b_gyr_cov",
    "b_acc_cov",
    "imu_meas_acc_cov",
    "imu_meas_omg_cov",
    "match_s",
    "gravity_align",
    "gravity",
    "gravity_init",
    "extrinsic_t",
    "extrinsic_r",
    "publish_odometry_without_downsample",
    "odom_only",
}

_ADAPTER_FIELDS = {
    "odom_history_sec",
    "max_odom_bracket_sec",
    "cloud_wait_timeout_sec",
    "min_range_m",
    "max_range_m",
    "voxel_size_m",
    "self_min_xyz",
    "self_max_xyz",
    "timestamp_regression_tolerance_sec",
    "max_lidar_timestamp_step_sec",
    "max_odom_timestamp_step_sec",
    "max_odom_position_step_m",
    "max_odom_rotation_step_rad",
}


def build_mid360_preprocessing_manifest(
    *,
    map_voxel_size_m: float = MID360_DEFAULT_MAP_VOXEL_SIZE_M,
) -> dict[str, Any]:
    """Return the exact preprocessing manifest shared by recording and loading."""
    if map_voxel_size_m <= 0:
        raise ValueError("map_voxel_size_m must be positive")
    pointlio = PointLioConfig(
        frame_id="world",
        sensor_frame_id="mid360_imu_link",
        device_model="mid360s",
        pointcloud_freq=10.0,
        odom_freq=10.0,
        publish_tf=False,
    )
    adapter = Go2Mid360NavigationSourceConfig()
    return {
        "profile_schema_version": MID360_MAP_PROFILE_SCHEMA_VERSION,
        "pointlio": pointlio.model_dump(include=_POINTLIO_FIELDS, mode="json"),
        "navigation_adapter": adapter.model_dump(include=_ADAPTER_FIELDS, mode="json"),
        "live_mapping": {
            "algorithm": "voxel_grid_mapper",
            "voxel_size": map_voxel_size_m,
            "carve_columns": True,
            "emit_every": MID360_DEFAULT_GLOBAL_MAP_EMIT_EVERY,
        },
        "costmap_projection": {
            "algorithm": "height_cost",
            "resolution": 0.1,
            "can_pass_under": 0.6,
            "can_climb": 0.15,
            "ignore_noise": 0.05,
            "smoothing": 1.0,
            "frame_id": None,
        },
        "map_export": {
            "lidar_stream": "lidar",
            "frame": "world",
            "voxel_size": map_voxel_size_m,
            "pgo_tolerance": 0.30,
            "tf_tolerance": None,
            "column_carving": False,
            "denoise": False,
            "bottom_cutoff": None,
        },
    }
