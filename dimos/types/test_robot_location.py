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

from dimos.types.robot_location import RobotLocation


def test_vector_metadata_round_trip_preserves_map_binding() -> None:
    location = RobotLocation(
        name="office",
        position=(1.0, 2.0, 0.3),
        rotation=(0.0, 0.0, 0.5),
        metadata={
            "map_key": "office-map",
            "pose_map": {
                "position": [0.5, 2.0, 0.3],
                "rotation": [0.0, 0.0, 0.5],
            },
        },
    )

    restored = RobotLocation.from_vector_metadata(location.to_vector_metadata())

    assert restored.position == (1.0, 2.0, 0.3)
    assert restored.metadata == location.metadata
