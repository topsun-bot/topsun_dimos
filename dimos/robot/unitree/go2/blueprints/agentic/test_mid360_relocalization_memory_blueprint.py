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

from collections import Counter
import subprocess
import sys
from unittest.mock import MagicMock

from dimos.agents.mcp.mcp_client import McpClient
from dimos.agents.mcp.mcp_server import McpServer
from dimos.agents.skills.navigation import NavigationSkillContainer
from dimos.hardware.sensors.lidar.pointlio.module import PointLio
from dimos.hardware.sensors.lidar.virtual_mid360.recorder import Mid360PcapRecorder
from dimos.mapping.costmapper import CostMapper
from dimos.mapping.map_profile import MID360_POINTLIO_SENSOR_PROFILE, preprocess_config_hash
from dimos.mapping.ray_tracing.module import RayTracingVoxelMap
from dimos.mapping.relocalization.module import RelocalizationModule
from dimos.mapping.voxels.module import HealthGatedVoxelGridMapper
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2
from dimos.navigation.frontier_exploration.wavefront_frontier_goal_selector import (
    WavefrontFrontierExplorer,
)
from dimos.navigation.movement_manager.movement_manager import MovementManager
from dimos.navigation.replanning_a_star.module import ReplanningAStarPlanner
from dimos.perception.experimental.spatial_perception import SpatialMemory
from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_mid360_agentic_deepseek import (
    unitree_go2_mid360_agentic_deepseek,
)
from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_mid360_relocalization_memory_agentic_deepseek import (
    unitree_go2_mid360_relocalization_memory_agentic_deepseek,
)
from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_mid360_relocalization_memory_agentic_deepseek_sim import (
    unitree_go2_mid360_relocalization_memory_agentic_deepseek_sim,
    unitree_go2_mid360_relocalization_memory_record_skills_sim,
)
from dimos.robot.unitree.go2.blueprints.smart.unitree_go2_mid360 import (
    _MID360_GLOBAL_MAP_EMIT_EVERY,
    _MID360_SIM_RELOCALIZATION_MIN_LOCAL_POINTS,
    _MID360_VIEWER_MAX_HEIGHT_M,
    _MID360_VIEWER_MIN_HEIGHT_M,
    unitree_go2_mid360,
    unitree_go2_mid360_map_record,
    unitree_go2_mid360_map_record_validation,
    unitree_go2_mid360_navigation_source_validation,
    unitree_go2_mid360_relocalization_validation,
    unitree_go2_mid360_simulation_source_validation,
)
from dimos.robot.unitree.go2.connection import GO2Connection
from dimos.robot.unitree.go2.go2_mid360_recorder import Go2Mid360NavigationRecorder
from dimos.robot.unitree.go2.go2_mid360_static_transforms import (
    ORIN_NAVIGATION_EXTRINSIC_VERSION,
    Go2OrinMid360StaticTf,
)
from dimos.robot.unitree.go2.mid360_map_profile import build_mid360_preprocessing_manifest
from dimos.robot.unitree.go2.mid360_navigation_source import Go2Mid360NavigationSource
from dimos.robot.unitree.go2.mid360_simulation_adapter import Go2Mid360SimulationAdapter
from dimos.spec.utils import spec_annotation_compliance, spec_structural_compliance
from dimos.types.navigation_source_spec import NavigationSourceStateSpec
from dimos.visualization.rerun.bridge import RerunBridgeModule


def _atom(module: type):  # type: ignore[no-untyped-def]
    return next(
        atom
        for atom in unitree_go2_mid360_relocalization_memory_agentic_deepseek.active_blueprints
        if atom.module is module
    )


def _sim_atom(module: type):  # type: ignore[no-untyped-def]
    return next(
        atom
        for atom in unitree_go2_mid360_relocalization_memory_agentic_deepseek_sim.active_blueprints
        if atom.module is module
    )


def test_mid360_blueprint_has_single_navigation_source_owners() -> None:
    counts = Counter(
        atom.module
        for atom in unitree_go2_mid360_relocalization_memory_agentic_deepseek.active_blueprints
    )
    for module in (
        GO2Connection,
        PointLio,
        Go2Mid360NavigationSource,
        Go2OrinMid360StaticTf,
        HealthGatedVoxelGridMapper,
        CostMapper,
        MovementManager,
        RelocalizationModule,
        SpatialMemory,
    ):
        assert counts[module] == 1
    assert counts[RayTracingVoxelMap] == 0


