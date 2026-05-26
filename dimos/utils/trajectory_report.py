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

# ruff: noqa: RUF001, RUF002
# Chinese full-width punctuation (，：（）；) is intentional in docstrings/comments.

"""闭环系统轨迹比对报告生成。

提供两个工具：
- ``generate_html_report``：把 truth/generated 多轮轨迹、每轮指标和 critic
  反馈渲染成单文件 HTML 报告，matplotlib 图以 base64 内嵌，便于离线分享。
- ``write_comparison_rrd``：把 truth 与 generated 两条轨迹用 ``rr.LineStrips3D``
  写入同一个 ``.rrd`` 文件，可以用 Rerun viewer 做视觉对比。

输入轨迹严格遵守闭环系统接口契约 C：``np.ndarray`` shape ``(N, 3)``，
``(x, y, z)``，单位米。
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import html
import io
from pathlib import Path
import sys
from typing import TYPE_CHECKING

# 在无头环境/CI 里默认用 Agg backend；如果 pyplot 已经被其它模块导入
# (说明调用方可能已经选了交互 backend) 就不要覆盖，避免破坏其图形展示。
import matplotlib

if "matplotlib.pyplot" not in sys.modules:
    matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

if TYPE_CHECKING:
    from matplotlib.figure import Figure


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class IterationRecord:
    """单轮闭环结果。

    Attributes:
        attempt_idx: 轮次序号（从 0 开始）。
        generated_traj: 该轮 AI 生成代码在仿真里跑出的轨迹，
            形状 ``(M, 3)``，列依次为 ``x, y, z``，单位米。
        metrics: 指标字典，常见键：``ate``、``frechet``、``dtw``。
            值为 float 或 None；None 会渲染为 "n/a"。
        feedback: critic 对该轮的文本反馈。
        skill_sequence_json: 该轮 LLM 输出的 skill 序列 JSON 串（原样落入报告，
            供人工事后审阅，不做反序列化）。
    """

    attempt_idx: int
    generated_traj: np.ndarray
    metrics: dict
    feedback: str
    skill_sequence_json: str


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _validate_traj(traj: np.ndarray, name: str) -> np.ndarray:
    """校验输入轨迹符合 ``(N, 3)`` numpy 约定。

    Args:
        traj: 待校验数组。
        name: 出错时打印的字段名。

    Returns:
        转为 float 的轨迹副本，shape ``(N, 3)``。允许 ``N == 0``。
    """
    arr = np.asarray(traj, dtype=float)
    if arr.ndim != 2 or arr.shape[1] != 3:
        raise ValueError(
            f"{name} 轨迹形状必须是 (N, 3)，实际是 {arr.shape}",
        )
    return arr


def _fig_to_base64_png(fig: Figure, dpi: int = 120) -> str:
    """把 matplotlib Figure 序列化为 base64 编码的 PNG 字符串。

    Args:
        fig: matplotlib Figure。
        dpi: 保存 DPI，默认 120 折中分辨率/体积。

    Returns:
        ``base64`` 字符串（无 ``data:`` 前缀），可直接拼到
        ``<img src="data:image/png;base64,...">``。
    """
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _plot_overlay(
    truth: np.ndarray,
    generated: np.ndarray,
    *,
    title: str,
    truth_label: str = "Truth",
    generated_label: str = "Generated",
) -> str:
    """生成 truth + generated 2D 俯视叠加图，返回 base64 PNG。

    使用 (x, y) 投影，y 轴 ``equal`` 比例避免视觉拉伸。空轨迹会以
    "(empty trajectory)" 占位文本提示。
    """
    fig, ax = plt.subplots(figsize=(7.5, 5.5))

    plotted_any = False
    if truth.shape[0] > 0:
        ax.plot(
            truth[:, 0],
            truth[:, 1],
            color="#1f77b4",  # 蓝色实线
            linewidth=2.0,
            label=truth_label,
            zorder=2,
        )
        ax.scatter(
            [truth[0, 0]],
            [truth[0, 1]],
            color="#1f77b4",
            marker="o",
            s=40,
            zorder=3,
            label=f"{truth_label} start",
        )
        plotted_any = True

    if generated.shape[0] > 0:
        ax.plot(
            generated[:, 0],
            generated[:, 1],
            color="#ff7f0e",  # 橙色虚线
            linewidth=2.0,
            linestyle="--",
            label=generated_label,
            zorder=2,
        )
        ax.scatter(
            [generated[0, 0]],
            [generated[0, 1]],
            color="#ff7f0e",
            marker="s",
            s=40,
            zorder=3,
            label=f"{generated_label} start",
        )
        plotted_any = True

    if not plotted_any:
        ax.text(
            0.5,
            0.5,
            "(empty trajectory)",
            transform=ax.transAxes,
            ha="center",
            va="center",
            fontsize=14,
            color="#888888",
        )

    ax.set_title(title)
    ax.set_xlabel("x [m]")
    ax.set_ylabel("y [m]")
    ax.set_aspect("equal", adjustable="datalim")
    ax.grid(True, linestyle=":", alpha=0.5)
    if plotted_any:
        ax.legend(loc="best", fontsize=9)

    return _fig_to_base64_png(fig)


def _fmt_metric(value: object) -> str:
    """把指标值格式化为短字符串。

    None / NaN / 非数值统一返回 ``"n/a"``，数值保留 4 位小数。
    """
    if value is None:
        return "n/a"
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "n/a"
    if np.isnan(f):
        return "n/a"
    return f"{f:.4f}"


def _iter_status(metrics: dict, *, is_last: bool, final_action: str) -> str:
    """根据 metrics + 是否最后一轮 + final_action 推断单轮状态字符串。

    只用于报告展示，不参与判定逻辑。最后一轮 status 直接反映 final_action，
    中间轮一律标 "retry"。
    """
    if is_last:
        if final_action == "pass":
            return "pass"
        if final_action == "abort":
            return "abort"
        return final_action  # 未知 action 原样透传
    return "retry"


def _render_metrics_table(
    iterations: list[IterationRecord],
    final_action: str,
) -> str:
    """渲染指标表 HTML 片段。

    所有出现在任何一轮 metrics 字典里的键都会成为一列；优先把
    ``ate``、``frechet``、``dtw`` 排在前面，其余按字母序。
    """
    preferred = ["ate", "frechet", "dtw"]
    seen: dict[str, None] = {}
    for it in iterations:
        for k in it.metrics:
            seen[k] = None
    other_keys = sorted(k for k in seen if k not in preferred)
    metric_keys = [k for k in preferred if k in seen] + other_keys

    header_cells = "".join(f"<th>{html.escape(k)}</th>" for k in metric_keys)
    header = f"<tr><th>Attempt</th>{header_cells}<th>Status</th></tr>"

    rows: list[str] = []
    for i, it in enumerate(iterations):
        is_last = i == len(iterations) - 1
        status = _iter_status(it.metrics, is_last=is_last, final_action=final_action)
        status_cls = {
            "pass": "status-pass",
            "abort": "status-abort",
            "retry": "status-retry",
        }.get(status, "status-retry")
        metric_cells = "".join(
            f"<td>{html.escape(_fmt_metric(it.metrics.get(k)))}</td>" for k in metric_keys
        )
        rows.append(
            "<tr>"
            f"<td>#{it.attempt_idx}</td>"
            f"{metric_cells}"
            f'<td class="{status_cls}">{html.escape(status)}</td>'
            "</tr>"
        )

    if not metric_keys:
        # 无任何指标时给一个友好提示。
        return (
            '<table class="metrics-table">'
            "<tr><th>Attempt</th><th>Status</th></tr>"
            + "".join(
                f"<tr><td>#{it.attempt_idx}</td>"
                f'<td class="status-retry">{html.escape(_iter_status(it.metrics, is_last=(i == len(iterations) - 1), final_action=final_action))}</td></tr>'
                for i, it in enumerate(iterations)
            )
            + "</table>"
        )

    return f'<table class="metrics-table">{header}' + "".join(rows) + "</table>"


def _render_iteration_section(
    truth: np.ndarray,
    record: IterationRecord,
    *,
    is_last: bool,
    final_action: str,
) -> str:
    """渲染单轮折叠区 HTML。"""
    overlay_b64 = _plot_overlay(
        truth,
        record.generated_traj,
        title=f"Attempt #{record.attempt_idx}: truth vs generated",
    )
    status = _iter_status(record.metrics, is_last=is_last, final_action=final_action)
    status_cls = {
        "pass": "status-pass",
        "abort": "status-abort",
        "retry": "status-retry",
    }.get(status, "status-retry")

    feedback_html = html.escape(record.feedback) if record.feedback else "(no feedback)"
    skill_html = (
        html.escape(record.skill_sequence_json)
        if record.skill_sequence_json
        else "(no skill sequence captured)"
    )

    metric_chips = " ".join(
        f'<span class="chip">{html.escape(k)}={html.escape(_fmt_metric(v))}</span>'
        for k, v in record.metrics.items()
    )

    # 默认展开最后一轮（最相关）；其他折叠。
    open_attr = " open" if is_last else ""

    return (
        f'<details class="iter-block"{open_attr}>'
        f"<summary>Attempt #{record.attempt_idx} "
        f'<span class="{status_cls}">[{html.escape(status)}]</span></summary>'
        f'<div class="iter-body">'
        f'<div class="chip-row">{metric_chips}</div>'
        f'<img class="iter-img" alt="trajectory overlay attempt {record.attempt_idx}" '
        f'src="data:image/png;base64,{overlay_b64}" />'
        f"<h4>Critic feedback</h4>"
        f'<p class="feedback">{feedback_html}</p>'
        f"<h4>Skill sequence (LLM output)</h4>"
        f'<pre class="skill-json">{skill_html}</pre>'
        f"</div>"
        f"</details>"
    )


_CSS = """
body { font-family: -apple-system, BlinkMacSystemFont, 'Helvetica Neue', Arial, sans-serif;
       margin: 24px; color: #222; max-width: 1100px; }
h1 { margin-bottom: 4px; }
.subtitle { color: #666; margin-top: 0; }
.badge { display: inline-block; padding: 4px 12px; border-radius: 12px;
         color: #fff; font-weight: 600; margin-left: 12px; vertical-align: middle; }
.badge-pass { background: #2ca02c; }
.badge-abort { background: #d62728; }
.badge-other { background: #888; }
.section { margin-top: 28px; }
.metrics-table { border-collapse: collapse; margin-top: 8px; }
.metrics-table th, .metrics-table td { border: 1px solid #ccc; padding: 6px 12px; text-align: right; }
.metrics-table th { background: #f4f4f4; }
.metrics-table td:first-child, .metrics-table th:first-child { text-align: left; }
.status-pass { color: #2ca02c; font-weight: 600; }
.status-abort { color: #d62728; font-weight: 600; }
.status-retry { color: #ff7f0e; font-weight: 600; }
.iter-block { border: 1px solid #ddd; border-radius: 6px; padding: 8px 12px; margin: 10px 0; background: #fafafa; }
.iter-block summary { cursor: pointer; font-weight: 600; }
.iter-body { padding-top: 10px; }
.iter-img { max-width: 100%; height: auto; border: 1px solid #eee; background: #fff; }
.chip-row { margin-bottom: 8px; }
.chip { display: inline-block; background: #eef; color: #224; padding: 2px 8px;
        border-radius: 10px; font-size: 12px; margin-right: 6px; }
.feedback { white-space: pre-wrap; background: #fff; border-left: 3px solid #1f77b4;
            padding: 8px 12px; margin: 6px 0; }
.skill-json { background: #1e1e1e; color: #e8e8e8; padding: 12px;
              border-radius: 4px; overflow-x: auto; font-size: 12px;
              white-space: pre; }
.main-img { max-width: 100%; height: auto; border: 1px solid #ccc; background: #fff; }
"""


def _render_html(
    *,
    task: str,
    title: str,
    truth: np.ndarray,
    iterations: list[IterationRecord],
    final_action: str,
) -> str:
    """组装最终 HTML 字符串。

    没有外部模板引擎依赖，全部用 f-string 拼接。
    """
    if final_action == "pass":
        badge_cls = "badge badge-pass"
    elif final_action == "abort":
        badge_cls = "badge badge-abort"
    else:
        badge_cls = "badge badge-other"

    badge_html = f'<span class="{badge_cls}">{html.escape(final_action.upper())}</span>'

    # 主图：用最后一轮的 generated 与 truth 叠加。无 iteration 时只画 truth。
    if iterations:
        last = iterations[-1]
        main_overlay_b64 = _plot_overlay(
            truth,
            last.generated_traj,
            title=f"Truth vs final generated (attempt #{last.attempt_idx})",
        )
    else:
        main_overlay_b64 = _plot_overlay(
            truth,
            np.zeros((0, 3)),
            title="Truth only (no iterations recorded)",
        )

    iter_sections = "".join(
        _render_iteration_section(
            truth,
            it,
            is_last=(i == len(iterations) - 1),
            final_action=final_action,
        )
        for i, it in enumerate(iterations)
    )
    if not iter_sections:
        iter_sections = "<p><em>No iterations recorded.</em></p>"

    metrics_table = _render_metrics_table(iterations, final_action)

    return (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head>'
        f'<meta charset="utf-8"><title>{html.escape(title)}</title>'
        f"<style>{_CSS}</style>"
        "</head><body>"
        f"<h1>{html.escape(title)} {badge_html}</h1>"
        f'<p class="subtitle"><strong>Task:</strong> {html.escape(task)}</p>'
        '<div class="section">'
        "<h2>Final overlay</h2>"
        f'<img class="main-img" alt="truth vs final generated" '
        f'src="data:image/png;base64,{main_overlay_b64}" />'
        "</div>"
        '<div class="section">'
        "<h2>Per-iteration metrics</h2>"
        f"{metrics_table}"
        "</div>"
        '<div class="section">'
        "<h2>Iterations</h2>"
        f"{iter_sections}"
        "</div>"
        "</body></html>"
    )


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------


def generate_html_report(
    out_dir: Path,
    *,
    task: str,
    truth_traj: np.ndarray,
    iterations: list[IterationRecord],
    final_action: str,
    title: str | None = None,
) -> Path:
    """生成单文件 HTML 报告并返回路径。

    Args:
        out_dir: 报告输出目录。不存在则递归创建。
        task: 任务自然语言描述，渲染在标题下方。
        truth_traj: ground-truth 轨迹，shape ``(N, 3)``。
        iterations: 每轮闭环结果。空列表也允许（仅渲染 truth）。
        final_action: 最终决策，``"pass"`` / ``"abort"`` / 其他字符串。
            决定标题徽章颜色和最后一行 status。
        title: 报告标题，默认为 ``"Closed-loop trajectory report"``。

    Returns:
        生成的 HTML 文件路径（``out_dir / "report.html"``）。

    Raises:
        ValueError: 轨迹形状不是 ``(N, 3)``。
    """
    truth = _validate_traj(truth_traj, "truth_traj")
    # 校验每一轮生成轨迹形状，捕获错误时携带轮次信息。
    for it in iterations:
        _ = _validate_traj(it.generated_traj, f"iterations[{it.attempt_idx}].generated_traj")

    resolved_title = title if title else "Closed-loop trajectory report"

    out_dir.mkdir(parents=True, exist_ok=True)
    html_str = _render_html(
        task=task,
        title=resolved_title,
        truth=truth,
        iterations=iterations,
        final_action=final_action,
    )
    out_path = out_dir / "report.html"
    out_path.write_text(html_str, encoding="utf-8")
    return out_path


def write_comparison_rrd(
    rrd_path: Path,
    *,
    truth_traj: np.ndarray,
    generated_traj: np.ndarray,
) -> None:
    """把 truth 与 generated 两条轨迹写入同一个 ``.rrd`` 文件。

    用 ``rr.LineStrips3D`` 在路径 ``/truth`` / ``/generated`` 下分别落盘，
    打开后即可在 Rerun viewer 里叠加对比。

    Args:
        rrd_path: 输出 ``.rrd`` 路径。父目录不存在则创建。
        truth_traj: 真值轨迹，shape ``(N, 3)``。
        generated_traj: 生成轨迹，shape ``(M, 3)``。

    Raises:
        ValueError: 轨迹形状不是 ``(N, 3)``。
        ImportError: rerun-sdk 不可用（实际由 ``import`` 抛出）。
    """
    import rerun as rr  # 本地 import，让 rerun 缺失只在调用时报错

    truth = _validate_traj(truth_traj, "truth_traj")
    generated = _validate_traj(generated_traj, "generated_traj")

    rrd_path.parent.mkdir(parents=True, exist_ok=True)

    # 用独立 RecordingStream，避免污染调用方进程里的全局默认 stream。
    rec = rr.RecordingStream(application_id="dimos.closed_loop_comparison")
    rec.save(str(rrd_path))

    # 即便轨迹为空也要 log，让 viewer 知道这条路径存在（log 空 strip）。
    truth_strip = [truth.tolist()] if truth.shape[0] > 0 else []
    gen_strip = [generated.tolist()] if generated.shape[0] > 0 else []

    rec.log(
        "/truth",
        rr.LineStrips3D(
            truth_strip,
            colors=[(31, 119, 180)],  # matplotlib 蓝
            radii=0.03,
        ),
    )
    rec.log(
        "/generated",
        rr.LineStrips3D(
            gen_strip,
            colors=[(255, 127, 14)],  # matplotlib 橙
            radii=0.03,
        ),
    )
