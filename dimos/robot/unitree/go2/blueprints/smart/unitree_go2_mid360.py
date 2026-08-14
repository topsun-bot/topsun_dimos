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

"""Go2 2D navigation and spatial memory backed exclusively by Mid360 Point-LIO."""

from functools import partial
import os
from typing import Any, cast

from dimos.core.coordination.blueprints import autoconnect
from dimos.core.global_config import global_config
from dimos.experimental.security_demo.security_module import SecurityModule
from dimos.hardware.sensors.lidar.pointlio.module import PointLio
from dimos.hardware.sensors.lidar.virtual_mid360.recorder import Mid360PcapRecorder
from dimos.mapping.costmapper import CostMapper
from dimos.mapping.map_profile import MID360_POINTLIO_SENSOR_PROFILE, preprocess_config_hash
from dimos.mapping.relocalization.module import RelocalizationModule
from dimos.mapping.voxels.module import HealthGatedVoxelGridMapper
from dimos.msgs.sensor_msgs.PointCloud2 import pointcloud_to_rerun_height_clipped
from dimos.navigation.bbox_navigation import BBoxNavigationModule
from dimos.navigation.frontier_exploration.wavefront_frontier_goal_selector import (
    WavefrontFrontierExplorer,
)
from dimos.navigation.movement_manager.movement_manager import MovementManager
from dimos.navigation.patrolling.module import PatrollingModule
from dimos.navigation.replanning_a_star.module import ReplanningAStarPlanner
from dimos.perception.detection.door.door_spatial_memory_module import SpatialLandmarkMemoryModule
from dimos.perception.experimental.object_tracker_2d import ObjectTracker2D
from dimos.perception.experimental.perceive_loop_skill import PerceiveLoopSkill
from dimos.perception.experimental.spatial_perception import SpatialMemory
from dimos.robot.unitree.go2.blueprints.basic.unitree_go2_basic import (
    _transports_base,
    rerun_config,
)
from dimos.robot.unitree.go2.connection import GO2Connection
from dimos.robot.unitree.go2.go2_mid360_recorder import Go2Mid360NavigationRecorder
from dimos.robot.unitree.go2.go2_mid360_static_transforms import (
    ORIN_NAVIGATION_EXTRINSIC_VERSION,
    Go2OrinMid360StaticTf,
)
from dimos.robot.unitree.go2.mid360_map_profile import (
    MID360_DEFAULT_MAP_VOXEL_SIZE_M,
    build_mid360_preprocessing_manifest,
)
from dimos.robot.unitree.go2.mid360_navigation_source import Go2Mid360NavigationSource
from dimos.robot.unitree.go2.mid360_simulation_adapter import Go2Mid360SimulationAdapter
from dimos.visualization.vis_module import vis_module

_MID360_MAP_VOXEL_SIZE_M = float(
    os.environ.get("DIMOS_MID360_MAP_VOXEL_SIZE", MID360_DEFAULT_MAP_VOXEL_SIZE_M)
)
_MID360_VIEWER_MIN_HEIGHT_M = float(os.environ.get("DIMOS_MID360_VIEWER_MIN_HEIGHT_M", "-0.05"))
_MID360_VIEWER_MAX_HEIGHT_M = float(os.environ.get("DIMOS_MID360_VIEWER_MAX_HEIGHT_M", "1.50"))
_MID360_GLOBAL_MAP_EMIT_EVERY = int(os.environ.get("DIMOS_MID360_GLOBAL_MAP_EMIT_EVERY", "10"))
_MID360_SIM_RELOCALIZATION_MIN_LOCAL_POINTS = 5_000
if _MID360_VIEWER_MIN_HEIGHT_M > _MID360_VIEWER_MAX_HEIGHT_M:
    raise ValueError(
        "DIMOS_MID360_VIEWER_MIN_HEIGHT_M must be less than or equal to "
        "DIMOS_MID360_VIEWER_MAX_HEIGHT_M"
    )
if _MID360_GLOBAL_MAP_EMIT_EVERY < 1:
    raise ValueError("DIMOS_MID360_GLOBAL_MAP_EMIT_EVERY must be at least 1")
_MID360_PREPROCESSING_MANIFEST = build_mid360_preprocessing_manifest(
    map_voxel_size_m=_MID360_MAP_VOXEL_SIZE_M
)
_MID360_PREPROCESSING_HASH = preprocess_config_hash(_MID360_PREPROCESSING_MANIFEST)

