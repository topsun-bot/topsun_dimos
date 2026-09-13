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

"""用本机 MLX 学生模型（Gemma 4）对同样的帧+prompt 出结果，产出 student_raw.jsonl。

蒸馏前跑一次 = 零样本基线；蒸馏后换 --model 指向微调产物再跑 = 效果对比。
用 mlx-vlm 独立环境运行：
    ~/.venvs/er-distill/bin/python tools/er_distill/run_student.py \
        --frames data/.lfs/distill/frames_v0 --model mlx-community/gemma-4-26b-a4b-it-4bit
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

app = typer.Typer(add_completion=False)

# 与 teacher_client.TASKS 保持一致；复制一份避免此脚本依赖项目 venv。
TASKS: dict[str, str] = {
    "points": (
        "Point to no more than 10 distinct objects in the image. "
        'The answer should follow the json format: [{"point": [y, x], "label": "<name>"}, ...]. '
        "The points are in [y, x] format normalized to 0-1000."
    ),
    "boxes": (
        "Detect the prominent objects in the image (no more than 10). Return a json list where "
        'each entry is {"box_2d": [ymin, xmin, ymax, xmax], "label": "<name>"}, '
        "coordinates normalized to 0-1000."
    ),
    "traj": (
        "You are a quadruped robot and this image is from your front camera. Plan a safe walking "
        "path from the bottom-center of the image toward the most open traversable area. Return a "
        'json list of 8 waypoints in order: [{"point": [y, x], "label": "<index>"}, ...], '
        "normalized to 0-1000."
    ),
    "spatial": (
        "You are a quadruped robot and this image is from your front camera. In 3-5 short "
        "sentences: name the main objects, describe their relative positions, and state which "
        "direction is traversable and what to avoid."
    ),
}


@app.command()
def main(
    frames: Path = typer.Option(Path("data/.lfs/distill/frames_v0")),
    model: str = typer.Option("mlx-community/gemma-4-26b-a4b-it-4bit"),
    tasks: str = typer.Option("points,boxes,traj,spatial"),
    out_name: str = typer.Option("student_raw.jsonl", help="输出文件名（微调后跑用别的名字）"),
    limit: int = typer.Option(0, help="最多处理多少帧（0=全部）"),
    max_tokens: int = typer.Option(512),
) -> None:
    from mlx_vlm import generate, load
    from mlx_vlm.prompt_utils import apply_chat_template
    from mlx_vlm.utils import load_config

    task_list = [t.strip() for t in tasks.split(",") if t.strip() in TASKS]
    out_path = frames / out_name

    done: set[tuple[str, str]] = set()
    if out_path.exists():
        with open(out_path) as fh:
            for line in fh:
                rec = json.loads(line)
                done.add((rec["image"], rec["task"]))

    images = [json.loads(l)["image"] for l in open(frames / "index.jsonl")]
    if limit:
        images = images[:limit]

    vlm, processor = load(model)
    config = load_config(model)

    new = 0
    for image in images:
        for task in task_list:
            if (image, task) in done:
                continue
            prompt = apply_chat_template(processor, config, TASKS[task], num_images=1)
            result = generate(
                vlm,
                processor,
                prompt,
                image=[str(frames / image)],
                max_tokens=max_tokens,
                temperature=0.0,
                verbose=False,
            )
            text = result.text if hasattr(result, "text") else str(result)
            with open(out_path, "a") as fh:
                fh.write(
                    json.dumps(
                        {
                            "image": image,
                            "task": task,
                            "prompt": TASKS[task],
                            "response": text,
                            "model": model,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            new += 1
            if new % 20 == 0:
                typer.echo(f"已推理 {new} 条")
    typer.echo(f"完成：新增 {new} 条 → {out_path}")


if __name__ == "__main__":
    app()
