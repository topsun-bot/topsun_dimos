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

import threading
import time

import numpy as np
from reactivex.disposable import Disposable

from dimos.agents.annotation import skill
from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.core.stream import In, Out
from dimos.msgs.geometry_msgs import PoseStamped, Quaternion, Vector3
from dimos.msgs.geometry_msgs.Vector3 import make_vector3
from dimos.msgs.sensor_msgs import PointCloud2
from dimos.slam.types import PoseGraphNode, ScanMatchResult
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

try:
    import open3d as o3d

    O3D_AVAILABLE = True
except ImportError:
    O3D_AVAILABLE = False


def _pose_to_matrix(pose: PoseStamped) -> np.ndarray:
    from scipy.spatial.transform import Rotation

    q = pose.orientation
    rot = Rotation.from_quat([q.x, q.y, q.z, q.w]).as_matrix()
    T = np.eye(4)
    T[:3, :3] = rot
    T[0, 3] = pose.position.x
    T[1, 3] = pose.position.y
    T[2, 3] = pose.position.z
    return T


def _matrix_to_pose(T: np.ndarray, frame_id: str = "map") -> PoseStamped:
    from scipy.spatial.transform import Rotation

    r = Rotation.from_matrix(T[:3, :3])
    q = r.as_quat()
    return PoseStamped(
        position=make_vector3(T[0, 3], T[1, 3], T[2, 3]),
        orientation=Quaternion(q[0], q[1], q[2], q[3]),
        frame_id=frame_id,
    )


