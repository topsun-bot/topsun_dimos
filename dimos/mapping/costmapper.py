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

from dataclasses import asdict
import math
import threading
import time
from typing import Any, Literal

from dimos_lcm.std_msgs import Bool  # type: ignore[import-untyped]
import numpy as np
from pydantic import Field
from reactivex import combine_latest, operators as ops
from reactivex.disposable import Disposable

from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.core.stream import In, Out
from dimos.mapping.pointclouds.occupancy import (
    OCCUPANCY_ALGOS,
    HeightCostConfig,
    OccupancyConfig,
)
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.nav_msgs.OccupancyGrid import OccupancyGrid
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2
from dimos.navigation.diagnostics.sink import TraceSink, isolate_trace_failure
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


class Config(ModuleConfig):
    algo: str = "height_cost"
    config: OccupancyConfig = Field(default_factory=HeightCostConfig)
    # for robots that cant see directly below themself
    initial_safe_radius_meters: float = 0.0
    require_navigation_source_health: bool = False
    # 投影高度带(相对地面基准, 米). 360 度雷达(如 Mid360)会扫到天花板和
    # 高处物体, 这些点不影响通行, 只应在 3D->2D 投影前裁掉; 3D 地图本身
    # (建图/重定位输入)保持完整. 两个值任一为 None 时完全保持旧行为.
    projection_band_below_m: float | None = Field(default=None, ge=0.0)
    projection_band_above_m: float | None = Field(default=None, ge=0.0)
    # 地面基准来源: "odom" 用最新 base_link z 减站高推算(推荐, 对站立/
    # 趴下等启动姿态都成立, 因为 Point-LIO world 原点在 IMU 启动位姿处);
    # "static" 直接使用 ground_static_z(用于回放和调试).
    ground_reference: Literal["odom", "static"] = "odom"
    ground_static_z: float = 0.0
    robot_standing_height_m: float = Field(default=0.30, gt=0.0)