def test_mid360_ordinary_agentic_blueprint_has_spatial_memory_without_relocalization() -> None:
    counts = Counter(atom.module for atom in unitree_go2_mid360_agentic_deepseek.active_blueprints)

    assert counts[GO2Connection] == 1
    assert counts[PointLio] == 1
    assert counts[Go2Mid360SimulationAdapter] == 0
    assert counts[Go2Mid360NavigationSource] == 1
    assert counts[SpatialMemory] == 1
    assert counts[RelocalizationModule] == 0
    assert counts[McpServer] == 1
    assert counts[McpClient] == 1
    assert counts[NavigationSkillContainer] == 1


def test_mid360_plain_navigation_production_uses_pointlio_without_memory() -> None:
    counts = Counter(atom.module for atom in unitree_go2_mid360.active_blueprints)

    assert counts[GO2Connection] == 1
    assert counts[PointLio] == 1
    assert counts[Go2Mid360SimulationAdapter] == 0
    assert counts[Go2Mid360NavigationSource] == 1
    assert counts[SpatialMemory] == 0
    assert counts[RelocalizationModule] == 0


def test_public_mid360_names_select_simulation_source_with_global_flag() -> None:
    script = r"""
from collections import Counter

from dimos.core.global_config import global_config

global_config.update(simulation="mujoco")

from dimos.agents.mcp.mcp_client import McpClient
from dimos.agents.mcp.mcp_server import McpServer
from dimos.mapping.relocalization.module import RelocalizationModule
from dimos.perception.experimental.spatial_perception import SpatialMemory
from dimos.robot.get_all_blueprints import get_blueprint_by_name
from dimos.robot.unitree.go2.connection import GO2Connection
from dimos.robot.unitree.go2.mid360_navigation_source import Go2Mid360NavigationSource
from dimos.robot.unitree.go2.mid360_simulation_adapter import Go2Mid360SimulationAdapter
from dimos.hardware.sensors.lidar.pointlio.module import PointLio

expected = {
    "unitree-go2-mid360": (0, 0),
    "unitree-go2-mid360-agentic-deepseek": (1, 0),
    "unitree-go2-mid360-relocalization-memory-agentic-deepseek": (1, 1),
}
for name, (spatial_count, relocalization_count) in expected.items():
    blueprint = get_blueprint_by_name(name)
    counts = Counter(atom.module for atom in blueprint.active_blueprints)
    assert counts[GO2Connection] == 1, name
    assert counts[Go2Mid360SimulationAdapter] == 1, name
    assert counts[Go2Mid360NavigationSource] == 1, name
    assert counts[PointLio] == 0, name
    assert counts[SpatialMemory] == spatial_count, name
    assert counts[RelocalizationModule] == relocalization_count, name
    assert counts[McpServer] == spatial_count, name
    assert counts[McpClient] == spatial_count, name
    connection = next(
        atom for atom in blueprint.active_blueprints if atom.module is GO2Connection
    )
    assert connection.kwargs["lidar"] is True, name
    assert connection.kwargs["odom"] is True, name
    assert connection.kwargs["camera"] is True, name
    assert connection.kwargs["publish_mount_tf"] is False, name
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_mid360_sim_blueprint_reuses_production_source_without_native_udp_owner() -> None:
    counts = Counter(
        atom.module
        for atom in unitree_go2_mid360_relocalization_memory_agentic_deepseek_sim.active_blueprints
    )

    assert counts[GO2Connection] == 1
    assert counts[Go2Mid360SimulationAdapter] == 1
    assert counts[Go2Mid360NavigationSource] == 1
    assert counts[PointLio] == 0
    assert counts[HealthGatedVoxelGridMapper] == 1
    assert counts[RelocalizationModule] == 1
    assert counts[SpatialMemory] == 1
    assert counts[MovementManager] == 1

    connection = _sim_atom(GO2Connection)
    assert connection.kwargs["lidar"] is True
    assert connection.kwargs["odom"] is True
    assert connection.kwargs["camera"] is True
    assert connection.kwargs["publish_mount_tf"] is False
    assert (
        unitree_go2_mid360_relocalization_memory_agentic_deepseek_sim.remapping_map[
            (connection.name, "lidar")
        ]
        == "simulation_world_lidar"
    )
    assert (
        unitree_go2_mid360_relocalization_memory_agentic_deepseek_sim.remapping_map[
            (connection.name, "odom")
        ]
        == "simulation_base_odom"
    )

    mapper = _sim_atom(HealthGatedVoxelGridMapper)
    assert mapper.kwargs["device"] == "CPU:0"
    assert mapper.kwargs["voxel_size"] == 0.05
    assert mapper.kwargs["emit_every"] == _MID360_GLOBAL_MAP_EMIT_EVERY

    relocalization = _sim_atom(RelocalizationModule)
    assert (
        relocalization.kwargs["min_local_points"]
        == _MID360_SIM_RELOCALIZATION_MIN_LOCAL_POINTS
        == 5_000
    )


def test_mid360_sim_record_blueprint_records_exact_navigation_contract() -> None:
    counts = Counter(
        atom.module
        for atom in unitree_go2_mid360_relocalization_memory_record_skills_sim.active_blueprints
    )

    assert counts[GO2Connection] == 1
    assert counts[Go2Mid360SimulationAdapter] == 1
    assert counts[Go2Mid360NavigationSource] == 1
    assert counts[Go2Mid360NavigationRecorder] == 1
    assert counts[PointLio] == 0
    assert counts[Mid360PcapRecorder] == 0
    assert counts[MovementManager] == 1
    assert counts[SpatialMemory] == 1


def test_navigation_skill_binds_optional_external_source_health_spec() -> None:
    navigation_skill = _atom(NavigationSkillContainer)
    source_refs = [ref for ref in navigation_skill.module_refs if ref.name == "_navigation_source"]

    assert len(source_refs) == 1
    assert source_refs[0].spec is NavigationSourceStateSpec
    assert source_refs[0].optional is True
    assert spec_structural_compliance(Go2Mid360NavigationSource, NavigationSourceStateSpec)
    assert spec_annotation_compliance(Go2Mid360NavigationSource, NavigationSourceStateSpec)


def test_mid360_blueprint_disables_builtin_go2_sensor_ownership() -> None:
    connection = _atom(GO2Connection)
    pointlio = _atom(PointLio)
    movement = _atom(MovementManager)
    planner = _atom(ReplanningAStarPlanner)
    explorer = _atom(WavefrontFrontierExplorer)
    mapper = _atom(HealthGatedVoxelGridMapper)
    costmapper = _atom(CostMapper)

    assert connection.kwargs["lidar"] is False
    assert connection.kwargs["odom"] is False
    assert connection.kwargs["camera"] is True
    assert connection.kwargs["publish_mount_tf"] is False
    assert pointlio.kwargs["publish_tf"] is False
    assert pointlio.kwargs["sensor_frame_id"] == "mid360_imu_link"
    assert pointlio.kwargs["device_model"] == "mid360s"
    assert movement.kwargs["require_navigation_source_health"] is True
    assert planner.kwargs["require_navigation_source_health"] is True
    assert explorer.kwargs["require_navigation_source_health"] is True
    assert mapper.kwargs["voxel_size"] == 0.05
    assert mapper.kwargs["emit_every"] == _MID360_GLOBAL_MAP_EMIT_EVERY
    assert _MID360_GLOBAL_MAP_EMIT_EVERY == 10
    assert costmapper.kwargs["require_navigation_source_health"] is True
    assert "algo" not in costmapper.kwargs
    assert "config" not in costmapper.kwargs

    relocalization = _atom(RelocalizationModule)
    assert relocalization.kwargs["require_map_profile"] is True
    assert relocalization.kwargs["expected_sensor_profile"] == MID360_POINTLIO_SENSOR_PROFILE
    assert relocalization.kwargs["expected_preprocess_config_hash"] == preprocess_config_hash(
        build_mid360_preprocessing_manifest()
    )
    assert relocalization.kwargs["expected_extrinsic_version"] == ORIN_NAVIGATION_EXTRINSIC_VERSION
    assert relocalization.kwargs["require_navigation_source_health"] is True

    # Disabling Go2 lidar/odom must leave the camera and command channel on the
    # same connection. Neither stream is remapped away from the navigation graph.
    stream_directions = {(stream.name, stream.direction) for stream in connection.streams}
    assert ("color_image", "out") in stream_directions
    assert ("cmd_vel", "in") in stream_directions
    remappings = unitree_go2_mid360_relocalization_memory_agentic_deepseek.remapping_map
    assert (connection.name, "color_image") not in remappings
    assert (connection.name, "cmd_vel") not in remappings


def test_mid360_viewer_suppresses_duplicate_internal_pointcloud_streams() -> None:
    bridge = _atom(RerunBridgeModule)

    overrides = bridge.kwargs["visual_override"]
    for entity in (
        "world/pointlio_lidar",
        "world/pointlio_odometry",
        "world/mapping_lidar",
        "world/mapping_odometry",
        "world/local_map",
    ):
        assert entity in overrides
        assert overrides[entity] is None

    max_hz = bridge.kwargs["max_hz"]
    assert max_hz["world/lidar"] == 1.0
    assert max_hz["world/global_map"] == 0.5
    assert max_hz["world/global_costmap"] == 1.0


def test_mid360_viewer_clips_global_maps_without_changing_navigation_streams() -> None:
    bridge = _atom(RerunBridgeModule)
    grid = MagicMock()
    expected = object()
    grid.to_rerun.return_value = expected

    for entity in ("world/global_map", "world/merged_map"):
        assert bridge.kwargs["visual_override"][entity](grid) is expected
        grid.to_rerun.assert_called_once_with(
            bottom_cutoff=_MID360_VIEWER_MIN_HEIGHT_M,
            top_cutoff=_MID360_VIEWER_MAX_HEIGHT_M,
        )
        grid.reset_mock()

    assert _MID360_VIEWER_MIN_HEIGHT_M == -0.05
    assert _MID360_VIEWER_MAX_HEIGHT_M == 1.5


def test_mid360_base_pose_is_the_planners_single_connected_pose_contract() -> None:
    source = _atom(Go2Mid360NavigationSource)
    planner = _atom(ReplanningAStarPlanner)

    source_poses = [
        stream
        for stream in source.streams
        if stream.direction == "out" and stream.type is PoseStamped
    ]
    planner_poses = [
        stream
        for stream in planner.streams
        if stream.direction == "in" and stream.type is PoseStamped and stream.name == "odom"
    ]

    assert [stream.name for stream in source_poses] == ["odom"]
    assert [stream.name for stream in planner_poses] == ["odom"]
    assert (
        Go2Mid360NavigationSource.name,
        "navigation_odometry",
    ) not in unitree_go2_mid360_relocalization_memory_agentic_deepseek.remapping_map


def test_voxel_map_consumes_only_health_gated_world_cloud() -> None:
    source = _atom(Go2Mid360NavigationSource)
    mapper = _atom(HealthGatedVoxelGridMapper)
    source_outputs = {
        (stream.name, stream.type) for stream in source.streams if stream.direction == "out"
    }

    assert ("lidar", PointCloud2) in source_outputs
    assert (mapper.name, "lidar") not in (
        unitree_go2_mid360_relocalization_memory_agentic_deepseek.remapping_map
    )
    assert (
        unitree_go2_mid360_relocalization_memory_agentic_deepseek.remapping_map[
            (PointLio.name, "lidar")
        ]
        == "pointlio_lidar"
    )
    assert (
        unitree_go2_mid360_relocalization_memory_agentic_deepseek.remapping_map[
            (PointLio.name, "odometry")
        ]
        == "pointlio_odometry"
    )


def test_mid360_validation_blueprint_cannot_publish_robot_motion() -> None:
    modules = {
        atom.module for atom in unitree_go2_mid360_navigation_source_validation.active_blueprints
    }

    assert PointLio in modules
    assert Go2Mid360NavigationSource in modules
    assert Go2OrinMid360StaticTf in modules
    assert HealthGatedVoxelGridMapper in modules
    assert CostMapper in modules
    assert GO2Connection not in modules
    assert MovementManager not in modules


def test_mid360_relocalization_validation_cannot_publish_robot_motion() -> None:
    modules = {
        atom.module for atom in unitree_go2_mid360_relocalization_validation.active_blueprints
    }

    assert RelocalizationModule in modules
    assert GO2Connection not in modules
    assert ReplanningAStarPlanner not in modules
    assert MovementManager not in modules


def test_mid360_simulation_source_validation_has_no_autonomous_command_owner() -> None:
    modules = {
        atom.module for atom in unitree_go2_mid360_simulation_source_validation.active_blueprints
    }

    assert GO2Connection in modules
    assert Go2Mid360SimulationAdapter in modules
    assert Go2Mid360NavigationSource in modules
    assert PointLio not in modules
    assert ReplanningAStarPlanner not in modules
    assert MovementManager not in modules


def test_mid360_map_record_uses_adapted_source_without_duplicate_livox_owner() -> None:
    counts = Counter(atom.module for atom in unitree_go2_mid360_map_record.active_blueprints)

    assert counts[PointLio] == 1
    assert counts[Go2Mid360NavigationSource] == 1
    assert counts[Go2Mid360NavigationRecorder] == 1
    assert counts[Mid360PcapRecorder] == 1
    assert counts[GO2Connection] == 1
    assert counts[MovementManager] == 1


def test_mid360_map_record_validation_cannot_publish_robot_motion() -> None:
    modules = {atom.module for atom in unitree_go2_mid360_map_record_validation.active_blueprints}

    assert Go2Mid360NavigationRecorder in modules
    assert Mid360PcapRecorder in modules
    assert GO2Connection not in modules
    assert ReplanningAStarPlanner not in modules
    assert MovementManager not in modules
