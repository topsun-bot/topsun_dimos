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
# Chinese full-width punctuation is intentional in docstrings/comments.

"""``dimos.utils.trajectory_report`` 单测。

不验证图像像素内容（matplotlib 渲染会因版本/字体不同浮动），只验证：
- HTML 文件生成、size > 1KB、关键字符串存在
- base64 PNG 标签出现
- ``write_comparison_rrd`` 能落盘非空 ``.rrd`` 文件
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from dimos.utils.trajectory_report import (
    IterationRecord,
    generate_html_report,
    write_comparison_rrd,
)


def _make_fake_iterations() -> tuple[np.ndarray, list[IterationRecord]]:
    """构造 truth + 2 个 IterationRecord 的小用例。"""
    # 25 个点的简单 S 形作为 ground truth。
    t = np.linspace(0.0, 2.0 * np.pi, 25)
    truth = np.stack([t, np.sin(t), np.zeros_like(t)], axis=1)

    # 第一轮：偏离严重（整体偏 0.5 米）。
    gen1 = truth + np.array([0.0, 0.5, 0.0])
    # 第二轮：偏离很小（误差近 0）。
    gen2 = truth + np.array([0.0, 0.02, 0.0])

    iters = [
        IterationRecord(
            attempt_idx=0,
            generated_traj=gen1,
            metrics={"ate": 0.5, "frechet": 0.5, "dtw": 12.3},
            feedback="路径整体偏 y+0.5m, 起点出门方向错误",
            skill_sequence_json='{"task": "向前走", "steps": [{"skill": "GoToW", "args": {"x": 1.0}}]}',
        ),
        IterationRecord(
            attempt_idx=1,
            generated_traj=gen2,
            metrics={"ate": 0.02, "frechet": 0.03, "dtw": 0.5},
            feedback="基本贴合真值",
            skill_sequence_json='{"task": "向前走", "steps": [{"skill": "GoToW", "args": {"x": 1.0, "y": -0.5}}]}',
        ),
    ]
    return truth, iters


# ---------------------------------------------------------------------------
# generate_html_report
# ---------------------------------------------------------------------------


def test_generate_html_report_basic(tmp_path: Path) -> None:
    truth, iters = _make_fake_iterations()
    out = generate_html_report(
        tmp_path / "reports",
        task="围绕办公楼跑一圈",
        truth_traj=truth,
        iterations=iters,
        final_action="pass",
    )

    assert out.exists()
    assert out.name == "report.html"
    assert out.parent.name == "reports"

    size = out.stat().st_size
    assert size > 1024, f"HTML 文件太小 ({size} 字节)"

    content = out.read_text(encoding="utf-8")

    # 关键字符串：task 文本、指标名、base64 PNG 标签。
    assert "围绕办公楼跑一圈" in content
    assert "ATE" in content or "ate" in content
    assert "Frechet" in content or "frechet" in content
    assert "DTW" in content or "dtw" in content
    assert "data:image/png;base64," in content
    # 至少有主图 + 2 张 per-iteration 图。
    assert content.count("data:image/png;base64,") >= 3
    # PASS 徽章存在。
    assert "PASS" in content
    # 每轮 feedback 出现。
    assert "路径整体偏" in content
    # skill_sequence_json 在 <pre> 里。
    assert "<pre" in content
    assert "GoToW" in content


def test_generate_html_report_abort_badge(tmp_path: Path) -> None:
    """最终 abort 时应渲染红色徽章。"""
    truth, iters = _make_fake_iterations()
    out = generate_html_report(
        tmp_path / "out",
        task="abort 测试",
        truth_traj=truth,
        iterations=iters,
        final_action="abort",
    )
    content = out.read_text(encoding="utf-8")
    assert "ABORT" in content
    assert "badge-abort" in content
    # 最后一轮 status 应该是 abort 而不是 pass。
    assert "status-abort" in content


def test_generate_html_report_custom_title(tmp_path: Path) -> None:
    truth, iters = _make_fake_iterations()
    custom_title = "我的自定义报告标题 XYZ"
    out = generate_html_report(
        tmp_path / "titled",
        task="t",
        truth_traj=truth,
        iterations=iters,
        final_action="pass",
        title=custom_title,
    )
    content = out.read_text(encoding="utf-8")
    assert custom_title in content
    # default title 不应再出现。
    assert "Closed-loop trajectory report" not in content


def test_generate_html_report_default_title(tmp_path: Path) -> None:
    truth, iters = _make_fake_iterations()
    out = generate_html_report(
        tmp_path / "default-title",
        task="t",
        truth_traj=truth,
        iterations=iters,
        final_action="pass",
    )
    content = out.read_text(encoding="utf-8")
    assert "Closed-loop trajectory report" in content


def test_generate_html_report_no_iterations(tmp_path: Path) -> None:
    """空 iterations 不应炸；HTML 仍生成。"""
    truth, _ = _make_fake_iterations()
    out = generate_html_report(
        tmp_path / "noiter",
        task="只有 truth",
        truth_traj=truth,
        iterations=[],
        final_action="abort",
    )
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "No iterations recorded" in content
    # 主图仍要存在。
    assert "data:image/png;base64," in content


def test_generate_html_report_creates_out_dir(tmp_path: Path) -> None:
    """out_dir 不存在应自动创建（含多层）。"""
    truth, iters = _make_fake_iterations()
    nested = tmp_path / "a" / "b" / "c"
    assert not nested.exists()

    out = generate_html_report(
        nested,
        task="t",
        truth_traj=truth,
        iterations=iters,
        final_action="pass",
    )
    assert out.exists()
    assert out.parent == nested


def test_generate_html_report_html_escaping(tmp_path: Path) -> None:
    """task 文本里的 HTML 特殊字符应被转义，不能破坏布局。"""
    truth, _ = _make_fake_iterations()
    bad_task = '<script>alert("xss")</script>'
    out = generate_html_report(
        tmp_path / "esc",
        task=bad_task,
        truth_traj=truth,
        iterations=[
            IterationRecord(
                attempt_idx=0,
                generated_traj=truth,
                metrics={"ate": 0.0},
                feedback="<b>bold?</b>",
                skill_sequence_json='{"x": "<>&"}',
            )
        ],
        final_action="pass",
    )
    content = out.read_text(encoding="utf-8")
    # 原始 <script> 标签不该出现；转义后的 &lt;script&gt; 应该出现。
    assert "<script>alert" not in content
    assert "&lt;script&gt;" in content
    # feedback 与 JSON 也应被转义。
    assert "&lt;b&gt;bold?&lt;/b&gt;" in content
    assert "&lt;&gt;&amp;" in content


def test_generate_html_report_invalid_shape_raises(tmp_path: Path) -> None:
    """truth_traj 形状错误应抛 ValueError。"""
    with pytest.raises(ValueError, match=r"\(N, 3\)"):
        generate_html_report(
            tmp_path / "bad",
            task="t",
            truth_traj=np.zeros((5,)),  # 1D, 错
            iterations=[],
            final_action="pass",
        )


def test_generate_html_report_missing_metric_keys(tmp_path: Path) -> None:
    """各轮 metrics 键不一致时，缺失应渲染为 n/a 而不是 crash。"""
    truth, _ = _make_fake_iterations()
    iters = [
        IterationRecord(
            attempt_idx=0,
            generated_traj=truth,
            metrics={"ate": 0.1},  # 只有 ate
            feedback="a",
            skill_sequence_json="{}",
        ),
        IterationRecord(
            attempt_idx=1,
            generated_traj=truth,
            metrics={"frechet": 0.2, "dtw": 0.3},  # 没有 ate
            feedback="b",
            skill_sequence_json="{}",
        ),
    ]
    out = generate_html_report(
        tmp_path / "mixed",
        task="混合指标",
        truth_traj=truth,
        iterations=iters,
        final_action="pass",
    )
    content = out.read_text(encoding="utf-8")
    assert "n/a" in content
    # 三个指标都出现在表头。
    assert "ate" in content
    assert "frechet" in content
    assert "dtw" in content


def test_generate_html_report_empty_generated_traj(tmp_path: Path) -> None:
    """某轮 generated_traj 为空 (0,3) 应能渲染（占位文本），不能 crash。"""
    truth, _ = _make_fake_iterations()
    iters = [
        IterationRecord(
            attempt_idx=0,
            generated_traj=np.zeros((0, 3)),
            metrics={"ate": float("nan")},  # NaN 也走 n/a 分支
            feedback="仿真没跑出来",
            skill_sequence_json="{}",
        ),
    ]
    out = generate_html_report(
        tmp_path / "empty",
        task="空轨迹",
        truth_traj=truth,
        iterations=iters,
        final_action="abort",
    )
    content = out.read_text(encoding="utf-8")
    assert out.exists()
    assert "n/a" in content  # NaN -> n/a


# ---------------------------------------------------------------------------
# write_comparison_rrd
# ---------------------------------------------------------------------------


def test_write_comparison_rrd_basic(tmp_path: Path) -> None:
    rerun = pytest.importorskip("rerun")
    _ = rerun  # 仅检查可导入

    truth, iters = _make_fake_iterations()
    rrd_path = tmp_path / "cmp.rrd"

    write_comparison_rrd(
        rrd_path,
        truth_traj=truth,
        generated_traj=iters[-1].generated_traj,
    )

    assert rrd_path.exists()
    assert rrd_path.stat().st_size > 0


def test_write_comparison_rrd_creates_parent(tmp_path: Path) -> None:
    rerun = pytest.importorskip("rerun")
    _ = rerun

    truth, iters = _make_fake_iterations()
    rrd_path = tmp_path / "nested" / "dir" / "cmp.rrd"

    write_comparison_rrd(
        rrd_path,
        truth_traj=truth,
        generated_traj=iters[0].generated_traj,
    )
    assert rrd_path.exists()


def test_write_comparison_rrd_empty_trajectories(tmp_path: Path) -> None:
    """两条都空轨迹也应安全落盘。"""
    rerun = pytest.importorskip("rerun")
    _ = rerun

    rrd_path = tmp_path / "empty.rrd"
    write_comparison_rrd(
        rrd_path,
        truth_traj=np.zeros((0, 3)),
        generated_traj=np.zeros((0, 3)),
    )
    assert rrd_path.exists()
    assert rrd_path.stat().st_size > 0  # 流元数据本身就有字节


def test_write_comparison_rrd_invalid_shape_raises(tmp_path: Path) -> None:
    pytest.importorskip("rerun")
    with pytest.raises(ValueError, match=r"\(N, 3\)"):
        write_comparison_rrd(
            tmp_path / "bad.rrd",
            truth_traj=np.zeros((5, 2)),  # 列数错
            generated_traj=np.zeros((0, 3)),
        )
