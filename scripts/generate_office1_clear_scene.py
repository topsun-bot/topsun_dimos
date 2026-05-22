#!/usr/bin/env python3
# Copyright 2025-2026 Dimensional Inc.
#
# Regenerate scene_office1_clear.xml from the LFS office1 scene.
#
# Usage (after mujoco_sim is extracted to data/mujoco_sim/):
#   uv run python scripts/generate_office1_clear_scene.py

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from dimos.simulation.mujoco.scenes.stairs import staircase_asset_xml, staircase_worldbody_xml
from dimos.simulation.mujoco.scenes.thresholds import thresholds_asset_xml, thresholds_worldbody_xml
from dimos.utils.data import get_data

REMOVE_PATTERNS = ("chair", "table", "desk", "desktop")
OUT = Path(__file__).resolve().parents[1] / "dimos/simulation/mujoco/scenes/scene_office1_clear.xml"


def _inject_stairs(xml_str: str) -> str:
    xml_str = re.sub(
        r'\s*<body name="staircase"[^>]*>.*?</body>\s*',
        "\n",
        xml_str,
        count=1,
        flags=re.DOTALL,
    )
    if 'name="mat_stair"' not in xml_str:
        xml_str = xml_str.replace("  </asset>\n", staircase_asset_xml() + "  </asset>\n", 1)
    if 'name="staircase"' not in xml_str:
        xml_str = re.sub(
            r"\s*</worldbody>\s*\n",
            "\n" + staircase_worldbody_xml() + "  </worldbody>\n",
            xml_str,
            count=1,
        )
    return xml_str


def _inject_thresholds(xml_str: str) -> str:
    xml_str = re.sub(
        r'\s*<body name="thresholds"[^>]*>.*?</body>\s*',
        "\n",
        xml_str,
        count=1,
        flags=re.DOTALL,
    )
    if 'name="mat_threshold"' not in xml_str:
        xml_str = xml_str.replace("  </asset>\n", thresholds_asset_xml() + "  </asset>\n", 1)
    if 'name="thresholds"' not in xml_str:
        xml_str = re.sub(
            r"\s*</worldbody>\s*\n",
            "\n" + thresholds_worldbody_xml() + "  </worldbody>\n",
            xml_str,
            count=1,
        )
    return xml_str


def _should_remove(name: str) -> bool:
    n = name.lower()
    return any(p in n for p in REMOVE_PATTERNS)


def main() -> None:
    src = Path(get_data("mujoco_sim")) / "scene_office1.xml"
    tree = ET.parse(src)
    root = tree.getroot()
    root.set("model", "scene_office1_clear")

    asset = root.find("asset")
    if asset is not None:
        for child in list(asset):
            ident = child.get("name") or child.get("file") or ""
            if _should_remove(ident):
                asset.remove(child)

    worldbody = root.find("worldbody")
    removed = 0
    if worldbody is not None:
        for body in list(worldbody):
            if body.tag.split("}")[-1] != "body":
                continue
            if _should_remove(body.get("name") or ""):
                worldbody.remove(body)
                removed += 1

    ET.indent(tree, space="  ")
    xml_str = '<?xml version="1.0" ?>\n' + ET.tostring(root, encoding="unicode").split("?>", 1)[-1].lstrip()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    xml_str = _inject_stairs(xml_str)
    OUT.write_text(_inject_thresholds(xml_str))
    print(
        f"Wrote {OUT} (removed {removed} bodies from {src}, "
        "includes staircase + 3 threshold portals)"
    )


if __name__ == "__main__":
    main()
