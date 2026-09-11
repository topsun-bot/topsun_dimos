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

"""Public Topsun SpatialMemory module.

Upstream relocated the implementation under ``perception.experimental`` and
deleted this path. Restoring the import keeps semantic-nav / Go2 spatial /
G1 perceptive blueprints on the Topsun API.

Port plan: do not treat experimental as the only caller-facing path. Product
code should import ``dimos.perception.spatial_perception``. The experimental
module holds the merged implementation (upstream move + Topsun wipe/recovery
RPCs). Unify after rebasing ``feat/spatial-memory-export`` and related
branches.
"""

from dimos.perception.experimental.spatial_perception import (
    SpatialMemory as SpatialMemory,
)