_convert_mid360_global_map = partial(
    pointcloud_to_rerun_height_clipped,
    bottom_cutoff=_MID360_VIEWER_MIN_HEIGHT_M,
    top_cutoff=_MID360_VIEWER_MAX_HEIGHT_M,
)

# The generic Go2 viewer would also ingest Point-LIO's raw streams and the
# mapper's private streams. Those are duplicates of the adapted /lidar and
# /odom data and can make a remote Rerun viewer fall progressively behind.
_mid360_visual_override: dict[str, Any] = {
    **cast("dict[str, Any]", rerun_config["visual_override"]),
    "world/global_map": _convert_mid360_global_map,
    "world/merged_map": _convert_mid360_global_map,
    "world/pointlio_lidar": None,
    "world/pointlio_odometry": None,
    "world/mapping_lidar": None,
    "world/mapping_odometry": None,
    "world/local_map": None,
}
_mid360_max_hz: dict[str, float] = {
    **cast("dict[str, float]", rerun_config["max_hz"]),
    "world/lidar": 1.0,
    "world/global_map": 0.5,
    "world/global_costmap": 1.0,
}
_mid360_rerun_config: dict[str, Any] = {
    **rerun_config,
    "visual_override": _mid360_visual_override,
    "max_hz": _mid360_max_hz,
}
_with_mid360_vis = vis_module(
    viewer_backend=global_config.viewer,
    rerun_config=_mid360_rerun_config,
)

_go2_camera_and_control = GO2Connection.blueprint(
    lidar=False,
    odom=False,
    camera=True,
    publish_mount_tf=False,
).remappings(
    [
        (GO2Connection, "lidar", "go2_lidar_disabled"),
        (GO2Connection, "odom", "go2_odom_disabled"),
        (GO2Connection, "tf", "go2_tf_disabled"),
    ]
)

_go2_simulated_mid360_sensors_and_control = GO2Connection.blueprint(
    lidar=True,
    odom=True,
    camera=True,
    publish_mount_tf=False,
).remappings(
    [
        (GO2Connection, "lidar", "simulation_world_lidar"),
        (GO2Connection, "odom", "simulation_base_odom"),
        (GO2Connection, "tf", "go2_tf_disabled"),
    ]
)

_pointlio_source = PointLio.blueprint(
    frame_id="world",
    # Point-LIO's body cloud has already applied lidar->IMU extrinsics and its
    # odometry state is the same IMU/body frame.
    sensor_frame_id="mid360_imu_link",
    # The Orin-connected sensor identifies itself as Livox dev_type 35.
    device_model="mid360s",
    pointcloud_freq=10.0,
    # Point-LIO advances its externally visible corrected state per lidar scan.
    odom_freq=10.0,
    publish_tf=False,
).remappings(
    [
        (PointLio, "lidar", "pointlio_lidar"),
        (PointLio, "odometry", "pointlio_odometry"),
        (PointLio, "tf", "pointlio_tf_disabled"),
    ]
)

# MuJoCo's synthesized lidar runs at 2 Hz and can briefly pause while the
# local CPU initializes CLIP/ONNX workers. Require several missed simulation
# periods before latching a fault; the production Point-LIO gate below keeps
# its stricter 1.0 s / 0.5 s defaults.
_mid360_simulation_source = Go2Mid360NavigationSource.blueprint(
    lidar_stale_timeout_sec=3.0,
    odom_stale_timeout_sec=1.0,
)

# Minimal simulation gate used before enabling mapping or autonomous movement.
# It has no planner/MovementManager and therefore cannot produce a navigation
# command; GO2Connection only drives the MuJoCo process in this blueprint.
unitree_go2_mid360_simulation_source_validation = autoconnect(
    _transports_base,
    _go2_simulated_mid360_sensors_and_control,
    Go2Mid360SimulationAdapter.blueprint(),
    _mid360_simulation_source,
    Go2OrinMid360StaticTf.blueprint(),
).global_config(n_workers=5, robot_model="unitree_go2")

_mid360_relocalization = RelocalizationModule.blueprint(
    require_map_profile=True,
    expected_sensor_profile=MID360_POINTLIO_SENSOR_PROFILE,
    expected_preprocess_config_hash=_MID360_PREPROCESSING_HASH,
    expected_extrinsic_version=ORIN_NAVIGATION_EXTRINSIC_VERSION,
    require_navigation_source_health=True,
)

