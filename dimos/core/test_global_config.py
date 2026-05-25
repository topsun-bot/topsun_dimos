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

"""Tests for GlobalConfig security defaults and simulation viewer defaults."""

import pytest


class TestGlobalConfigSecurityDefaults:
    """Network services must bind to localhost by default (not 0.0.0.0)."""

    def test_listen_host_defaults_to_localhost(self) -> None:
        from dimos.core.global_config import GlobalConfig

        config = GlobalConfig()
        assert config.listen_host == "127.0.0.1", (
            f"listen_host must default to 127.0.0.1, got {config.listen_host}"
        )


class TestSimulationViewerDefaults:
    def test_mujoco_sim_without_explicit_viewer_defaults_to_rerun_native(self) -> None:
        from dimos.core.global_config import GlobalConfig, apply_simulation_viewer_defaults

        overrides = apply_simulation_viewer_defaults(
            {"simulation": "mujoco"},
            current=GlobalConfig(),
            argv=["dimos", "--simulation", "mujoco", "run", "unitree-go2"],
        )
        assert overrides["viewer"] == "rerun"
        assert overrides["rerun_web"] is False
        assert overrides["rerun_open"] == "native"

    def test_explicit_viewer_none_is_respected(self) -> None:
        from dimos.core.global_config import GlobalConfig, apply_simulation_viewer_defaults

        overrides = apply_simulation_viewer_defaults(
            {"simulation": "mujoco", "viewer": "none"},
            current=GlobalConfig(),
            argv=[
                "dimos",
                "--simulation",
                "mujoco",
                "--viewer",
                "none",
                "run",
                "unitree-go2",
            ],
        )
        assert overrides["viewer"] == "none"

    def test_explicit_viewer_rerun_web_is_respected(self) -> None:
        from dimos.core.global_config import GlobalConfig, apply_simulation_viewer_defaults

        overrides = apply_simulation_viewer_defaults(
            {"simulation": "mujoco", "viewer": "rerun-web"},
            current=GlobalConfig(),
            argv=[
                "dimos",
                "--simulation",
                "mujoco",
                "--viewer",
                "rerun-web",
                "run",
                "unitree-go2",
            ],
        )
        assert overrides["viewer"] == "rerun-web"
        assert "rerun_web" not in overrides

    def test_no_simulation_leaves_viewer_unset(self) -> None:
        from dimos.core.global_config import GlobalConfig, apply_simulation_viewer_defaults

        overrides = apply_simulation_viewer_defaults(
            {},
            current=GlobalConfig(),
            argv=["dimos", "run", "unitree-go2"],
        )
        assert "viewer" not in overrides

    @pytest.mark.parametrize("simulation", ["mujoco", "dimsim"])
    def test_any_simulator_enables_rerun_native_viewer_default(self, simulation: str) -> None:
        from dimos.core.global_config import GlobalConfig, apply_simulation_viewer_defaults

        overrides = apply_simulation_viewer_defaults(
            {"simulation": simulation},
            current=GlobalConfig(),
            argv=["dimos", "--simulation", simulation, "run", "unitree-go2"],
        )
        assert overrides["viewer"] == "rerun"
        assert overrides["rerun_web"] is False

    def test_simulation_does_not_override_explicit_rerun_web_flag(self) -> None:
        from dimos.core.global_config import GlobalConfig, apply_simulation_viewer_defaults

        overrides = apply_simulation_viewer_defaults(
            {"simulation": "mujoco", "rerun_web": True},
            current=GlobalConfig(rerun_web=False),
            argv=["dimos", "--simulation", "mujoco", "--rerun-web", "run", "unitree-go2"],
        )
        assert overrides["viewer"] == "rerun"
        assert overrides["rerun_web"] is True


class TestNormalizeGlobalConfigArgv:
    def test_stuck_obstacle_mvp_height_cm_flag_splits_value(self) -> None:
        from dimos.core.global_config import normalize_global_config_argv

        argv = [
            "dimos",
            "--simulation",
            "mujoco",
            "--obstacle-crossing-mvp",
            "--obstacle-crossing-mvp-height-cm8",
            "run",
            "unitree-go2-agentic",
        ]
        assert normalize_global_config_argv(argv) == [
            "dimos",
            "--simulation",
            "mujoco",
            "--obstacle-crossing-mvp",
            "--obstacle-crossing-mvp-height-cm",
            "8",
            "run",
            "unitree-go2-agentic",
        ]
