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

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.animation as animation
import matplotlib.pyplot as plt

# 初始化画布
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))
fig.suptitle("Loop Closure Shock: No TF Layer vs With TF Layer", fontsize=14)


def update(frame):
    ax1.clear()
    ax2.clear()

    # 设置坐标轴范围和标签
    for ax in [ax1, ax2]:
        ax.set_xlim(-1.5, 1.5)
        ax.set_ylim(-0.5, 2.5)
        ax.grid(True, linestyle="--", alpha=0.6)
        ax.axhline(0, color="black", lw=0.5)
        ax.axvline(0, color="black", lw=0.5)

    ax1.set_title("⚠️ NO TF Layer (Direct Map Frame)")
    ax2.set_title("✅ WITH TF Layer (Odom Frame Buffer)")

    if frame < 20:
        # t = 100.5s 闭环前：SLAM以为在 (-0.5, 1.2)，物理在 (0,0)
        # 1. 没有TF的情况
        ax1.scatter(-0.5, 1.2, color="red", s=100, label="SLAM Robot")
        ax1.plot([-0.5, -0.5], [1.2, 1.8], "r--")
        ax1.plot([-0.5, 0.1], [1.8, 1.8], "g-", lw=4, label="Local Map (Wall)")
        ax1.text(-1.3, 2.2, "t=100.5s: Drifting...", color="blue", fontsize=12)

        # 2. 有TF的情况 (站在Odom/Robot视角，一切正常)
        ax2.scatter(0, 0, color="red", s=100, label="Robot (Odom)")
        ax2.plot([0, 0], [0, 0.6], "r--")
        ax2.plot([0, 0.6], [0.6, 0.6], "g-", lw=4, label="Local Map (Wall)")
        ax2.text(-1.3, 2.2, "t=100.5s: Smooth Odom", color="green", fontsize=12)

    else:
        # t = 100.6s 闭环后：SLAM瞬间跳回 (0,0)
        # 1. 没有TF：历史地图残留在原地，新雷达在(0, 0.6)画新墙 -> 撕裂！
        ax1.scatter(0, 0, color="red", s=100, label="SLAM Robot (Jumped!)")
        # 幽灵墙
        ax1.plot([-0.5, 0.1], [1.8, 1.8], "g-", lw=4, alpha=0.3, label="Ghost Wall (Old)")
        # 真实墙
        ax1.plot([0, 0], [0, 0.6], "r--")
        ax1.plot([0, 0.6], [0.6, 0.6], "g-", lw=4, label="New Wall")
        ax1.text(
            -1.3,
            2.2,
            "t=100.6s: SHOCK! Two Walls!",
            color="darkred",
            fontsize=12,
            fontweight="bold",
        )
        ax1.annotate(
            "Brake! 💥",
            xy=(0, 0),
            xytext=(-1, 0.5),
            arrowprops=dict(facecolor="black", shrink=0.05),
        )

        # 2. 有TF：Odom系没跳变，老墙和新墙完美重合
        ax2.scatter(0, 0, color="red", s=100, label="Robot (Odom)")
        ax2.plot([0, 0], [0, 0.6], "r--")
        ax2.plot([0, 0.6], [0.6, 0.6], "g-", lw=4, label="Local Map (Stable)")
        ax2.text(
            -1.3, 2.2, "t=100.6s: TF Absorbed Shock", color="green", fontsize=12, fontweight="bold"
        )
        ax2.text(-1.3, -0.3, "*Map frame jumped, but Odom is continuous!", color="gray", fontsize=9)

    ax1.legend(loc="lower right")
    ax2.legend(loc="lower right")


# 生成并保存 GIF（与脚本同级目录）
OUTPUT_GIF = Path(__file__).resolve().parent / "demo1.gif"
ani = animation.FuncAnimation(fig, update, frames=40, interval=150)
ani.save(OUTPUT_GIF, writer="pillow", fps=1000 / 150)
print(f"已保存: {OUTPUT_GIF}")