class CostMapper(Module):
    config: Config
    global_map: In[PointCloud2]
    merged_map: In[PointCloud2]
    odom: In[PoseStamped]
    navigation_source_healthy: In[Bool]
    global_costmap: Out[OccupancyGrid]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._navigation_trace = TraceSink("costmapper", config=self.config.g)
        self._trace_costmap_seq = 0
        self._trace_first_blob_saved = False
        self._navigation_source_lock = threading.Lock()
        self._navigation_source_healthy = not self.config.require_navigation_source_health
        self._navigation_source_fault_latched = False
        self._ground_lock = threading.Lock()
        self._latest_base_z: float | None = None
        self._clip_failopen_logged = False
        self._clip_active_logged = False

    @rpc
    def start(self) -> None:
        super().start()

        if self.config.require_navigation_source_health:
            if self.navigation_source_healthy.transport is None:
                raise RuntimeError("CostMapper requires navigation_source_healthy")
            self.register_disposable(
                Disposable(
                    self.navigation_source_healthy.subscribe(self._on_navigation_source_health)
                )
            )

        if self._band_enabled() and self.config.ground_reference == "odom":
            if self.odom.transport is None:
                logger.warning(
                    "CostMapper projection band uses ground_reference='odom' but no odom "
                    "stream is connected; height-band clipping will stay disabled"
                )
            else:
                self.register_disposable(Disposable(self.odom.subscribe(self._on_odom)))

        def _select_map(
            pair: tuple[PointCloud2, PointCloud2 | None],
        ) -> tuple[PointCloud2, str]:
            gmap, merged = pair
            return (merged, "merged_map") if merged is not None else (gmap, "global_map")

        def _publish_costmap(
            grid: OccupancyGrid,
            calc_time_ms: float,
            rx_monotonic: float,
            source: str,
            source_ts: float,
            source_point_count: int,
        ) -> None:
            self.global_costmap.publish(grid)
            self._trace_costmap_published(
                grid,
                calc_time_ms,
                rx_monotonic,
                source,
                source_ts,
                source_point_count,
            )

        def _calculate_and_time(
            selected: tuple[PointCloud2, str],
        ) -> tuple[OccupancyGrid, float, float, str, float, int]:
            msg, source = selected
            rx_monotonic = time.monotonic()  # Capture receipt time
            start = time.perf_counter()
            grid = self._calculate_costmap(msg)
            elapsed_ms = (time.perf_counter() - start) * 1000
            return grid, elapsed_ms, rx_monotonic, source, float(msg.ts), len(msg)

        self.register_disposable(
            combine_latest(
                self.global_map.observable(),  # type: ignore[no-untyped-call]
                self.merged_map.observable().pipe(ops.start_with(None)),  # type: ignore[no-untyped-call,arg-type]
            )
            .pipe(ops.map(_select_map))
            .pipe(ops.filter(lambda _selected: self._map_updates_allowed()))
            .pipe(ops.map(_calculate_and_time))
            .subscribe(
                lambda result: _publish_costmap(
                    result[0],
                    result[1],
                    result[2],
                    result[3],
                    result[4],
                    result[5],
                )
            )
        )

    def _band_enabled(self) -> bool:
        return (
            self.config.projection_band_below_m is not None
            and self.config.projection_band_above_m is not None
        )

    def _on_odom(self, msg: PoseStamped) -> None:
        with self._ground_lock:
            self._latest_base_z = float(msg.position.z)

    def _floor_z(self) -> float | None:
        if self.config.ground_reference == "static":
            return self.config.ground_static_z
        with self._ground_lock:
            base_z = self._latest_base_z
        if base_z is None:
            return None
        return base_z - self.config.robot_standing_height_m

    def _clip_to_travel_band(self, msg: PointCloud2) -> PointCloud2:
        """把通行高度带之外的点(天花板/高处悬空物)从 2D 投影输入里裁掉.

        只影响 costmap 投影, 不影响 3D 地图本身. 地面基准未知时 fail-open,
        宁可暂时用未裁切投影, 也不发布空图卡死规划.
        """
        below = self.config.projection_band_below_m
        above = self.config.projection_band_above_m
        if below is None or above is None:
            return msg
        floor_z = self._floor_z()
        if floor_z is None:
            if not self._clip_failopen_logged:
                self._clip_failopen_logged = True
                logger.warning(
                    "CostMapper has no odometry yet; projecting without the height band"
                )
            return msg
        points = msg.points_f32()
        if len(points) == 0:
            return msg
        z_min = floor_z - below
        z_max = floor_z + above
        keep = (points[:, 2] >= z_min) & (points[:, 2] <= z_max)
        if not self._clip_active_logged:
            self._clip_active_logged = True
            logger.info(
                "CostMapper height band active: [%.2f, %.2f]m (floor_z=%.2f), "
                "keeping %d/%d points",
                z_min,
                z_max,
                floor_z,
                int(keep.sum()),
                len(points),
            )
        if bool(keep.all()):
            return msg
        if not keep.any():
            logger.error(
                "CostMapper height band [%.2f, %.2f] removed all %d points; check "
                "robot_standing_height_m and the ground reference",
                z_min,
                z_max,
                len(points),
            )
        intensities = msg.intensities_f32()
        return PointCloud2.from_numpy(
            points[keep],
            frame_id=msg.frame_id,
            timestamp=msg.ts,
            intensities=intensities[keep] if intensities is not None else None,
        )

    def _map_updates_allowed(self) -> bool:
        with self._navigation_source_lock:
            return self._navigation_source_healthy and not self._navigation_source_fault_latched

    def _on_navigation_source_health(self, msg: Bool) -> None:
        if msg.data:
            with self._navigation_source_lock:
                if not self._navigation_source_fault_latched:
                    self._navigation_source_healthy = True
            return

        with self._navigation_source_lock:
            if self._navigation_source_fault_latched:
                return
            self._navigation_source_healthy = False
            self._navigation_source_fault_latched = True

        # Replace any cached navigable costmap with one unknown cell. The
        # planner is separately fault-latched, but this prevents stale map reuse
        # by visualization or another downstream consumer.
        self.global_costmap.publish(
            OccupancyGrid(
                width=1,
                height=1,
                resolution=self.config.config.resolution,
                frame_id=self.config.config.frame_id or "world",
            )
        )
        logger.error(
            "Navigation source fault latched; replaced cached costmap with a 1x1 unknown "
            "grid. Restart is required."
        )

    @rpc
    def stop(self) -> None:
        super().stop()
        self._navigation_trace.close()

    # @timed()  # TODO: fix thread leak in timed decorator
    def _calculate_costmap(self, msg: PointCloud2) -> OccupancyGrid:
        msg = self._clip_to_travel_band(msg)
        occupancy_function = OCCUPANCY_ALGOS[self.config.algo]
        grid = occupancy_function(msg, **asdict(self.config.config))
        self._apply_initial_safe_radius(grid)
        return grid

    def _apply_initial_safe_radius(self, grid: OccupancyGrid) -> None:
        radius_meters = self.config.initial_safe_radius_meters
        if radius_meters <= 0 or grid.grid.size == 0:
            return

        resolution = grid.resolution
        origin_x = grid.origin.position.x
        origin_y = grid.origin.position.y

        rows, columns = np.ogrid[: grid.grid.shape[0], : grid.grid.shape[1]]
        cell_world_x = columns * resolution + origin_x
        cell_world_y = rows * resolution + origin_y
        distance_squared_meters = cell_world_x**2 + cell_world_y**2

        # Half-cell tolerance: a cell counts as inside if any part of it overlaps
        # the disc. Avoids floating-point boundary flakiness from radius/resolution.
        effective_radius_meters = radius_meters + resolution * 0.5
        safe_mask = distance_squared_meters <= effective_radius_meters**2
        grid.grid[safe_mask] = 0

    def _trace_costmap_published(
        self,
        grid: OccupancyGrid,
        calc_time_ms: float,
        rx_monotonic: float,
        source: str,
        source_ts: float,
        source_point_count: int,
    ) -> None:
        if not self._navigation_trace.accepts("summary"):
            return
        try:
            self._trace_costmap_seq += 1
            costmap_id = f"costmap-{self._trace_costmap_seq:06d}"
            metadata = _costmap_trace_metadata(
                self.config,
                grid,
                costmap_id=costmap_id,
                costmap_sequence=self._trace_costmap_seq,
                source=source,
                source_ts=source_ts,
                source_point_count=source_point_count,
                source_rx_monotonic_sec=rx_monotonic,
                calculation_time_ms=calc_time_ms,
            )
            self._navigation_trace.record(
                "costmap_published",
                metadata,
                estimated_bytes=1280,
            )
            if self._navigation_trace.accepts("full") and not self._trace_first_blob_saved:
                accepted = self._navigation_trace.record_blob(
                    "costmap",
                    grid.grid,
                    {
                        **metadata,
                        "snapshot_reason": "first_costmap_in_worker",
                        "planner_association": "UNKNOWN",
                    },
                    stem=costmap_id,
                )
                if accepted:
                    self._trace_first_blob_saved = True
        except Exception as exc:
            isolate_trace_failure(self._navigation_trace, exc)


