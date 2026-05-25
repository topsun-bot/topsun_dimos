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

"""Smoke tests for `unitree_go2_nav_onboard` 蓝图（里程碑 A，merge gate）。

只验证可静态分析的属性：
  - 蓝图 import 不抛异常
  - blueprint 类型正确
  - 关键模块都在装配链里
  - **不**起 worker、**不**接硬件——这些留给里程碑 C / D。
"""

from __future__ import annotations


def test_blueprint_imports() -> None:
    """蓝图模块可 import，对象可访问。"""
    from dimos.robot.unitree.go2.blueprints.navigation.unitree_go2_nav_onboard import (
        unitree_go2_nav_onboard,
    )

    assert unitree_go2_nav_onboard is not None


def test_blueprint_is_blueprint_instance() -> None:
    """对象是 dimos Blueprint 类型。"""
    from dimos.core.coordination.blueprints import Blueprint
    from dimos.robot.unitree.go2.blueprints.navigation.unitree_go2_nav_onboard import (
        unitree_go2_nav_onboard,
    )

    assert isinstance(unitree_go2_nav_onboard, Blueprint)


def test_blueprint_contains_required_modules() -> None:
    """蓝图源码必须显式装配 5 大核心模块（review-v2 §蓝图必须长成什么样）。

    用静态文本检查而不是遍历 Blueprint 内部结构，避免与 dataclass 字段名耦合。
    """
    from pathlib import Path

    from dimos.robot.unitree.go2.blueprints.navigation import unitree_go2_nav_onboard as bp

    src = Path(bp.__file__).read_text(encoding="utf-8")
    for required in (
        "GO2Connection.blueprint(",
        "FastLio2.blueprint(",
        "create_nav_stack(",
        "MovementManager.blueprint(",
        "vis_module(",
    ):
        assert required in src, f"merge gate：蓝图必须显式调用 {required!r}"


def test_blueprint_does_not_use_unitree_go2_basic() -> None:
    """review-v2 R14：必须显式装配，不能内嵌 unitree_go2_basic。

    用 ast 解析 import 和顶层调用——避免误伤 docstring 里的对比说明。
    """
    import ast
    from pathlib import Path

    from dimos.robot.unitree.go2.blueprints.navigation import unitree_go2_nav_onboard as bp

    tree = ast.parse(Path(bp.__file__).read_text(encoding="utf-8"))

    # 1. 不能 import unitree_go2_basic
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert "unitree_go2_basic" not in (node.module or ""), (
                f"review-v2 R14 blocker：禁止 from {node.module} import"
            )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert "unitree_go2_basic" not in alias.name, (
                    f"review-v2 R14 blocker：禁止 import {alias.name}"
                )

    # 2. 不能在 module 顶层调用 unitree_go2_basic 函数
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else (func.attr if isinstance(func, ast.Attribute) else None)
            )
            assert name != "unitree_go2_basic", (
                "review-v2 R14 blocker：禁止调用 unitree_go2_basic(...)"
            )


def test_record_is_false() -> None:
    """review-v2 R5 blocker：aarch64 TLS 风险，merge default 必须 record=False。"""
    from pathlib import Path

    from dimos.robot.unitree.go2.blueprints.navigation import unitree_go2_nav_onboard as bp

    src = Path(bp.__file__).read_text(encoding="utf-8")
    assert "record=False" in src, "merge gate：必须显式设置 record=False"


def test_max_speed_is_merge_default() -> None:
    """review-v2 R15 blocker：max_speed merge default 必须 0.4 m/s。"""
    from pathlib import Path

    from dimos.robot.unitree.go2.blueprints.navigation import unitree_go2_nav_onboard as bp

    src = Path(bp.__file__).read_text(encoding="utf-8")
    assert "max_speed=0.4" in src, "merge gate：max_speed merge default 必须是 0.4"
