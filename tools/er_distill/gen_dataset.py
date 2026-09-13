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

"""调教师模型（Gemini ER 1.6）给抽出的相机帧打标，产出蒸馏训练集。

输出两份 JSONL（追加写，支持断点续跑）：
  teacher_raw.jsonl — {image, task, prompt, response}，评测和 SFT 都从这份派生。

用法：
    export GEMINI_API_KEY=...
    .venv/bin/python tools/er_distill/gen_dataset.py \
        --frames data/.lfs/distill/frames_v0 --tasks points,boxes,traj,spatial --qps 0.5
"""

from __future__ import annotations

import json
from pathlib import Path
import time

from teacher_client import TASKS, TeacherClient
import typer

app = typer.Typer(add_completion=False)


@app.command()
def main(
    frames: Path = typer.Option(Path("data/.lfs/distill/frames_v0")),
    tasks: str = typer.Option("points,boxes,traj,spatial", help="逗号分隔的任务列表"),
    qps: float = typer.Option(0.5, help="每秒请求数上限（preview 模型限额低，别调高）"),
    limit: int = typer.Option(0, help="最多处理多少帧（0=全部），试跑时用"),
) -> None:
    task_list = [t.strip() for t in tasks.split(",") if t.strip() in TASKS]
    out_path = frames / "teacher_raw.jsonl"

    done: set[tuple[str, str]] = set()
    if out_path.exists():
        with open(out_path) as fh:
            for line in fh:
                rec = json.loads(line)
                done.add((rec["image"], rec["task"]))
    typer.echo(f"已有 {len(done)} 条标注，继续断点续跑")

    images = [json.loads(l)["image"] for l in open(frames / "index.jsonl")]
    if limit:
        images = images[:limit]

    client = TeacherClient()
    min_gap = 1.0 / qps
    last_call = 0.0
    new = 0
    for image in images:
        jpeg = (frames / image).read_bytes()
        for task in task_list:
            if (image, task) in done:
                continue
            wait = min_gap - (time.time() - last_call)
            if wait > 0:
                time.sleep(wait)
            last_call = time.time()
            try:
                response = client.annotate(jpeg, task)
            except Exception as e:
                typer.echo(f"跳过 {image}/{task}: {e}")
                continue
            with open(out_path, "a") as fh:
                fh.write(
                    json.dumps(
                        {"image": image, "task": task, "prompt": TASKS[task], "response": response},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            new += 1
            if new % 50 == 0:
                typer.echo(f"新增 {new} 条")
    typer.echo(f"完成：新增 {new} 条 → {out_path}")


if __name__ == "__main__":
    app()
