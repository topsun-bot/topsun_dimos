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
# ruff: noqa: RUF001, RUF002

"""Remap merge gate（review-v2 R4）：必须使用模块**类型**作 key，禁止字符串。

`Blueprint.remappings()` 签名是
    list[tuple[type[ModuleBase], str, str | type[ModuleBase] | type[Spec]]]
但实战指南早期写过 `("go2-connection", "lidar", "lidar_go2_low")` 这种字符串
形式，autoconnect 会因为找不到匹配模块而 silent-skip 这条 remap。本测试是 PR
合并的硬门槛——任何字符串形式 remap 直接 fail。
"""

from __future__ import annotations


def test_remapping_keys_are_module_types() -> None:
    """remap 列表的第一个元素必须是 type，禁止 str / bytes。"""
    from dimos.robot.unitree.go2.blueprints.navigation.unitree_go2_nav_onboard import (
        unitree_go2_nav_onboard,
    )

    bad: list[tuple[object, str, object]] = []
    for (module, old), new in unitree_go2_nav_onboard.remapping_map.items():
        if not isinstance(module, type):
            bad.append((module, old, new))

    assert not bad, (
        "merge gate (R4)：以下 remap 第一个元素不是模块类型，会被 autoconnect 静默忽略：\n"
        + "\n".join(f"  {m!r} → {old} → {new}" for m, old, new in bad)
    )


def test_required_remappings_present() -> None:
    """four key remap 必须存在（review-v2 §蓝图必须长成什么样）。"""
    from dimos.hardware.sensors.lidar.fastlio2.module import FastLio2
    from dimos.robot.unitree.go2.blueprints.navigation.unitree_go2_nav_onboard import (
        unitree_go2_nav_onboard,
    )
    from dimos.robot.unitree.go2.connection import GO2Connection

    rmap = unitree_go2_nav_onboard.remapping_map
    expected: list[tuple[type, str, str]] = [
        (GO2Connection, "lidar", "lidar_go2_low"),
        (GO2Connection, "odom", "odom_go2_raw"),
        (FastLio2, "lidar", "registered_scan"),
        (FastLio2, "global_map", "global_map_fastlio"),
    ]
    for module, old, new in expected:
        assert (module, old) in rmap, f"merge gate：缺少 remap {module.__name__}.{old}"
        assert rmap[(module, old)] == new, (
            f"merge gate：{module.__name__}.{old} 应 remap 到 {new!r}，"
            f"实际是 {rmap[(module, old)]!r}"
        )


def test_no_string_remappings_in_source() -> None:
    """源码层面禁止字符串形式的 remap（防止后续修改回退）。"""
    from pathlib import Path

    from dimos.robot.unitree.go2.blueprints.navigation import unitree_go2_nav_onboard as bp

    src = Path(bp.__file__).read_text(encoding="utf-8")
    # 只检测 remappings 块内的字符串-tuple 形式
    forbidden_patterns = [
        '("go2-connection"',
        '("fast-lio2"',
        '("movement-manager"',
        '("path-follower"',
    ]
    for pat in forbidden_patterns:
        assert pat not in src, f"merge gate (R4)：检测到字符串形式 remap {pat!r}，请改用模块类型"
