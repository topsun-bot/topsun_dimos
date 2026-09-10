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

"""LocalPlannerConfig must not pull LFS when given a lazy LfsPath."""

from __future__ import annotations

from dimos.navigation.nav_stack.modules.local_planner.local_planner import LocalPlannerConfig
from dimos.utils.data import LfsPath


def test_local_planner_config_keeps_lfs_path_without_download(mocker) -> None:
    get_data = mocker.patch(
        "dimos.utils.data.get_data",
        side_effect=AssertionError("LFS should stay lazy until start()"),
    )
    paths = LfsPath("unitree_g1_local_planner_precomputed_paths")

    config = LocalPlannerConfig(paths_dir=paths)

    assert config.paths_dir is paths
    get_data.assert_not_called()