# MuJoCo's three depth cameras produce a much sparser accumulated cloud than a
# physical 360-degree Mid360. Keep the production 50k cold-start gate above,
# but let simulation exercise the actual ICP/map-load path once its stable
# synthetic map reaches 5k points.
_mid360_simulation_relocalization = RelocalizationModule.blueprint(
    require_map_profile=True,
    expected_sensor_profile=MID360_POINTLIO_SENSOR_PROFILE,
    expected_preprocess_config_hash=_MID360_PREPROCESSING_HASH,
    expected_extrinsic_version=ORIN_NAVIGATION_EXTRINSIC_VERSION,
    require_navigation_source_health=True,
    min_local_points=_MID360_SIM_RELOCALIZATION_MIN_LOCAL_POINTS,
)

_mid360_map = HealthGatedVoxelGridMapper.blueprint(
    voxel_size=_MID360_MAP_VOXEL_SIZE_M,
    emit_every=_MID360_GLOBAL_MAP_EMIT_EVERY,
)

# MuJoCo uses CPU explicitly. Production keeps VoxelGridMapper's normal device
# selection and fallback behavior, matching the existing Go2 navigation stack.
_mid360_simulation_map = HealthGatedVoxelGridMapper.blueprint(
    voxel_size=_MID360_MAP_VOXEL_SIZE_M,
    device="CPU:0",
    emit_every=_MID360_GLOBAL_MAP_EMIT_EVERY,
)

_mid360_costmap = CostMapper.blueprint(require_navigation_source_health=True)

# First-run validation stack. It intentionally contains no Go2 connection,
# planner, or movement manager, so it cannot publish robot motion commands.
unitree_go2_mid360_navigation_source_validation = autoconnect(
    _transports_base,
    _with_mid360_vis,
    _pointlio_source,
    Go2Mid360NavigationSource.blueprint(),
    Go2OrinMid360StaticTf.blueprint(),
    _mid360_map,
    _mid360_costmap,
).global_config(n_workers=8, robot_model="unitree_go2")

# Safe map-profile/relocalization validation. Like the source validation stack,
# this has no command publisher and therefore cannot move the robot.
unitree_go2_mid360_relocalization_validation = autoconnect(
    unitree_go2_mid360_navigation_source_validation,
    _mid360_relocalization,
).global_config(n_workers=9, robot_model="unitree_go2")

# This stack is assembled explicitly instead of layering Point-LIO on top of
# unitree_go2: the latter already owns lidar, odom and dynamic TF.
_unitree_go2_mid360_production = autoconnect(
    _transports_base,
    _with_mid360_vis,
    _go2_camera_and_control,
    _pointlio_source,
    Go2Mid360NavigationSource.blueprint(),
    Go2OrinMid360StaticTf.blueprint(),
    _mid360_map,
    _mid360_costmap,
    ReplanningAStarPlanner.blueprint(require_navigation_source_health=True),
    WavefrontFrontierExplorer.blueprint(require_navigation_source_health=True),
    PatrollingModule.blueprint(),
    MovementManager.blueprint(require_navigation_source_health=True),
).global_config(n_workers=12, robot_model="unitree_go2")

# Records the exact adapted lidar/odom streams used by this navigation stack.
# The db and raw Mid360 PCAP paths are intentionally supplied at launch with
# module overrides so both artifacts share one timestamped session directory.
unitree_go2_mid360_map_record = autoconnect(
    _unitree_go2_mid360_production,
    Go2Mid360NavigationRecorder.blueprint(),
    Mid360PcapRecorder.blueprint(),
).global_config(n_workers=14, robot_model="unitree_go2")

# Static/push validation recorder: no Go2 control connection and no command
# publisher. It proves the same recording path before any powered motion test.
unitree_go2_mid360_map_record_validation = autoconnect(
    unitree_go2_mid360_navigation_source_validation,
    Go2Mid360NavigationRecorder.blueprint(),
    Mid360PcapRecorder.blueprint(),
).global_config(n_workers=10, robot_model="unitree_go2")


_unitree_go2_mid360_spatial_production = (
    autoconnect(
        _unitree_go2_mid360_production,
        SpatialMemory.blueprint(new_memory=global_config.new_memory),
        SpatialLandmarkMemoryModule.blueprint(new_memory=global_config.new_memory),
        ObjectTracker2D.blueprint(frame_id="camera_link"),
        BBoxNavigationModule.blueprint(),
        PerceiveLoopSkill.blueprint(),
        SecurityModule.blueprint(camera_info=GO2Connection.camera_info_static),
    )
    .remappings(
        [
            (BBoxNavigationModule, "detection2d", "detection2darray"),
        ]
    )
    .global_config(n_workers=15, robot_model="unitree_go2")
)

