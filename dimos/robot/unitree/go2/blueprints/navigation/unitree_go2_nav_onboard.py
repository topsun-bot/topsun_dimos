#!/usr/bin/env python3
# ruff: noqa: RUF002
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

"""Go2 + Mid-360 onboard 导航蓝图（首期 merge-ready 版本）。

详细设计见 jiangtao/plan/plan.md §5.3。本蓝图严格按 review-v2 R14 / R15 要求：

1. **显式装配**：直接组合 GO2Connection + FastLio2 + create_nav_stack +
   MovementManager + vis_module，**不**基于 unitree_go2_basic 叠加；reviewer
   一眼能看出装配链。
2. **merge default 速度 0.4 m/s**：实验目标 0.6 m/s 写在文档但不进 default。
3. **record=False**：aarch64 NavRecord TLS allocation failure 风险未验证。
4. **G1 路径库临时复用**：在 GO2_LOCAL_PLANNER_PRECOMPUTED_PATHS 注释里也已 TODO，
   PR 描述的"风险"段也会写明。

部署形态：拓展坞（Jetson Orin NX 16GB）本机运行；笔记本只做 rerun-web 浏览。

关键 ENV 变量：
  LIDAR_HOST_IP   拓展坞 LAN 网卡 IP，默认 192.168.123.18
  LIDAR_IP        Mid-360 IP，默认 192.168.123.20
  ROBOT_IP        Go2 IP；不设则走 jtlinux 81e9f144 的自动发现，扫到 192.168.123.161

外参的两份职责（C-9 评估结论，**不要混淆**）：
  GO2.internal_odom_offsets["mid360_link"]     —— 雷达整体在 Go2 上的位姿
                                                   （地面 → mid-360，FastLio2.mount 实参）
  config/mid360.yaml: mapping.extrinsic_T / R  —— 雷达内部 IMU 在雷达自身坐标系
                                                   （Livox 出厂标定，与 Go2 安装无关，**不要改**）
"""

from __future__ import annotations

import os

from dimos.core.coordination.blueprints import autoconnect
from dimos.core.global_config import global_config
from dimos.hardware.sensors.lidar.fastlio2.module import FastLio2
from dimos.navigation.movement_manager.movement_manager import MovementManager
from dimos.navigation.nav_stack.main import create_nav_stack, nav_stack_rerun_config
from dimos.protocol.service.system_configurator.clock_sync import ClockSyncConfigurator
from dimos.robot.unitree.go2.config import GO2, GO2_LOCAL_PLANNER_PRECOMPUTED_PATHS
from dimos.robot.unitree.go2.connection import GO2Connection
from dimos.visualization.vis_module import vis_module

unitree_go2_nav_onboard = (
    autoconnect(
        # 显式装配：reviewer 应能在这里一眼看到 5 大模块 ----------------------
        GO2Connection.blueprint(),
        FastLio2.blueprint(
            host_ip=os.getenv("LIDAR_HOST_IP", "192.168.123.18"),
            lidar_ip=os.getenv("LIDAR_IP", "192.168.123.20"),
            mount=GO2.internal_odom_offsets["mid360_link"],
            map_freq=1.0,
            config="mid360.yaml",
        ),
        create_nav_stack(
            planner="simple",
            record=False,  # blocker R5：aarch64 TLS allocation failure 风险
            vehicle_height=GO2.height_clearance,  # 0.4 m
            max_speed=0.4,  # blocker R15：merge default 偏保守；实验目标可放到 0.6
            terrain_analysis={
                "obstacle_height_threshold": 0.08,
                "ground_height_threshold": 0.05,
                "sensor_range": 30,  # mid-360 标称 40 m，留余量
            },
            local_planner={
                "paths_dir": str(GO2_LOCAL_PLANNER_PRECOMPUTED_PATHS),
                "publish_free_paths": False,
                "vehicle_length": 0.7,  # Go2 实测
                "vehicle_width": 0.3,
            },
            simple_planner={
                "cell_size": 0.15,
                "obstacle_height_threshold": 0.08,
                "inflation_radius": 0.25,
                "lookahead_distance": 1.5,
                "replan_rate": 5.0,
                "replan_cooldown": 2.0,
            },
        ),
        MovementManager.blueprint(),
        vis_module(
            viewer_backend=global_config.viewer,
            rerun_config=nav_stack_rerun_config(
                {"memory_limit": "1GB"},
                vis_throttle=0.5,
            ),
        ),
    )
    .remappings(
        [
            # Go2 自带的低分辨率 4D LiDAR 旁路改名（避免和 mid-360 主链路混淆）
            (GO2Connection, "lidar", "lidar_go2_low"),
            # Go2 WebRTC odom（PoseStamped）保留可调试，与 FastLio2 odometry（Odometry
            # 类型不同，本不冲突，但同名易引起 reviewer 疑惑）改名
            (GO2Connection, "odom", "odom_go2_raw"),
            # FastLio2 主输出对接 nav_stack：lidar→registered_scan、global_map 改名避让
            (FastLio2, "lidar", "registered_scan"),
            (FastLio2, "global_map", "global_map_fastlio"),
            # SimplePlanner 接管 way_point；断开 MovementManager 的 click 中继
            (MovementManager, "way_point", "_mgr_way_point_unused"),
        ]
    )
    .configurators(ClockSyncConfigurator())  # Go2 时钟与拓展坞同步（沿用 unitree_go2_basic 思路）
    .global_config(n_workers=12, robot_model="unitree_go2")
)


__all__ = ["unitree_go2_nav_onboard"]