def _costmap_trace_metadata(
    config: Config,
    grid: OccupancyGrid,
    *,
    costmap_id: str,
    costmap_sequence: int,
    source: str,
    source_ts: float,
    source_point_count: int,
    source_rx_monotonic_sec: float,
    calculation_time_ms: float,
) -> dict[str, object]:
    return {
        "costmap_id": costmap_id,
        "costmap_sequence": costmap_sequence,
        "source": source,
        "source_ts": source_ts,
        "source_point_count": source_point_count,
        "source_rx_monotonic_sec": source_rx_monotonic_sec,
        "output_ts": float(grid.ts),
        "calculation_time_ms": calculation_time_ms,
        "occupancy_algorithm": config.algo,
        "occupancy_config": asdict(config.config),
        "initial_safe_radius_meters": config.initial_safe_radius_meters,
        "publish_completed": True,
        "frame_id": grid.frame_id,
        "shape": [grid.height, grid.width],
        "dtype": str(grid.grid.dtype),
        "resolution": float(grid.resolution),
        "origin": {
            "x": float(grid.origin.position.x),
            "y": float(grid.origin.position.y),
            "z": float(grid.origin.position.z),
            "yaw": _quaternion_yaw(grid.origin.orientation),
        },
        "origin_rotation_applied_by_grid_helpers": False,
        "fingerprint": {
            "source_ts": source_ts,
            "shape": [grid.height, grid.width],
            "resolution": float(grid.resolution),
            "origin_x": float(grid.origin.position.x),
            "origin_y": float(grid.origin.position.y),
        },
    }


def _quaternion_yaw(orientation: Any) -> float:
    x = float(orientation.x)
    y = float(orientation.y)
    z = float(orientation.z)
    w = float(orientation.w)
    return math.atan2(
        2.0 * (w * z + x * y),
        1.0 - 2.0 * (y * y + z * z),
    )
