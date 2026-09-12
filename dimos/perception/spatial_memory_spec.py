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

"""Public Topsun spatial-memory Spec.

Upstream moved the implementation to ``dimos.perception.experimental``. This
module keeps the Topsun import path so semantic nav, greeter, and follow-up
branches (``feat/spatial-memory-export``, ``feat/web-spatial-memory-ui``,
``feat/store_map``, ``feat/open-goal-search``) can rebase without rewriting
callers.

Port plan: implementation + Topsun grafts (``new_memory`` wipe, Chroma
recovery, ``tag_location_with_image``) live in experimental; this file is the
stable public Spec.
"""
