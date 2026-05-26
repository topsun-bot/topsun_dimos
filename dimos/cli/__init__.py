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
# ruff: noqa: RUF002
"""DimOS 集成 CLI 子命令包。

此包托管 *跨模块* 的 CLI 子命令——它们把多个 unit 串成端到端流程，
而不是属于某个单独的机器人或子系统。其它已有 CLI 仍然挂在
``dimos/robot/cli/dimos.py`` 上；这里新增的命令会被 ``dimos.robot.cli.dimos``
通过 typer ``add_typer`` / ``command`` 注册到顶层 ``dimos`` 命令树。
"""
