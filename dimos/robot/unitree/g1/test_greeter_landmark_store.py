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

from __future__ import annotations

from pathlib import Path

from dimos.robot.unitree.g1.greeter_landmark_store import (
    GreeterLandmarkStore,
    Landmark,
    normalize_landmark_text,
)


def _front_desk() -> Landmark:
    return Landmark(
        name="前台",
        x=1.0,
        y=2.0,
        z=0.0,
        intro_script="这里是前台，请在此登记来访信息。",
        synonyms=("接待处", "registration"),
    )


def _restroom() -> Landmark:
    return Landmark(
        name="厕所",
        x=5.0,
        y=-1.0,
        z=0.0,
        intro_script="洗手间到了。",
        synonyms=("卫生间", "洗手间"),
    )


def test_normalize_landmark_text() -> None:
    assert normalize_landmark_text("  前 台 ") == "前台"
    assert normalize_landmark_text("Front Desk") == "frontdesk"


def test_upsert_and_len(tmp_path: Path) -> None:
    store = GreeterLandmarkStore(tmp_path / "lm.json")
    store.upsert(_front_desk())
    assert len(store) == 1
    assert store.names() == ["前台"]


def test_upsert_overwrites_by_name(tmp_path: Path) -> None:
    store = GreeterLandmarkStore(tmp_path / "lm.json")
    store.upsert(_front_desk())
    store.upsert(Landmark(name="前台", x=9.0, y=9.0, z=0.0, intro_script="新讲解词。"))
    assert len(store) == 1
    matched = store.match("前台")
    assert matched is not None
    assert matched.x == 9.0
    assert matched.intro_script == "新讲解词。"


def test_save_and_reload_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "lm.json"
    store = GreeterLandmarkStore(path)
    store.upsert(_front_desk())
    store.upsert(_restroom())

    reloaded = GreeterLandmarkStore(path)
    reloaded.load()
    assert len(reloaded) == 2
    front = reloaded.match("前台")
    assert front is not None
    assert front.intro_script == "这里是前台，请在此登记来访信息。"
    assert front.synonyms == ("接待处", "registration")


def test_load_missing_file_is_empty(tmp_path: Path) -> None:
    store = GreeterLandmarkStore(tmp_path / "does_not_exist.json")
    store.load()
    assert len(store) == 0


def test_match_by_name_and_synonym(tmp_path: Path) -> None:
    store = GreeterLandmarkStore(tmp_path / "lm.json")
    store.upsert(_front_desk())
    store.upsert(_restroom())

    assert store.match("带我去前台").name == "前台"  # type: ignore[union-attr]
    assert store.match("卫生间在哪").name == "厕所"  # type: ignore[union-attr]
    assert store.match("洗手间怎么走").name == "厕所"  # type: ignore[union-attr]
    assert store.match("今天天气怎么样") is None


def test_match_prefers_longest_keyword(tmp_path: Path) -> None:
    store = GreeterLandmarkStore(tmp_path / "lm.json")
    store.upsert(Landmark(name="厅", x=0.0, y=0.0, z=0.0, intro_script="大厅。"))
    store.upsert(Landmark(name="展厅A", x=1.0, y=1.0, z=0.0, intro_script="展厅A。"))
    matched = store.match("带我去展厅A")
    assert matched is not None
    assert matched.name == "展厅A"


def test_intro_scripts_deduped(tmp_path: Path) -> None:
    store = GreeterLandmarkStore(tmp_path / "lm.json")
    store.upsert(_front_desk())
    store.upsert(Landmark(name="后台", x=0.0, y=0.0, z=0.0, intro_script=""))
    scripts = store.intro_scripts()
    assert "这里是前台，请在此登记来访信息。" in scripts
    assert "" not in scripts
