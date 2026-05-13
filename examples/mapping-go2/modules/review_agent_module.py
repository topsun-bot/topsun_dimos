#!/usr/bin/env python3
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

"""审查Agent模块 - 负责系统监控、性能评估和异常检测."""

import time
from typing import Dict, List, Optional

from dimos.agents.annotation import skill
from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.utils.logging import get_logger

logger = get_logger(__name__)


class ReviewAgentModule(Module):
    """审查Agent模块.

    职责：
    - 监控各Agent运行状态
    - 性能指标统计与分析
    - 异常检测与告警
    - 探索任务进度跟踪
    - 生成探索报告
    """

    def __init__(self) -> None:
        super().__init__()

        # 监控数据
        self._start_time = time.time()
        self._last_check_time = time.time()

        # 系统健康状态
        self._system_health = {
            "perception": "unknown",
            "mapping": "unknown",
            "planning": "unknown",
            "overall": "unknown",
        }

        # 性能指标
        self._performance_metrics = {
            "exploration_efficiency": 0.0,  # 探索效率（m²/min）
            "average_speed": 0.0,  # 平均速度（m/s）
            "goal_success_rate": 0.0,  # 目标到达成功率
        }

        # 异常记录
        self._anomalies: List[Dict[str, str]] = []
        self._warnings: List[Dict[str, str]] = []

        # 探索进度
        self._exploration_progress = {
            "coverage_rate": 0.0,
            "explored_area": 0.0,
            "distance_traveled": 0.0,
            "goals_reached": 0,
            "elapsed_time": 0.0,
        }

    @rpc
    def start(self) -> None:
        """启动审查Agent."""
        super().start()
        self._start_time = time.time()
        logger.info("ReviewAgentModule started")

    @rpc
    def stop(self) -> None:
        """停止审查Agent."""
        super().stop()
        logger.info("ReviewAgentModule stopped")

    @skill
    def get_exploration_progress(self) -> str:
        """获取探索任务的进度信息.

        返回当前探索任务的详细进度，包括覆盖率、移动距离、
        已到达目标数等关键指标。

        Returns:
            探索进度的详细描述
        """
        elapsed_time = time.time() - self._start_time
        minutes = int(elapsed_time // 60)
        seconds = int(elapsed_time % 60)

        coverage = self._exploration_progress["coverage_rate"] * 100
        explored = self._exploration_progress["explored_area"]
        distance = self._exploration_progress["distance_traveled"]
        goals = self._exploration_progress["goals_reached"]

        # 计算预计剩余时间
        if coverage > 5:
            estimated_total_time = elapsed_time / (coverage / 100)
            remaining_time = estimated_total_time - elapsed_time
            remaining_minutes = int(remaining_time // 60)
            eta = f"约{remaining_minutes}分钟" if remaining_minutes > 0 else "即将完成"
        else:
            eta = "计算中..."

        # 进度状态
        if coverage < 30:
            status = "初期探索"
        elif coverage < 60:
            status = "中期探索"
        elif coverage < 85:
            status = "后期探索"
        else:
            status = "接近完成"

        return (
            f"探索进度报告：\n"
            f"  - 状态：{status}\n"
            f"  - 覆盖率：{coverage:.1f}%\n"
            f"  - 已探索面积：{explored:.2f} 平方米\n"
            f"  - 移动距离：{distance:.2f} 米\n"
            f"  - 到达目标：{goals} 个\n"
            f"  - 运行时长：{minutes}分{seconds}秒\n"
            f"  - 预计剩余时间：{eta}"
        )

    @skill
    def get_system_health(self) -> str:
        """获取系统健康状态.

        检查所有子系统的健康状况，包括感知、地图、规划等模块。

        Returns:
            系统健康状态报告
        """
        # 更新健康状态
        self._update_system_health()

        perception = self._system_health["perception"]
        mapping = self._system_health["mapping"]
        planning = self._system_health["planning"]
        overall = self._system_health["overall"]

        # 状态图标
        status_icon = {
            "healthy": "✓",
            "warning": "⚠",
            "error": "✗",
            "unknown": "?",
        }

        return (
            f"系统健康状态：\n"
            f"  - 感知系统：{status_icon.get(perception, '?')} {perception}\n"
            f"  - 地图系统：{status_icon.get(mapping, '?')} {mapping}\n"
            f"  - 规划系统：{status_icon.get(planning, '?')} {planning}\n"
            f"  - 整体状态：{status_icon.get(overall, '?')} {overall}\n"
            f"  - 异常数量：{len(self._anomalies)}\n"
            f"  - 警告数量：{len(self._warnings)}"
        )

    @skill
    def generate_report(self) -> str:
        """生成探索任务的完整报告.

        生成包含所有关键指标、性能数据和异常记录的综合报告。

        Returns:
            完整的探索报告
        """
        elapsed_time = time.time() - self._start_time
        minutes = int(elapsed_time // 60)
        seconds = int(elapsed_time % 60)

        coverage = self._exploration_progress["coverage_rate"] * 100
        explored = self._exploration_progress["explored_area"]
        distance = self._exploration_progress["distance_traveled"]
        goals = self._exploration_progress["goals_reached"]

        efficiency = self._performance_metrics["exploration_efficiency"]
        avg_speed = self._performance_metrics["average_speed"]
        success_rate = self._performance_metrics["goal_success_rate"]

        report = (
            "=" * 50 + "\n"
            "探索任务完整报告\n"
            "=" * 50 + "\n\n"
            "【任务概况】\n"
            f"  运行时长：{minutes}分{seconds}秒\n"
            f"  覆盖率：{coverage:.1f}%\n"
            f"  已探索面积：{explored:.2f} 平方米\n\n"
            "【运动统计】\n"
            f"  移动距离：{distance:.2f} 米\n"
            f"  到达目标：{goals} 个\n"
            f"  平均速度：{avg_speed:.2f} m/s\n\n"
            "【性能指标】\n"
            f"  探索效率：{efficiency:.2f} m²/min\n"
            f"  目标成功率：{success_rate:.1f}%\n\n"
            "【系统状态】\n"
            f"  感知系统：{self._system_health['perception']}\n"
            f"  地图系统：{self._system_health['mapping']}\n"
            f"  规划系统：{self._system_health['planning']}\n\n"
        )

        if self._anomalies:
            report += "【异常记录】\n"
            for i, anomaly in enumerate(self._anomalies[-5:], 1):
                report += f"  {i}. [{anomaly['time']}] {anomaly['message']}\n"
            report += "\n"

        if self._warnings:
            report += "【警告信息】\n"
            for i, warning in enumerate(self._warnings[-5:], 1):
                report += f"  {i}. [{warning['time']}] {warning['message']}\n"
            report += "\n"

        report += "=" * 50 + "\n"

        return report

    @skill
    def check_anomalies(self) -> str:
        """检查系统是否存在异常.

        扫描所有子系统，检测潜在的异常情况，如传感器故障、
        性能下降、探索停滞等。

        Returns:
            异常检查结果
        """
        # 执行异常检查
        self._detect_anomalies()

        if not self._anomalies and not self._warnings:
            return "系统运行正常，未检测到异常"

        result = "异常检查结果：\n"

        if self._anomalies:
            result += f"\n发现 {len(self._anomalies)} 个异常：\n"
            for i, anomaly in enumerate(self._anomalies[-3:], 1):
                result += f"  {i}. {anomaly['message']}\n"

        if self._warnings:
            result += f"\n发现 {len(self._warnings)} 个警告：\n"
            for i, warning in enumerate(self._warnings[-3:], 1):
                result += f"  {i}. {warning['message']}\n"

        return result

    def _update_system_health(self) -> None:
        """更新系统健康状态."""
        # 实际实现会检查各个模块的状态
        # 这里使用简化的模拟逻辑

        # 模拟健康检查
        self._system_health["perception"] = "healthy"
        self._system_health["mapping"] = "healthy"
        self._system_health["planning"] = "healthy"

        # 根据子系统状态确定整体状态
        statuses = [
            self._system_health["perception"],
            self._system_health["mapping"],
            self._system_health["planning"],
        ]

        if all(s == "healthy" for s in statuses):
            self._system_health["overall"] = "healthy"
        elif any(s == "error" for s in statuses):
            self._system_health["overall"] = "error"
        elif any(s == "warning" for s in statuses):
            self._system_health["overall"] = "warning"
        else:
            self._system_health["overall"] = "unknown"

    def _detect_anomalies(self) -> None:
        """检测系统异常."""
        # 实际实现会检查：
        # - 传感器数据质量
        # - 探索进度是否停滞
        # - 性能指标是否异常
        # - 资源使用是否过高

        # 这里使用简化的检查逻辑
        current_time = time.strftime("%H:%M:%S")

        # 检查覆盖率进度
        if self._exploration_progress["coverage_rate"] < 0.1:
            elapsed = time.time() - self._start_time
            if elapsed > 300:  # 5分钟后覆盖率还很低
                self._add_warning(
                    current_time,
                    "探索进度缓慢，5分钟后覆盖率仍低于10%"
                )

    def _add_anomaly(self, timestamp: str, message: str) -> None:
        """添加异常记录.

        Args:
            timestamp: 时间戳
            message: 异常消息
        """
        self._anomalies.append({"time": timestamp, "message": message})
        logger.error(f"Anomaly detected: {message}")

    def _add_warning(self, timestamp: str, message: str) -> None:
        """添加警告记录.

        Args:
            timestamp: 时间戳
            message: 警告消息
        """
        self._warnings.append({"time": timestamp, "message": message})
        logger.warning(f"Warning: {message}")

    @rpc
    def update_exploration_progress(self, progress_data: Dict) -> None:
        """更新探索进度数据（由其他模块调用）.

        Args:
            progress_data: 进度数据字典
        """
        self._exploration_progress.update(progress_data)

        # 更新性能指标
        elapsed_time = time.time() - self._start_time
        if elapsed_time > 0:
            explored_area = progress_data.get("explored_area", 0.0)
            self._performance_metrics["exploration_efficiency"] = (
                explored_area / (elapsed_time / 60.0)
            )

            distance = progress_data.get("distance_traveled", 0.0)
            self._performance_metrics["average_speed"] = distance / elapsed_time

    @rpc
    def get_performance_metrics(self) -> Dict[str, float]:
        """获取性能指标.

        Returns:
            性能指标字典
        """
        return self._performance_metrics.copy()
