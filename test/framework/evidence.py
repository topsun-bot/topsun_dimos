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

from __future__ import annotations

from framework.models import Evidence


class EvidenceCollector:
    """收集基于证据的审查条目，供 PR 评论使用。"""

    def __init__(self) -> None:
        self._items: list[Evidence] = []

    def add(
        self,
        *,
        file_path: str,
        location: str,
        trigger: str,
        risk_reason: str,
        test_basis: str | None = None,
        verification: str | None = None,
    ) -> Evidence:
        item = Evidence(
            file_path=file_path,
            location=location,
            trigger=trigger,
            risk_reason=risk_reason,
            test_basis=test_basis,
            verification=verification,
        )
        self._items.append(item)
        return item

    @property
    def items(self) -> list[Evidence]:
        return list(self._items)

    def clear(self) -> None:
        self._items.clear()
