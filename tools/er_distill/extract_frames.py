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

"""从 Go2 真值 DB（memory2 SQLite）中按时间间隔抽取相机帧，落成 JPEG + index.jsonl。

用法：
    .venv/bin/python tools/er_distill/extract_frames.py \
        --db-dir data/.lfs/extracted --out data/.lfs/distill/frames_v0 --interval 1.0
"""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import typer

from dimos.msgs.sensor_msgs.Image import Image

app = typer.Typer(add_completion=False)


def extract_db(db_path: Path, out_dir: Path, interval: float, index_fh) -> int:
    scene = db_path.stem
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT id, ts, pose_x, pose_y, pose_z, pose_qz, pose_qw FROM color_image ORDER BY ts"
    ).fetchall()
    if not rows:
        conn.close()
        return 0

    count = 0
    last_ts = None
    for row_id, ts, px, py, pz, qz, qw in rows:
        if last_ts is not None and ts - last_ts < interval:
            continue
        blob = conn.execute("SELECT data FROM color_image_blob WHERE id = ?", (row_id,)).fetchone()
        if blob is None:
            continue
        img = Image.lcm_jpeg_decode(blob[0])
        name = f"{scene}_{row_id:06d}.jpg"
        if not img.save(str(out_dir / name)):
            continue
        index_fh.write(
            json.dumps(
                {
                    "image": name,
                    "scene": scene,
                    "ts": ts,
                    "width": img.width,
                    "height": img.height,
                    "pose": {"x": px, "y": py, "z": pz, "qz": qz, "qw": qw},
                }
            )
            + "\n"
        )
        last_ts = ts
        count += 1
    conn.close()
    return count


@app.command()
def main(
    db_dir: Path = typer.Option(Path("data/.lfs/extracted"), help="真值 DB 所在目录"),
    out: Path = typer.Option(Path("data/.lfs/distill/frames_v0"), help="输出目录"),
    interval: float = typer.Option(1.0, help="抽帧最小时间间隔（秒）"),
) -> None:
    out.mkdir(parents=True, exist_ok=True)
    total = 0
    with open(out / "index.jsonl", "w") as index_fh:
        for db_path in sorted(db_dir.glob("go2_*.db")):
            n = extract_db(db_path, out, interval, index_fh)
            typer.echo(f"{db_path.name}: 抽取 {n} 帧")
            total += n
    typer.echo(f"总计 {total} 帧 → {out}")


if __name__ == "__main__":
    app()
