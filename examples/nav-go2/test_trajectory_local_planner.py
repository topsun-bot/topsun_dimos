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

from pathlib import Path
import sys
import threading

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent))

from engine.nomad.config import NoMaDConfig
from engine.nomad.local_planner_module import NoMaDTrajectoryLocalPlannerModule
from go2_nomad_nav import NAV_GO2_MODES, _build_blueprint, parse_nav_go2_config
from record_image_module import RecordImageConfig, RecordImageModule, save_record_image
from trajectory_inference import TrajectoryInferenceResult
from trajectory_local_planner_module import (
    TrajectoryLocalPlannerConfig,
    TrajectoryLocalPlannerModule,
)

from dimos.msgs.sensor_msgs.Image import Image, ImageFormat


class DummyTrajectoryEngine:
    ready = True

    def initialize(self) -> None:
        pass

    def push_observation(self, _pil_image) -> None:  # type: ignore[no-untyped-def]
        pass

    def context_ready(self) -> bool:
        return True

    def infer_exploration(self) -> TrajectoryInferenceResult:
        return TrajectoryInferenceResult(
            trajectories=np.array([[[0.5, 0.0], [1.0, 0.0]]], dtype=np.float64),
            chosen_waypoint=np.array([1.0, 0.0], dtype=np.float64),
        )


def test_nomad_config_loads_waypoint_selection() -> None:
    cfg = NoMaDConfig.load_default()
    assert cfg.waypoint_selection in ("navigation_map", "multi_waypoints")


def test_planner_config_accepts_multi_waypoints_fields() -> None:
    cfg = TrajectoryLocalPlannerConfig(
        waypoint_selection="multi_waypoints",
        collision_thresh=0.3,
        max_clusters=3,
    )
    assert cfg.waypoint_selection == "multi_waypoints"
    assert cfg.collision_thresh == 0.3


def test_nav_go2_mode_cli_defaults_to_explore() -> None:
    cfg = parse_nav_go2_config([])
    assert cfg.mode == "explore"
    assert cfg.nomad_config.inference_mode == "explore"


def test_nav_go2_mode_cli_accepts_supported_modes() -> None:
    for mode in NAV_GO2_MODES:
        cfg = parse_nav_go2_config(["--mode", mode])
        assert cfg.mode == mode


def test_nav_go2_navigate_mode_sets_nomad_navigation_inference() -> None:
    cfg = parse_nav_go2_config(["--mode", "navigate"])
    assert cfg.nomad_config.inference_mode == "navigate"


def test_nomad_config_resolves_relative_goal_image_path(tmp_path: Path) -> None:
    goal_path = tmp_path / "goal.png"
    goal_path.write_bytes(b"placeholder")
    config_path = tmp_path / "nomad_nav.yaml"
    config_path.write_text(
        "\n".join(
            [
                "goal_image_path: goal.png",
                "checkpoint_path: checkpoint.pth",
                "model_config_path: model.yaml",
            ]
        )
    )

    cfg = NoMaDConfig.from_yaml(config_path)

    assert cfg.goal_image_path == str(goal_path)
    assert cfg.resolved_goal_image() == goal_path


def test_record_mode_blueprint_uses_image_recorder(tmp_path: Path) -> None:
    cfg = parse_nav_go2_config(["--mode", "record", "--record-output-dir", str(tmp_path)])
    blueprint = _build_blueprint(cfg)
    modules = {atom.module for atom in blueprint.blueprints}
    assert RecordImageModule in modules
    assert NoMaDTrajectoryLocalPlannerModule not in modules
    assert blueprint.global_config_overrides["robot_ip"] == cfg.robot_ip


def test_save_record_image_converts_and_writes_png(tmp_path: Path) -> None:
    image = Image.from_numpy(
        np.array([[[0, 0, 255]]], dtype=np.uint8),
        format=ImageFormat.BGR,
        ts=1.25,
    )
    output_path = save_record_image(image, tmp_path, saved_count=0)
    assert output_path.name == "1250000000_000000.png"
    assert output_path.is_file()


def test_record_image_module_saves_at_one_second_interval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = RecordImageModule.__new__(RecordImageModule)
    module.config = RecordImageConfig(output_dir=str(tmp_path), interval_s=1.0)
    module._last_saved_monotonic = 0.0
    module._saved_count = 0
    image = Image.from_numpy(np.zeros((1, 1, 3), dtype=np.uint8), format=ImageFormat.RGB, ts=1.0)

    times = iter([1.0, 1.5, 2.1])
    monkeypatch.setattr("record_image_module.time.monotonic", lambda: next(times))

    module._on_image(image)
    module._on_image(image)
    module._on_image(image)

    assert sorted(path.name for path in tmp_path.glob("*.png")) == [
        "1000000000_000000.png",
        "1000000000_000001.png",
    ]


def test_lidar_freshness_uses_selection_timestamp() -> None:
    module = TrajectoryLocalPlannerModule.__new__(TrajectoryLocalPlannerModule)
    module.config = TrajectoryLocalPlannerConfig(
        waypoint_selection="multi_waypoints",
        lidar_max_age_s=0.5,
    )
    module._lidar_lock = threading.Lock()
    module._latest_lidar_xy = np.array([[1.0, 0.0]], dtype=np.float64)
    module._latest_lidar_frame_id = "base_link"
    module._latest_lidar_time_s = 100.0
    module._frame_count = 1

    lidar_xy, _ = module._lidar_for_selection(time_point=100.25)
    assert lidar_xy.tolist() == [[1.0, 0.0]]

    stale_xy, _ = module._lidar_for_selection(time_point=101.0)
    assert stale_xy.shape == (0, 2)


def test_selection_failure_still_throttles_inference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = TrajectoryLocalPlannerModule.__new__(TrajectoryLocalPlannerModule)
    module.config = TrajectoryLocalPlannerConfig(min_inference_interval_s=1.0)
    module._engine = DummyTrajectoryEngine()
    module._frame_count = 0
    module._last_infer_monotonic = 0.0
    module.traversability_map = type("Publisher", (), {"publish": lambda self, value: None})()

    def fail_selection(_trajectories, _time_point):  # type: ignore[no-untyped-def]
        raise RuntimeError("selection unavailable")

    module._select_best_trajectory = fail_selection
    monkeypatch.setattr("trajectory_local_planner_module.time.monotonic", lambda: 10.0)

    image = Image.from_numpy(np.zeros((4, 4, 3), dtype=np.uint8), format=ImageFormat.RGB, ts=50.0)
    module._on_image(image)

    assert module._last_infer_monotonic == 10.0