_unitree_go2_mid360_relocalization_memory_production = (
    autoconnect(
        _unitree_go2_mid360_production,
        _mid360_relocalization,
        SpatialMemory.blueprint(new_memory=global_config.new_memory),
        SpatialLandmarkMemoryModule.blueprint(new_memory=global_config.new_memory),
        ObjectTracker2D.blueprint(frame_id="camera_link"),
        BBoxNavigationModule.blueprint(),
        PerceiveLoopSkill.blueprint(),
        SecurityModule.blueprint(camera_info=GO2Connection.camera_info_static),
    )
    .remappings(
        [
            (BBoxNavigationModule, "detection2d", "detection2darray"),
        ]
    )
    .global_config(n_workers=16, robot_model="unitree_go2")
)

# MuJoCo renders a world-frame synthetic lidar instead of Livox UDP packets.
# The adapter converts it back to the native Point-LIO body-frame contract so
# the same health, mapping, planning and spatial-memory modules are exercised.
_unitree_go2_mid360_simulation = autoconnect(
    _transports_base,
    _with_mid360_vis,
    _go2_simulated_mid360_sensors_and_control,
    Go2Mid360SimulationAdapter.blueprint(),
    _mid360_simulation_source,
    Go2OrinMid360StaticTf.blueprint(),
    _mid360_simulation_map,
    _mid360_costmap,
    ReplanningAStarPlanner.blueprint(require_navigation_source_health=True),
    WavefrontFrontierExplorer.blueprint(require_navigation_source_health=True),
    PatrollingModule.blueprint(),
    MovementManager.blueprint(require_navigation_source_health=True),
).global_config(n_workers=12, robot_model="unitree_go2")

_unitree_go2_mid360_spatial_simulation = (
    autoconnect(
        _unitree_go2_mid360_simulation,
        SpatialMemory.blueprint(new_memory=global_config.new_memory),
        SpatialLandmarkMemoryModule.blueprint(new_memory=global_config.new_memory),
        ObjectTracker2D.blueprint(frame_id="camera_link"),
        BBoxNavigationModule.blueprint(),
        PerceiveLoopSkill.blueprint(),
        SecurityModule.blueprint(camera_info=GO2Connection.camera_info_static),
    )
    .remappings(
        [
            (BBoxNavigationModule, "detection2d", "detection2darray"),
        ]
    )
    .global_config(n_workers=16, robot_model="unitree_go2")
)

unitree_go2_mid360_relocalization_memory_sim = (
    autoconnect(
        _unitree_go2_mid360_simulation,
        _mid360_simulation_relocalization,
        SpatialMemory.blueprint(new_memory=global_config.new_memory),
        SpatialLandmarkMemoryModule.blueprint(new_memory=global_config.new_memory),
        ObjectTracker2D.blueprint(frame_id="camera_link"),
        BBoxNavigationModule.blueprint(),
        PerceiveLoopSkill.blueprint(),
        SecurityModule.blueprint(camera_info=GO2Connection.camera_info_static),
    )
    .remappings(
        [
            (BBoxNavigationModule, "detection2d", "detection2darray"),
        ]
    )
    .global_config(n_workers=16, robot_model="unitree_go2")
)

# CLI global flags are resolved before the registry lazily imports this module.
# Consequently the public blueprint names can select their sensor owner at
# import time: real runs use Livox UDP + Point-LIO, while ``--simulation`` uses
# MuJoCo + the simulation adapter. Explicit ``*-sim`` names remain available
# for backwards compatibility and deterministic test tooling.
unitree_go2_mid360 = autoconnect(
    _unitree_go2_mid360_simulation if global_config.simulation else _unitree_go2_mid360_production
)
unitree_go2_mid360_spatial = autoconnect(
    _unitree_go2_mid360_spatial_simulation
    if global_config.simulation
    else _unitree_go2_mid360_spatial_production
)
unitree_go2_mid360_relocalization_memory = autoconnect(
    unitree_go2_mid360_relocalization_memory_sim
    if global_config.simulation
    else _unitree_go2_mid360_relocalization_memory_production
)
