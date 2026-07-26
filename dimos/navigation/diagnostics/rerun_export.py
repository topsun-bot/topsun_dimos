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

"""Rerun export kept entirely out of online robot workers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import NDArray
import rerun as rr

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dimos.navigation.diagnostics.report import (
        ParsedTrace,
        PlanAnalysis,
        SessionWindow,
    )


def export_rerun(
    output_path: Path,
    plans: Sequence[PlanAnalysis],
    *,
    trace: ParsedTrace,
    session: SessionWindow,
) -> None:
    """Save paths, odom estimates, commands, costmaps, and pointclouds."""
    recording = rr.RecordingStream("dimos_navigation_diagnostics")
    for plan in plans:
        root = f"navigation/plan_{plan.plan_version:04d}"
        if plan.raw_path is not None:
            recording.log(
                f"{root}/raw_path",
                rr.LineStrips2D([plan.raw_path], colors=[80, 130, 220]),
                static=True,
            )
        if plan.smoothed_path is not None:
            recording.log(
                f"{root}/smoothed_path",
                rr.LineStrips2D([plan.smoothed_path], colors=[30, 200, 120]),
                static=True,
            )
        if len(plan.odom_xy):
            recording.log(
                f"{root}/odom_estimate",
                rr.LineStrips2D([plan.odom_xy], colors=[240, 120, 40]),
                static=True,
            )
            recording.log(
                f"{root}/odom_samples",
                rr.Points2D(plan.odom_xy, radii=0.015, colors=[240, 120, 40]),
                static=True,
            )
        for timestamp, angular_z in zip(plan.command_ts, plan.command_angular_z, strict=True):
            recording.set_time("monotonic", duration=float(timestamp))
            recording.log(
                f"{root}/command/angular_z",
                rr.Scalars(float(angular_z)),
            )
    _log_blobs(recording, trace, session)
    recording.save(output_path)


def _log_blobs(
    recording: rr.RecordingStream,
    trace: ParsedTrace,
    session: SessionWindow,
) -> None:
    from dimos.navigation.diagnostics.report import _in_session

    for sequence, event in enumerate(trace.events):
        if event.get("event") != "blob_saved" or not _in_session(event, session):
            continue
        relative = event.get("blob_path")
        if not isinstance(relative, str):
            continue
        path = (trace.navigation_dir / relative).resolve()
        try:
            path.relative_to(trace.navigation_dir)
            array = np.load(path, allow_pickle=False)
        except (OSError, ValueError):
            continue
        timestamp = float(event.get("monotonic_ns", 0)) / 1_000_000_000
        recording.set_time("monotonic", duration=timestamp)
        kind = event.get("blob_kind")
        if kind == "pointcloud" and array.ndim == 2 and array.shape[1] >= 3:
            recording.log(
                f"navigation/evidence/pointcloud_{sequence:06d}",
                rr.Points3D(array[:, :3], radii=0.025, colors=[80, 180, 255]),
            )
        elif kind == "costmap" and array.ndim == 2:
            metadata = event.get("metadata")
            points = _costmap_points(
                array,
                metadata if isinstance(metadata, dict) else {},
            )
            if len(points["occupied"]):
                recording.log(
                    f"navigation/evidence/costmap_{sequence:06d}/occupied",
                    rr.Points2D(points["occupied"], radii=0.035, colors=[240, 50, 40]),
                )
            if len(points["inflated"]):
                recording.log(
                    f"navigation/evidence/costmap_{sequence:06d}/inflated",
                    rr.Points2D(points["inflated"], radii=0.025, colors=[250, 180, 30]),
                )


def _costmap_points(
    grid: NDArray[Any],
    metadata: dict[str, object],
) -> dict[str, NDArray[np.float64]]:
    values = np.asarray(grid)
    costmap_metadata = metadata.get("costmap")
    geometry = costmap_metadata if isinstance(costmap_metadata, dict) else metadata
    resolution = _numeric(geometry.get("resolution"), 1.0)
    origin = geometry.get("origin")
    origin_x = origin_y = yaw = 0.0
    if isinstance(origin, dict):
        position = origin.get("position")
        if isinstance(position, dict):
            origin_x = _numeric(position.get("x"))
            origin_y = _numeric(position.get("y"))
            yaw = _numeric(origin.get("yaw"))
        else:
            origin_x = _numeric(origin.get("x"))
            origin_y = _numeric(origin.get("y"))
            yaw = _numeric(origin.get("yaw"))

    def convert(mask: NDArray[np.bool_]) -> NDArray[np.float64]:
        rows, columns = np.nonzero(mask)
        local = np.column_stack(((columns + 0.5) * resolution, (rows + 0.5) * resolution))
        if yaw:
            cosine = np.cos(yaw)
            sine = np.sin(yaw)
            rotation = np.array([[cosine, -sine], [sine, cosine]])
            local = local @ rotation.T
        local[:, 0] += origin_x
        local[:, 1] += origin_y
        return local

    return {
        "occupied": convert(values >= 100),
        "inflated": convert((values > 0) & (values < 100)),
    }


def _numeric(value: object, default: float = 0.0) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return default
