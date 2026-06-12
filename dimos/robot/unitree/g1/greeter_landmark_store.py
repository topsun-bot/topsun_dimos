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

"""导览地标的持久化存储。

每个地标在工作人员现场标点时一次性录入:名称、地图坐标(标点瞬间的里程计位姿)与
**到站讲解词**(``intro_script``)。讲解词只来自标点时写入的数据,运行时不让 LLM
改写或扩写(见 ``docs/platforms/humanoid/g1/greeter_onboard.md``)。

本模块为纯逻辑 + JSON 持久化,不依赖机器人硬件,便于单元测试。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import threading
from typing import Any

from dimos.utils.logging_config import setup_logger

logger = setup_logger()


def normalize_landmark_text(text: str) -> str:
    """小写化并去除空白,用于地标名称/同义词的子串匹配。"""
    return "".join(text.strip().lower().split())


@dataclass(frozen=True)
class Landmark:
    """一个已标点的地标:坐标 + 固定讲解词。"""

    name: str
    x: float
    y: float
    z: float
    yaw: float = 0.0
    intro_script: str = ""
    synonyms: tuple[str, ...] = field(default_factory=tuple)

    def keywords(self) -> tuple[str, ...]:
        """问路匹配关键词:名称 + 同义词。"""
        return (self.name, *self.synonyms)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["synonyms"] = list(self.synonyms)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Landmark:
        return cls(
            name=str(data["name"]),
            x=float(data.get("x", 0.0)),
            y=float(data.get("y", 0.0)),
            z=float(data.get("z", 0.0)),
            yaw=float(data.get("yaw", 0.0)),
            intro_script=str(data.get("intro_script", "")),
            synonyms=tuple(str(s) for s in data.get("synonyms", [])),
        )


class GreeterLandmarkStore:
    """线程安全的地标表,持久化到单个 JSON 文件,可在重启后重载。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._landmarks: dict[str, Landmark] = {}

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> None:
        """从磁盘读取地标表(文件不存在则为空,不报错)。"""
        with self._lock:
            self._landmarks = {}
            if not self._path.exists():
                return
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                logger.exception("读取地标表失败: %s", self._path)
                return
            for entry in raw if isinstance(raw, list) else []:
                try:
                    lm = Landmark.from_dict(entry)
                except Exception:
                    logger.exception("跳过损坏的地标条目: %s", entry)
                    continue
                self._landmarks[normalize_landmark_text(lm.name)] = lm
        logger.info("已加载 %d 个地标: %s", len(self._landmarks), ", ".join(self.names()))

    def save(self) -> None:
        with self._lock:
            self._save_locked()

    def _save_locked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = [lm.to_dict() for lm in self._landmarks.values()]
        self._path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def upsert(self, landmark: Landmark) -> None:
        """新增或按名称(归一化)覆盖一个地标,并立即持久化。"""
        with self._lock:
            self._landmarks[normalize_landmark_text(landmark.name)] = landmark
            self._save_locked()

    def all(self) -> list[Landmark]:
        with self._lock:
            return list(self._landmarks.values())

    def names(self) -> list[str]:
        with self._lock:
            return [lm.name for lm in self._landmarks.values()]

    def intro_scripts(self) -> list[str]:
        """所有非空讲解词(去重,用于 TTS 预缓存)。"""
        with self._lock:
            scripts = [lm.intro_script.strip() for lm in self._landmarks.values()]
        return list(dict.fromkeys(s for s in scripts if s))

    def match(self, text: str) -> Landmark | None:
        """在 ``text`` 中按最长关键词(名称/同义词)匹配地标。"""
        norm = normalize_landmark_text(text)
        if not norm:
            return None
        with self._lock:
            candidates = [
                (keyword, lm)
                for lm in self._landmarks.values()
                for keyword in lm.keywords()
                if keyword.strip()
            ]
        best: Landmark | None = None
        best_len = 0
        for keyword, lm in candidates:
            key_norm = normalize_landmark_text(keyword)
            if key_norm and key_norm in norm and len(key_norm) > best_len:
                best = lm
                best_len = len(key_norm)
        return best

    def __len__(self) -> int:
        with self._lock:
            return len(self._landmarks)


__all__ = ["GreeterLandmarkStore", "Landmark", "normalize_landmark_text"]
