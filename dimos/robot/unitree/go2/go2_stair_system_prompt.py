# Copyright 2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Extra system-prompt section for Go2 stair simulation / hardware climbs."""

GO2_STAIR_SKILLS_APPENDIX = """
# STAIRS (Go2 locomotion skills)
- To climb the straight stairs in simulation: call ``climb_stairs`` (enables WalkStair + sets goal at top tread).
- Before climbing, you may call ``free_walk`` if velocity commands are ignored.
- Use ``walk_stair(False)`` after reaching the top or to abort sport stair mode.
- Use ``move`` only for small adjustments; full ascent should use ``climb_stairs`` or map navigation on the costmap centerline.
- If navigation logs "No path found", wait for lidar mapping and "Stair corridor armed", or click the goal on the left costmap panel (not the Rerun 3D view).
- Semantic navigation (``navigate_with_text``, ``tag_location``) is not in this blueprint; use ``climb_stairs``, ``relative_move``, or costmap clicks.
- Do not call flips/jumps before stair climbs; use ``balance_stand`` / ``free_walk`` if the robot is not standing.
"""