class LidarSlamModule(Module):
    lidar: In[PointCloud2]
    raw_odom: In[PoseStamped]

    slam_pose: Out[PoseStamped]
    lidar_corrected: Out[PointCloud2]

    def __init__(
        self,
        voxel_size: float = 0.1,
        keyframe_distance: float = 0.5,
        icp_max_distance: float = 1.0,
        loop_closure_enabled: bool = True,
        loop_closure_interval: int = 30,
    ) -> None:
        super().__init__()
        self._voxel_size = voxel_size
        self._keyframe_distance = keyframe_distance
        self._icp_max_distance = icp_max_distance
        self._loop_closure_enabled = loop_closure_enabled
        self._loop_closure_interval = loop_closure_interval

        self._lock = threading.Lock()
        self._current_transform = np.eye(4)
        self._raw_odom_pose: PoseStamped | None = None
        self._submap: o3d.geometry.PointCloud | None = None
        self._pose_graph_nodes: list[PoseGraphNode] = []
        self._last_keyframe_transform = np.eye(4)
        self._scan_count = 0
        self._total_fitness = 0.0
        self._match_count = 0
        self._last_scan_time = 0.0

    @rpc
    def start(self) -> None:
        super().start()
        if not O3D_AVAILABLE:
            logger.warning("open3d not available, SLAM will pass through raw odometry")

        self._disposables.add(Disposable(self.raw_odom.subscribe(self._on_odom)))
        self._disposables.add(Disposable(self.lidar.subscribe(self._on_lidar)))

    @rpc
    def stop(self) -> None:
        super().stop()

    def _on_odom(self, odom: PoseStamped) -> None:
        with self._lock:
            self._raw_odom_pose = odom

    def _on_lidar(self, frame: PointCloud2) -> None:
        if time.time() - self._last_scan_time < 0.05:
            return
        self._last_scan_time = time.time()

        with self._lock:
            raw_odom = self._raw_odom_pose
        if raw_odom is None:
            return

        try:
            result = self._scan_match(frame, raw_odom)
        except Exception:
            logger.warning("SLAM scan matching failed, using raw odom", exc_info=True)
            result = None

        if result is not None:
            self._current_transform = result.transformation
            self._total_fitness += result.fitness
            self._match_count += 1
        else:
            self._current_transform = _pose_to_matrix(raw_odom)

        corrected_pose = _matrix_to_pose(self._current_transform)
        self.slam_pose.publish(corrected_pose)

        self._scan_count += 1

        if self._submap is not None and frame.pointcloud is not None:
            try:
                pc = self._to_o3d(frame.pointcloud)
                if pc is not None and not pc.is_empty():
                    corrected_pc = pc.transform(self._current_transform)
                    self.lidar_corrected.publish(
                        PointCloud2(
                            pointcloud=corrected_pc,
                            frame_id=frame.frame_id,
                            ts=frame.ts,
                        )
                    )
            except Exception:
                pass

    def _scan_match(self, frame: PointCloud2, raw_odom: PoseStamped) -> ScanMatchResult | None:
        if not O3D_AVAILABLE:
            return None

        pc = self._to_o3d(frame.pointcloud)
        if pc is None or pc.is_empty():
            return None

        current_scan = pc.voxel_downsample(self._voxel_size)
        odom_guess = _pose_to_matrix(raw_odom)

        if self._submap is None or len(self._submap.points) < 50:
            self._submap = current_scan
            self._last_keyframe_transform = odom_guess
            self._pose_graph_nodes.append(
                PoseGraphNode(index=0, pose=raw_odom, cumulative_transform=odom_guess.copy())
            )
            return ScanMatchResult(fitness=1.0, rmse=0.0, transformation=odom_guess)

        result = o3d.pipelines.registration.registration_icp(
            current_scan,
            self._submap,
            self._icp_max_distance,
            odom_guess,
            o3d.pipelines.registration.TransformationEstimationPointToPoint(),
        )

        corrected_transform = result.transformation

        dist_from_keyframe = np.linalg.norm(
            corrected_transform[:3, 3] - self._last_keyframe_transform[:3, 3]
        )
        if dist_from_keyframe > self._keyframe_distance:
            self._submap = self._submap + current_scan.transform(corrected_transform)
            self._last_keyframe_transform = corrected_transform.copy()
            node_idx = len(self._pose_graph_nodes)
            self._pose_graph_nodes.append(
                PoseGraphNode(
                    index=node_idx,
                    pose=_matrix_to_pose(corrected_transform),
                    cumulative_transform=corrected_transform.copy(),
                )
            )

            if self._submap.has_points() and len(self._submap.points) > 50000:
                self._submap = self._submap.voxel_downsample(self._voxel_size * 2)

        if (
            self._loop_closure_enabled
            and self._scan_count % self._loop_closure_interval == 0
            and len(self._pose_graph_nodes) > 5
        ):
            self._attempt_loop_closure(current_scan, corrected_transform)

        return ScanMatchResult(
            fitness=result.fitness,
            rmse=result.inlier_rmse,
            transformation=corrected_transform,
        )

    def _attempt_loop_closure(self, scan: "o3d.geometry.PointCloud", current_transform: np.ndarray) -> None:
        try:
            older_nodes = self._pose_graph_nodes[:-3]
            if not older_nodes:
                return

            best_fitness = 0.0
            best_transform = None
            for node in older_nodes[-5:]:
                initial_guess = current_transform @ np.linalg.inv(node.cumulative_transform)
                initial_guess[:3, 3] = node.cumulative_transform[:3, 3]
                result = o3d.pipelines.registration.registration_icp(
                    scan,
                    self._submap,
                    self._icp_max_distance * 2,
                    initial_guess,
                    o3d.pipelines.registration.TransformationEstimationPointToPoint(),
                )
                if result.fitness > best_fitness and result.fitness > 0.7:
                    best_fitness = result.fitness
                    best_transform = result.transformation

            if best_transform is not None and best_fitness > 0.8:
                drift = current_transform - best_transform
                correction = np.eye(4)
                correction[:3, :3] = best_transform[:3, :3] @ np.linalg.inv(current_transform[:3, :3])
                correction[:3, 3] = best_transform[:3, 3] - current_transform[:3, 3]
                self._current_transform = best_transform.copy()
                logger.info(f"Loop closure accepted, fitness={best_fitness:.3f}")
        except Exception:
            logger.debug("Loop closure attempt failed", exc_info=True)

    def _to_o3d(self, pointcloud: object) -> "o3d.geometry.PointCloud | None":
        if pointcloud is None:
            return None
        if isinstance(pointcloud, o3d.geometry.PointCloud):
            return pointcloud
        if isinstance(pointcloud, o3d.t.geometry.PointCloud):
            return pointcloud.to_legacy()
        if isinstance(pointcloud, np.ndarray):
            pcd = o3d.geometry.PointCloud()
            pcd.points = o3d.utility.Vector3dVector(pointcloud.astype(np.float64))
            return pcd
        return None

    @rpc
    def get_pose(self) -> PoseStamped:
        with self._lock:
            return _matrix_to_pose(self._current_transform)

    @rpc
    def get_quality_metrics(self) -> dict:
        with self._lock:
            avg_fitness = self._total_fitness / self._match_count if self._match_count > 0 else 0.0
            return {
                "avg_fitness": avg_fitness,
                "submap_points": len(self._submap.points) if self._submap else 0,
                "keyframe_count": len(self._pose_graph_nodes),
                "scan_count": self._scan_count,
            }

    @rpc
    def reset(self) -> bool:
        with self._lock:
            self._current_transform = np.eye(4)
            self._submap = None
            self._pose_graph_nodes = []
            self._last_keyframe_transform = np.eye(4)
            self._scan_count = 0
            self._total_fitness = 0.0
            self._match_count = 0
        return True

    @skill
    def check_localization_quality(self) -> str:
        """Check the quality of the SLAM localization.

        Returns a human-readable assessment of how well the robot is localized,
        including match quality, submap size, and number of keyframes.

        Returns:
            str: SLAM quality assessment
        """
        metrics = self.get_quality_metrics()
        fitness = metrics["avg_fitness"]
        keyframes = metrics["keyframe_count"]
        submap_pts = metrics["submap_points"]

        if not O3D_AVAILABLE:
            return "SLAM: running in passthrough mode (open3d not available). Using raw odometry only."

        if fitness > 0.8:
            quality = "excellent"
        elif fitness > 0.5:
            quality = "good"
        elif fitness > 0.3:
            quality = "degraded"
        else:
            quality = "poor"

        return (
            f"SLAM quality: {quality}. "
            f"Match fitness: {fitness:.3f}, "
            f"submap: {submap_pts} points, "
            f"keyframes: {keyframes}, "
            f"scans processed: {metrics['scan_count']}."
        )


lidar_slam = LidarSlamModule.blueprint

__all__ = ["LidarSlamModule", "lidar_slam"]
