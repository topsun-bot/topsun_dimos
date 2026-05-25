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
# ruff: noqa: RUF002

"""Go2 物理描述与传感器外参（仿 G1 / Alfred 模式）。"""

from __future__ import annotations

from pathlib import Path

from dimos.msgs.geometry_msgs.Pose import Pose
from dimos.msgs.geometry_msgs.Quaternion import Quaternion
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.robot.config import RobotConfig
from dimos.robot.unitree.g1.config import G1_LOCAL_PLANNER_PRECOMPUTED_PATHS

# TODO(jiangtao, 二期): Go2 与 G1 几何尺寸不同（Go2 矮窄、转向更灵活），严格说应该
# 用 CMU pathGenerator 重新生成 Go2 专属的预生成路径库。首期临时复用 G1 的：实测
# 局部规划"能跑但不优"，避障仍然安全（路径库只决定候选路径形状，碰撞检查仍用真
# vehicle_length / vehicle_width / terrain_map）。详见 jiangtao/plan/plan.md §13 Q6 / R8。
GO2_LOCAL_PLANNER_PRECOMPUTED_PATHS = G1_LOCAL_PLANNER_PRECOMPUTED_PATHS

# Mid-360 在 Go2 上的安装位姿（实测值，2026-05-21；C-3 已核对参考系语义）。
#
# **参考系语义**（已核 dimos/hardware/sensors/lidar/fastlio2/module.py:82 注释）：
#   `internal_odom_offsets["mid360_link"]` 是 FastLio2.mount 的实参，表示
#   **传感器相对地面的初始位姿（init_pose）**——FastLio2 启动时把它转成
#   `[x, y, z, qx, qy, qz, qw]` 喂给 EKF 当先验。
#   因此：z 必须叠加 base_link 离地高度，**不是裸的 base_link → mid-360 偏移**。
#
# 用户提供的相对外参（base_link / IMU 系下，2026-05-21）：
#   T_Dog2lidar = [0.1870, 0.0, 0.0803, 0.0, 0.226, 0.0]   # xyz rpy (m, rad)
#
# Go2 站立时 base_link 离地高度估计值：
_GO2_BASE_LINK_GROUND_HEIGHT = 0.30  # m，Go2 EDU 站立时 base_link（盒中心）≈ 0.30
#
# 最终地面 → mid-360 位姿：
#   x = 0.1870 m              （前方）
#   y = 0.0    m              （居中）
#   z = 0.30 + 0.0803 = 0.3803 m
#   pitch = -0.226 rad ≈ -13°  （实机点云验证：FastLio2 输出需要反向补偿该 Y 轴安装角）
#
# TODO(C-3 实测复核): 在 C 期 bring-up 时让 Go2 站立，比对 FastLio2 输出 odometry
#   的 z 是否符合预期（应在 0.38 m 附近）；若偏差大于 5 cm，重测 base_link 离地
#   高度并更新 _GO2_BASE_LINK_GROUND_HEIGHT。
_MID360_MOUNT = Pose(
    0.1870,
    0.0,
    _GO2_BASE_LINK_GROUND_HEIGHT + 0.0803,
    *Quaternion.from_euler(Vector3(0.0, -0.226, 0.0)),
)

GO2 = RobotConfig(
    name="unitree_go2",
    model_path=Path(__file__).parent / "go2.urdf",
    height_clearance=0.4,  # Go2 站立约 0.40 m
    width_clearance=0.3,  # Go2 身宽约 0.30 m
    internal_odom_offsets={
        "mid360_link": _MID360_MOUNT,
    },
)


__all__ = [
    "GO2",
    "GO2_LOCAL_PLANNER_PRECOMPUTED_PATHS",
]
