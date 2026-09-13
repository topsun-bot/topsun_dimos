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

"""Gemini Robotics-ER 1.6 教师模型客户端（黑盒蒸馏的标注来源）。

需要环境变量 GEMINI_API_KEY（https://aistudio.google.com/apikey）。
坐标约定与官方一致：[y, x] 或 [ymin, xmin, ymax, xmax]，归一化到 0-1000。
"""

from __future__ import annotations

import json
import re
import time

from google import genai
from google.genai import types

MODEL = "gemini-robotics-er-1.6-preview"

# 每类任务一条固定 prompt——教师和学生用完全相同的 prompt，保证蒸馏/评测可比。
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

JSON_TASKS = {"points", "boxes", "traj"}


def parse_json_response(text: str):
    """剥掉 ```json 围栏后解析；失败返回 None。"""
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    payload = m.group(1) if m else text
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


class TeacherClient:
    def __init__(self, api_key: str | None = None, model: str = MODEL):
        self.client = genai.Client(api_key=api_key)  # None 时读 GEMINI_API_KEY
        self.model = model

    def annotate(self, jpeg_bytes: bytes, task: str, retries: int = 4) -> str:
        prompt = TASKS[task]
        delay = 5.0
        for attempt in range(retries):
            try:
                resp = self.client.models.generate_content(
                    model=self.model,
                    contents=[
                        types.Part.from_bytes(data=jpeg_bytes, mime_type="image/jpeg"),
                        prompt,
                    ],
                    config=types.GenerateContentConfig(
                        temperature=0.5,
                        # 官方建议：坐标类任务关掉 thinking 提速；不影响输出格式。
                        thinking_config=types.ThinkingConfig(thinking_budget=0),
                    ),
                )
                return resp.text or ""
            except Exception:  # 429/5xx 统一退避重试
                if attempt == retries - 1:
                    raise
                time.sleep(delay)
                delay *= 2
        raise RuntimeError("unreachable")
