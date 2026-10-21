#!/usr/bin/env python3
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

"""Go2 + NoMaD traversability: subscribe to DimOS camera, publish traversability_map."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import sys
from typing import Any, Literal, cast

from dimos.core.coordination.blueprints import Blueprint, autoconnect
from dimos.core.coordination.module_coordinator import ModuleCoordinator
from dimos.core.global_config import global_config
from dimos.mapping.costmapper import CostMapper
from dimos.mapping.voxels.module import VoxelGridMapper
from dimos.robot.unitree.go2.blueprints.basic.unitree_go2_basic import (
    rerun_config,
    unitree_go2_basic,
)
from dimos.visualization.rerun.bridge import RerunBridgeModule

sys.path.insert(0, str(Path(__file__).parent))

from controller import WaypointFollowerConfig, WaypointFollowerModule
from engine.nomad import NoMaDConfig, NoMaDTrajectoryLocalPlannerModule
from local_navigation_map_module import (
    LocalNavigationMapConfig,
    LocalNavigationMapModule,
)
from record_image_module import DEFAULT_RECORD_IMAGE_DIR, RecordImageModule

NavGo2Mode = Literal["record", "explore", "navigate"]
NAV_GO2_MODES: tuple[NavGo2Mode, ...] = ("record", "explore", "navigate")


@dataclass(frozen=True)
class NavGo2RunConfig:
    """Resolved runtime configuration from CLI / environment."""

    mode: NavGo2Mode
    robot_ip: str | None
    nomad_config: NoMaDConfig
    viewer: str | None
    record_output_dir: str


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Go2 NoMaD traversability: RGB -> diffusion samples -> traversability_map",
    )
    parser.add_argument("--simulation", action="store_true", help="MuJoCo simulation")
    parser.add_argument("--replay", action="store_true", help="Dataset replay mode")
    parser.add_argument(
        "--mode",
        type=str,
        choices=NAV_GO2_MODES,
        default="explore",
        help="Runtime mode: record, explore, or navigate (default: explore)",
    )
    parser.add_argument(
        "--viewer",
        type=str,
        choices=["rerun", "rerun-web", "foxglove", "none"],
        default=None,
        help=(
            "Visualization backend. rerun-web keeps GlobalConfig.viewer=rerun and "
            "sets RerunBridgeModule.rerun_web (default: GlobalConfig / none)"
        ),
    )
    parser.add_argument(
        "--visualnav-root",
        type=str,
        default=None,
        help="Override visualnav-transformer root from config file",
    )
    parser.add_argument(
        "--num-samples",
        type=int,
        default=8,
        help="Diffusion trajectory samples per frame (default: 8)",
    )
    parser.add_argument(
        "--record-output-dir",
        type=str,
        default=str(DEFAULT_RECORD_IMAGE_DIR),
        help="Directory for --mode record image output",
    )
    return parser


def resolve_robot_ip(args: argparse.Namespace) -> str | None:
    """Map --simulation / --replay / ROBOT_IP to GlobalConfig robot_ip."""
    if args.simulation:
        print("Simulation mode\n")
        return "mujoco"
    if args.replay:
        print("Replay mode\n")
        return "replay"
    if "ROBOT_IP" in os.environ:
        robot_ip = os.environ["ROBOT_IP"]
        print(f"Hardware mode: {robot_ip}\n")
        return robot_ip
    print("No ROBOT_IP set; use --replay, --simulation, or export ROBOT_IP\n")
    return None


def parse_nav_go2_config(argv: list[str] | None = None) -> NavGo2RunConfig:
    """Parse CLI and build :class:`NavGo2RunConfig`."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    nomad_config = NoMaDConfig.load_default()
    overrides: dict[str, object] = {"num_samples": args.num_samples}
    if args.mode in ("explore", "navigate"):
        overrides["inference_mode"] = args.mode
    if args.visualnav_root is not None:
        overrides["visualnav_root"] = args.visualnav_root
    nomad_config = nomad_config.model_copy(update=overrides)

    return NavGo2RunConfig(
        mode=cast("NavGo2Mode", args.mode),
        robot_ip=resolve_robot_ip(args),
        nomad_config=nomad_config,
        viewer=args.viewer,
        record_output_dir=args.record_output_dir,
    )


class NavGo2Viewer:
    """Maps this example's ``--viewer`` flag onto GlobalConfig and the Rerun bridge.

    ``GlobalConfig.viewer`` is only ``rerun`` or ``none``. The web viewer is
    ``RerunBridgeModule.rerun_web``, which ``unitree_go2_basic`` does not set.
    Later-wins overlay keeps the Go2 visual overrides already on that atom.
    """

    @staticmethod
    def apply_global(viewer: str) -> None:
        global_config.viewer = "rerun" if viewer == "rerun-web" else viewer

    @staticmethod
    def overlay_blueprint(blueprint: Blueprint, viewer: str | None) -> Blueprint:
        if viewer != "rerun-web":
            return blueprint
        return autoconnect(
            blueprint,
            RerunBridgeModule.blueprint(
                **NavGo2Viewer._composed_rerun_kwargs(blueprint),
                rerun_web=True,
            ),
        )

    @staticmethod
    def _composed_rerun_kwargs(blueprint: Blueprint) -> dict[str, Any]:
        for atom in blueprint.blueprints:
            if atom.module is RerunBridgeModule:
                return dict(atom.kwargs)
        return dict(rerun_config)


def _build_blueprint(config: NavGo2RunConfig) -> Blueprint:
    """Compose stack from ``waypoint_selection`` in nomad config."""
    if config.mode == "record":
        return NavGo2Viewer.overlay_blueprint(
            autoconnect(
                unitree_go2_basic,
                RecordImageModule.blueprint(output_dir=config.record_output_dir),
            ).global_config(
                n_workers=3,
                robot_model="unitree_go2",
                robot_ip=config.robot_ip,
            ),
            config.viewer,
        )

    follower_config = WaypointFollowerConfig.load_default()
    modules: list = [unitree_go2_basic]

    if config.nomad_config.waypoint_selection == "navigation_map":
        local_map_config = LocalNavigationMapConfig.model_validate(
            config.nomad_config.model_dump(
                include={
                    "trajectory_frame_id",
                    "selected_trajectory_index",
                    "navigation_map_gradient_strategy",
                    "navigation_map_robot_increase",
                    "local_map_max_age_s",
                }
            )
        )
        modules.extend(
            [
                VoxelGridMapper.blueprint(),
                CostMapper.blueprint(),
                LocalNavigationMapModule.blueprint(**local_map_config.model_dump()),
            ]
        )

    modules.extend(
        [
            NoMaDTrajectoryLocalPlannerModule.blueprint(**config.nomad_config.model_dump()),
            WaypointFollowerModule.blueprint(**follower_config.model_dump()),
        ]
    )
    n_workers = 6 if config.nomad_config.waypoint_selection == "navigation_map" else 4
    return NavGo2Viewer.overlay_blueprint(
        autoconnect(*modules).global_config(
            n_workers=n_workers,
            robot_model="unitree_go2",
            robot_ip=config.robot_ip,
        ),
        config.viewer,
    )


def run(config: NavGo2RunConfig) -> None:
    if config.viewer:
        NavGo2Viewer.apply_global(config.viewer)

    blueprint = _build_blueprint(config)
    ModuleCoordinator.build(blueprint).loop()


def main() -> None:
    run(parse_nav_go2_config())


if __name__ == "__main__":
    main()
