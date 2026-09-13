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

"""学生 vs 教师一致性评测：同一批帧、同一批 prompt，量化两边输出的差距。

指标（坐标都在 0-1000 归一化空间）：
  - json_valid：能解析出合法 JSON 的比例（结构服从度）
  - points/traj：对称 Chamfer 距离（越小越像教师）
  - boxes：贪心 IoU 匹配的平均 IoU + 教师框召回率（IoU>=0.5）

用法：
    python tools/er_distill/eval_agreement.py \
        --teacher data/.lfs/distill/frames_v0/teacher_raw.jsonl \
        --student data/.lfs/distill/frames_v0/student_raw.jsonl
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from statistics import mean

import typer

app = typer.Typer(add_completion=False)


def parse_json(text: str):
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    payload = m.group(1) if m else text
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


def get_points(parsed) -> list[tuple[float, float]]:
    pts = []
    if isinstance(parsed, list):
        for item in parsed:
            p = item.get("point") if isinstance(item, dict) else None
            if isinstance(p, list) and len(p) == 2:
                pts.append((float(p[0]), float(p[1])))
    return pts


def get_boxes(parsed) -> list[list[float]]:
    boxes = []
    if isinstance(parsed, list):
        for item in parsed:
            b = item.get("box_2d") if isinstance(item, dict) else None
            if isinstance(b, list) and len(b) == 4:
                boxes.append([float(v) for v in b])
    return boxes


def chamfer(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> float | None:
    if not a or not b:
        return None
    d_ab = mean(min(((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2) ** 0.5 for q in b) for p in a)
    d_ba = mean(min(((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2) ** 0.5 for q in a) for p in b)
    return (d_ab + d_ba) / 2


def iou(a: list[float], b: list[float]) -> float:
    ymin = max(a[0], b[0])
    xmin = max(a[1], b[1])
    ymax = min(a[2], b[2])
    xmax = min(a[3], b[3])
    inter = max(0.0, ymax - ymin) * max(0.0, xmax - xmin)
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def greedy_iou_match(teacher: list[list[float]], student: list[list[float]]):
    pairs = sorted(
        ((iou(t, s), ti, si) for ti, t in enumerate(teacher) for si, s in enumerate(student)),
        reverse=True,
    )
    used_t: set[int] = set()
    used_s: set[int] = set()
    matched = []
    for score, ti, si in pairs:
        if ti in used_t or si in used_s:
            continue
        used_t.add(ti)
        used_s.add(si)
        matched.append(score)
    return matched


def load_raw(path: Path) -> dict[tuple[str, str], str]:
    out = {}
    with open(path) as fh:
        for line in fh:
            rec = json.loads(line)
            out[(rec["image"], rec["task"])] = rec["response"]
    return out


@app.command()
def main(
    teacher: Path = typer.Option(...),
    student: Path = typer.Option(...),
) -> None:
    t_raw = load_raw(teacher)
    s_raw = load_raw(student)
    keys = sorted(set(t_raw) & set(s_raw))
    typer.echo(f"教师 {len(t_raw)} 条 / 学生 {len(s_raw)} 条 / 交集 {len(keys)} 条\n")

    for task in ("points", "traj", "boxes", "spatial"):
        task_keys = [k for k in keys if k[1] == task]
        if not task_keys:
            continue
        if task == "spatial":
            lens = [len(s_raw[k]) for k in task_keys]
            typer.echo(
                f"[spatial] n={len(task_keys)} 学生平均输出 {mean(lens):.0f} 字符（需 LLM 评审定性）"
            )
            continue

        valid = 0
        chamfers = []
        ious = []
        recalls = []
        for k in task_keys:
            sp = parse_json(s_raw[k])
            tp = parse_json(t_raw[k])
            if sp is not None:
                valid += 1
            if tp is None or sp is None:
                continue
            if task in ("points", "traj"):
                d = chamfer(get_points(tp), get_points(sp))
                if d is not None:
                    chamfers.append(d)
            else:
                tb, sb = get_boxes(tp), get_boxes(sp)
                if tb and sb:
                    matched = greedy_iou_match(tb, sb)
                    ious.extend(matched)
                    recalls.append(sum(1 for s in matched if s >= 0.5) / len(tb))

        line = f"[{task}] n={len(task_keys)} json_valid={valid / len(task_keys):.1%}"
        if chamfers:
            line += f" chamfer={mean(chamfers):.1f}/1000"
        if ious:
            line += f" mean_iou={mean(ious):.3f} recall@0.5={mean(recalls):.1%}"
        typer.echo(line)


if __name__ == "__main__":
    app()
