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

import json
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np
from reactivex.disposable import Disposable

from dimos.agents.annotation import skill
from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.core.stream import In, Out
from dimos.msgs.geometry_msgs import PoseStamped
from dimos.msgs.geometry_msgs.Vector3 import make_vector3
from dimos.msgs.nav_msgs import OccupancyGrid
from dimos.msgs.sensor_msgs import PointCloud2
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

try:
    from dimos_lcm.std_msgs import String
except ImportError:
    String = str


class PatrolMapManager(Module):
    slam_pose: In[PoseStamped]
    global_costmap: In[OccupancyGrid]
    global_map: In[PointCloud2]

    patrol_costmap: Out[OccupancyGrid]
    map_metadata: Out[String]

    def __init__(
        self,
        map_save_dir: str = "~/.dimos/maps",
        auto_save_interval: float = 60.0,
        min_coverage_percent: float = 80.0,
    ) -> None:
        super().__init__()
        self._map_save_dir = Path(map_save_dir).expanduser()
        self._auto_save_interval = auto_save_interval
        self._min_coverage_percent = min_coverage_percent

        self._lock = threading.Lock()
        self._latest_costmap: OccupancyGrid | None = None
        self._latest_pose: PoseStamped | None = None
        self._last_save_time = 0.0
        self._map_changed = False
        self._auto_save_name = "autosave"
        self._auto_save_timer: threading.Timer | None = None

    @rpc
    def start(self) -> None:
        super().start()
        self._disposables.add(Disposable(self.global_costmap.subscribe(self._on_costmap)))
        self._disposables.add(Disposable(self.slam_pose.subscribe(self._on_pose)))

    @rpc
    def stop(self) -> None:
        if self._auto_save_timer is not None:
            self._auto_save_timer.cancel()
        if self._map_changed and self._latest_costmap is not None:
            self._save_map_internal(self._auto_save_name)
        super().stop()

    def _on_pose(self, pose: PoseStamped) -> None:
        with self._lock:
            self._latest_pose = pose

    def _on_costmap(self, costmap: OccupancyGrid) -> None:
        with self._lock:
            self._latest_costmap = costmap
            self._map_changed = True

        try:
            self.patrol_costmap.publish(costmap)
        except Exception:
            pass

        metadata = self._build_metadata(costmap)
        try:
            self.map_metadata.publish(json.dumps(metadata))
        except Exception:
            pass

        now = time.time()
        if now - self._last_save_time >= self._auto_save_interval:
            self._save_map_internal(self._auto_save_name)
            self._last_save_time = now
            self._map_changed = False

    def _build_metadata(self, costmap: OccupancyGrid) -> dict:
        return {
            "width": costmap.width,
            "height": costmap.height,
            "resolution": costmap.resolution,
            "free_percent": round(costmap.free_percent, 1),
            "occupied_percent": round(costmap.occupied_percent, 1),
            "unknown_percent": round(costmap.unknown_percent, 1),
            "total_area_m2": round(costmap.width * costmap.height * costmap.resolution**2, 2),
            "ts": costmap.ts,
        }

    def _get_map_dir(self, name: str) -> Path:
        return self._map_save_dir / name

    def _save_map_internal(self, name: str) -> bool:
        with self._lock:
            costmap = self._latest_costmap
        if costmap is None:
            return False

        try:
            map_dir = self._get_map_dir(name)
            map_dir.mkdir(parents=True, exist_ok=True)

            np.save(map_dir / "costmap.npy", costmap.grid)

            metadata = {
                "name": name,
                "width": costmap.width,
                "height": costmap.height,
                "resolution": costmap.resolution,
                "frame_id": costmap.frame_id,
                "origin": {
                    "x": costmap.origin.position.x,
                    "y": costmap.origin.position.y,
                    "z": costmap.origin.position.z,
                },
                "coverage": {
                    "free_percent": round(costmap.free_percent, 1),
                    "occupied_percent": round(costmap.occupied_percent, 1),
                    "unknown_percent": round(costmap.unknown_percent, 1),
                },
                "total_area_m2": round(costmap.width * costmap.height * costmap.resolution**2, 2),
                "saved_at": datetime.now().isoformat(),
            }
            with open(map_dir / "metadata.json", "w") as f:
                json.dump(metadata, f, indent=2)

            logger.info(f"Saved map '{name}' to {map_dir}")
            return True
        except Exception:
            logger.error(f"Failed to save map '{name}'", exc_info=True)
            return False

    @rpc
    def save_map(self, name: str) -> bool:
        return self._save_map_internal(name)

    @rpc
    def load_map(self, name: str) -> bool:
        map_dir = self._get_map_dir(name)
        if not (map_dir / "costmap.npy").exists():
            logger.error(f"Map '{name}' not found at {map_dir}")
            return False

        try:
            grid = np.load(map_dir / "costmap.npy")
            with open(map_dir / "metadata.json") as f:
                metadata = json.load(f)

            from dimos.msgs.geometry_msgs import Pose

            costmap = OccupancyGrid(
                grid=grid,
                resolution=metadata.get("resolution", 0.05),
                origin=Pose(
                    position=make_vector3(
                        metadata.get("origin", {}).get("x", 0),
                        metadata.get("origin", {}).get("y", 0),
                        metadata.get("origin", {}).get("z", 0),
                    )
                ),
                frame_id=metadata.get("frame_id", "world"),
            )

            with self._lock:
                self._latest_costmap = costmap

            try:
                self.patrol_costmap.publish(costmap)
            except Exception:
                pass
            logger.info(f"Loaded map '{name}' from {map_dir}")
            return True
        except Exception:
            logger.error(f"Failed to load map '{name}'", exc_info=True)
            return False

    @rpc
    def list_maps(self) -> list[str]:
        if not self._map_save_dir.exists():
            return []
        return [
            d.name
            for d in self._map_save_dir.iterdir()
            if d.is_dir() and (d / "metadata.json").exists()
        ]

    @rpc
    def get_map_stats(self) -> dict:
        with self._lock:
            costmap = self._latest_costmap
        if costmap is None:
            return {"error": "no map available"}
        return self._build_metadata(costmap)

    @rpc
    def is_map_complete(self) -> bool:
        with self._lock:
            costmap = self._latest_costmap
        if costmap is None:
            return False
        return costmap.free_percent >= self._min_coverage_percent

    @skill
    def save_current_map(self, name: str) -> str:
        """Save the current map with a given name for later use.

        Args:
            name (str): Name to save the map under

        Returns:
            str: Outcome of the save operation
        """
        with self._lock:
            costmap = self._latest_costmap
        if costmap is None:
            return "No map data available to save."

        success = self.save_map(name)
        if success:
            area = costmap.width * costmap.height * costmap.resolution**2
            return (
                f"Saved map '{name}' "
                f"({area:.1f} m² explored, "
                f"{costmap.free_percent:.0f}% coverage)."
            )
        return f"Failed to save map '{name}'."

    @skill
    def load_saved_map(self, name: str) -> str:
        """Load a previously saved map for patrol navigation.

        Args:
            name (str): Name of the map to load

        Returns:
            str: Outcome of the load operation
        """
        maps = self.list_maps()
        if name not in maps:
            return f"Map '{name}' not found. Available maps: {', '.join(maps) if maps else 'none'}."

        map_dir = self._get_map_dir(name)
        try:
            with open(map_dir / "metadata.json") as f:
                metadata = json.load(f)
        except Exception:
            metadata = {}

        success = self.load_map(name)
        if success:
            saved_at = metadata.get("saved_at", "unknown date")
            return f"Loaded map '{name}' from {saved_at}."
        return f"Failed to load map '{name}'."

    @skill
    def check_map_coverage(self) -> str:
        """Check how much of the environment has been mapped.

        Returns:
            str: Map coverage statistics
        """
        with self._lock:
            costmap = self._latest_costmap
        if costmap is None:
            return "No map data available."

        quality = "good" if costmap.free_percent >= self._min_coverage_percent else "needs more exploration"
        return (
            f"Map coverage: {costmap.free_percent:.1f}% free, "
            f"{costmap.occupied_percent:.1f}% obstacles, "
            f"{costmap.unknown_percent:.1f}% unknown. "
            f"Map quality: {quality}."
        )

    @skill
    def list_available_maps(self) -> str:
        """List all saved maps available for loading.

        Returns:
            str: List of available maps with metadata
        """
        maps = self.list_maps()
        if not maps:
            return "No saved maps available."

        entries = []
        for name in maps:
            map_dir = self._get_map_dir(name)
            try:
                with open(map_dir / "metadata.json") as f:
                    metadata = json.load(f)
                saved_at = metadata.get("saved_at", "unknown")
                area = metadata.get("total_area_m2", 0)
                entries.append(f"{name} ({area:.1f} m², saved {saved_at[:10]})")
            except Exception:
                entries.append(f"{name} (metadata unavailable)")

        return "Available maps: " + ", ".join(entries) + "."


patrol_map_manager = PatrolMapManager.blueprint

__all__ = ["PatrolMapManager", "patrol_map_manager"]
