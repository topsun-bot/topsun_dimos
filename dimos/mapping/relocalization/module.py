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

from datetime import datetime
import json
from pathlib import Path
import re
import time
from typing import Any, Literal

import numpy as np
import reactivex as rx
from reactivex import Subject, combine_latest, operators as ops

from dimos.constants import DIMOS_PROJECT_ROOT
from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.core.stream import In, Out
from dimos.mapping.relocalization.relocalize import (
    relocalize as _relocalize,
    relocalize_with_initial as _relocalize_with_initial,
)
from dimos.mapping.voxels import VoxelGrid
from dimos.msgs.geometry_msgs.Quaternion import Quaternion
from dimos.msgs.geometry_msgs.Transform import Transform
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2
from dimos.utils.data import resolve_named_path
from dimos.utils.logging_config import setup_logger
from dimos.utils.reactive import backpressure

logger = setup_logger()

# 预地图使用的坐标系：离线导出的 premap 都被归一到 map 坐标系下。
FRAME_MAP = "map"
# 当前启动后实时建图使用的坐标系：Go2 本次运行的局部世界坐标系。
FRAME_WORLD = "world"

# 周期性发布 loaded_map 和 TF 的间隔，避免高频重复刷可视化/TF。
PUBLISH_INTERVAL = 2.0  # for loaded_map + TF
# 两次重定位尝试之间的最小间隔，防止 RANSAC/ICP 持续占满 CPU。
RELOC_INTERVAL = 2.0
# 当前局部地图点数不足时，局部几何约束太少，先不做全局匹配。
MIN_LOCAL_POINTS = 50_000
# premap 文件后缀；CLI 里传 stem 时会自动补这个后缀。
MAP_SUFFIX = ".pc2.lcm"


class Config(ModuleConfig):
    # 离线 premap 的文件名或路径，例如通过
    # `-o relocalizationmodule.map_file=recording_go2` 传入。
    map_file: str | None = (
        None  # e.g. `-o relocalizationmodule.map_file=go2_hongkong_office_twopass_map`
    )
    # 是否把加载的原始 premap 也发布出去，主要用于调试/可视化。
    publish_loaded_map: bool = False
    # relocalize() 返回的匹配质量阈值；低于该值说明候选配准不可信。
    fitness_threshold: float = 0.75
    # merge 时是否用列雕刻：local 当前观测覆盖 premap 的同 XY 列旧点。
    use_carving: bool = True
    # 快速 ICP 总开关；关闭后所有重定位都保持原全局 RANSAC 流程。
    fast_icp_enabled: bool = False
    # 启动时是否读取上一次独立运行保存的 latest.json。
    load_cached_transform_on_start: bool = True
    # 每次运行是否把第一次成功发布的 TF 持久化到 JSON。
    save_first_transform_json: bool = True
    # 相对路径按仓库根目录解析，并按 map_file 再分子目录。
    cached_transform_dir: str = "jiangtao/cache/relocalization_tf"
    # 可选显式 latest 文件；为空时使用 cached_transform_dir/map/latest.json。
    cached_transform_latest_file: str | None = None
    # 同一次运行第一次发布之后，选择快速 ICP 或原全局匹配。
    subsequent_relocalization_mode: Literal["fast_icp", "global"] = "global"
    # 快速 ICP 拒绝后是否允许回退到全局 RANSAC。
    fast_icp_fallback_global: bool = True
    # 快速 ICP 的最大对应距离，单位为米。
    fast_icp_max_dist: float = 0.3
    # 按需求与当前 final ICP 一致，默认最多迭代 50 次。
    fast_icp_max_iter: int = 50
    # 按缓存初值裁剪 premap 时，在 local AABB 外扩的距离，单位为米。
    fast_icp_crop_radius: float = 8.0
    # 为空时复用 fitness_threshold，便于先保持统一验收标准。
    fast_icp_min_fitness: float | None = None
    # 有跨运行 JSON 初值时，1 万点即可开始本次运行的第一次快速匹配。
    cached_start_min_local_points: int = 10_000
    # 同一次运行后续选择快速 ICP 时使用的点数门槛。
    fast_icp_min_local_points: int = 10_000
    # 没有缓存或选择全局 RANSAC 时仍保持原来的 5 万点门槛。
    min_local_points: int = MIN_LOCAL_POINTS


class RelocalizationModule(Module):
    # 输入：实时累积的当前局部/本次启动地图，通常由 VoxelGridMapper 发布。
    config: Config
    global_map: In[PointCloud2]
    # 输出：可选发布原始 premap，便于在 Rerun 中看旧地图本体。
    loaded_map: Out[PointCloud2]
    # 输出：把 premap 变换到当前 world 后，与 local 融合得到的导航地图。
    merged_map: Out[PointCloud2]

    def __init__(self, **kwargs: Any) -> None:
        # 初始化 Module 基类，完成配置解析、RPC/TF 等基础设施设置。
        super().__init__(**kwargs)
        # 缓存离线加载的 premap；没有 map_file 时保持 None，模块相当于禁用。
        self._premap: PointCloud2 | None = None
        # 控制“点数不够，跳过重定位”日志频率，避免每帧刷屏。
        self._last_skip_log = 0.0
        # 保存并广播最近一次成功的 world <- map 变换；merge 和 TF 发布都依赖它。
        self._world_to_map: Subject[Transform | None] = Subject()
        # 算法内部统一保存 map <- world，快速 ICP 可以直接拿它当初值。
        self._last_T_map_world: np.ndarray | None = None
        # 保存最近一次由算法确认过的 world <- map 消息，不会直接发布磁盘旧值。
        self._last_world_to_map_tf: Transform | None = None
        # 区分矩阵是否来自上一次独立运行；只有该状态允许 1 万点启动快配。
        self._loaded_T_map_world_from_json = False
        # 同一次进程中是否已经真正向 _world_to_map 发布过成功 TF。
        self._has_published_tf_this_run = False
        # 防止同一次运行每 2 秒重复覆盖“首次成功”JSON。
        self._first_published_tf_saved = False
        # 算法成功后暂存保存元数据，由 _publish_tf 在真正发布后写磁盘。
        self._pending_cache_record: tuple[np.ndarray, float, int, str] | None = None
        # 连续失败次数只用于日志诊断，不自动清空缓存。
        self._fast_icp_fail_count = 0

    @rpc
    def start(self) -> None:
        # 先启动 Module 基类，确保流、RPC 和生命周期资源已经就绪。
        super().start()

        # 没有配置 premap 时，不注册任何重定位/merge 订阅，避免误用空地图。
        if not self.config.map_file:
            logger.info("Relocalization module disabled (no map_file configured)")
            return

        # 将 CLI 传入的名字解析成实际 .pc2.lcm 路径，支持 cwd/repo/data 查找。
        path = resolve_named_path(self.config.map_file, MAP_SUFFIX)
        # 从 LCM 二进制文件反序列化出 PointCloud2，作为离线 premap。
        self._premap = PointCloud2.lcm_decode(path.read_bytes())
        # 强制设为 map 坐标系，后续配准得到的是 map <- world。
        self._premap.frame_id = FRAME_MAP

        # 磁盘缓存只作为 ICP 初值，必须经过本次点云验证后才允许发布 TF。
        self._load_cached_transform_on_start()

        # 订阅实时 global_map：节流、点数满足要求后，执行重定位并发布 TF。
        self.register_disposable(
            # backpressure：限流机制，只保留最新消息，丢掉中间积压消息，如 global_map 的最新消息。
            backpressure(
                # observable() = 把 global_map 这个输入口收到的所有消息，变成一个可以继续 .pipe() 和 .subscribe() 的实时数据流
                self.global_map.observable().pipe(  # type: ignore[no-untyped-call]
                    # 每 RELOC_INTERVAL 秒最多放行一次重定位任务。
                    ops.throttle_first(RELOC_INTERVAL),
                    # 点数不够时打限频日志，方便判断是在等待建图还是算法慢。
                    ops.do_action(self._maybe_log_skip),
                    # 只有局部地图足够大时才进入昂贵的 RANSAC/ICP。
                    ops.filter(self._has_enough_points),
                )
            )
            # 对每个满足条件的局部地图调用 _try_relocalize()。
            .pipe(ops.map(self._try_relocalize))
            # 成功时把 Transform 写入 _world_to_map，失败时 None 会被忽略。
            .subscribe(self._publish_tf)
        )

        # 订阅实时 global_map 与最近的 world<-map TF，用于构造 merged_map。
        self.register_disposable(
            backpressure(
                combine_latest(
                    # local：当前本次启动累计出来的地图。
                    self.global_map.observable(),  # type: ignore[no-untyped-call]
                    # tf：成功重定位前先给 None，让 merge 分支知道暂时不能合并。
                    self._world_to_map.pipe(ops.start_with(None)),
                )
            ).subscribe(self._on_merge_input)
        )

        # 周期性发布 TF 和可选 loaded_map，让可视化/下游长期拿到最近结果。
        self.register_disposable(
            rx.interval(PUBLISH_INTERVAL)
            .pipe(ops.with_latest_from(self._world_to_map))
            .subscribe(self._publish_periodic)
        )

        logger.info(
            f"Relocalization module started: map_file={self.config.map_file!r}  "
            f"loaded_map.frame_id={self._premap.frame_id!r}"
        )

    @staticmethod
    def _sanitize_map_key(map_file: str) -> str:
        """Build a filesystem-safe cache key from a configured map name."""
        # 只取文件名，避免绝对路径中的目录层级进入缓存目录。
        name = Path(map_file).name
        # 正常的 .pc2.lcm 文件去掉完整后缀，CLI stem 则保持原样。
        if name.endswith(MAP_SUFFIX):
            name = name[: -len(MAP_SUFFIX)]
        # 非字母、数字、点、下划线和短横线统一替换，防止路径注入。
        sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
        return sanitized or "default"

    def _map_cache_key(self) -> str:
        # start() 只有 map_file 非空才调用缓存逻辑，这里保留 default 便于单测和容错。
        return self._sanitize_map_key(self.config.map_file or "default")

    @rpc
    def get_current_map_key(self) -> str | None:
        """Return the stable key for the configured preloaded map."""
        if not self.config.map_file:
            return None
        return self._map_cache_key()

    @rpc
    def get_current_map_file(self) -> str | None:
        """Return the configured map file/stem used by this relocalization module."""
        return self.config.map_file

    @rpc
    def get_world_to_map(self) -> Transform | None:
        """Return the latest validated world<-map transform, if available."""
        return self._last_world_to_map_tf

    @rpc
    def is_relocalized(self) -> bool:
        """Whether this run has published a validated world<-map transform."""
        return self._last_world_to_map_tf is not None and self._has_published_tf_this_run

    @staticmethod
    def _resolve_cache_path(value: str) -> Path:
        # 用户给绝对路径时原样使用；相对路径固定相对仓库根目录，而非启动 cwd。
        path = Path(value).expanduser()
        if path.is_absolute():
            return path
        return DIMOS_PROJECT_ROOT / path

    def _map_cache_dir(self) -> Path:
        # 不同 premap 放入不同子目录，避免 latest.json 互相覆盖。
        root = self._resolve_cache_path(self.config.cached_transform_dir)
        return root / self._map_cache_key()

    def _latest_transform_path(self) -> Path:
        # 显式配置主要用于现场指定某个缓存文件；否则读取地图目录里的 latest。
        if self.config.cached_transform_latest_file:
            return self._resolve_cache_path(self.config.cached_transform_latest_file)
        return self._map_cache_dir() / "latest.json"

    @staticmethod
    def _validate_T_map_world(raw_matrix: Any) -> np.ndarray:
        """Validate and normalize a JSON matrix before it reaches Open3D."""
        try:
            matrix = np.asarray(raw_matrix, dtype=float)
        except (TypeError, ValueError) as exc:
            raise ValueError("T_map_world must contain only numbers") from exc
        # 只接受有限 4x4 齐次矩阵，防止坏 JSON 让 ICP 或求逆产生 NaN。
        if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
            raise ValueError("T_map_world must be a finite 4x4 matrix")
        if not np.allclose(matrix[3], np.array([0.0, 0.0, 0.0, 1.0])):
            raise ValueError("T_map_world must have a homogeneous last row")
        return matrix

    def _load_latest_T_map_world(self) -> np.ndarray | None:
        """Load a map-specific cached transform; invalid files are ignored."""
        path = self._latest_transform_path()
        # 第一次运行没有 latest 是正常状态，随后会自然进入全局 RANSAC。
        if not path.exists():
            logger.info(f"No relocalization transform cache found: path={path}")
            return None

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("cache root must be a JSON object")
            if payload.get("schema_version") != 1:
                raise ValueError("unsupported schema_version")
            if payload.get("frame") != "map<-world":
                raise ValueError("cached matrix frame must be map<-world")

            # map_key 优先；兼容手工创建但只写了 map_file 的 schema v1 文件。
            cached_key = payload.get("map_key")
            if cached_key is None and isinstance(payload.get("map_file"), str):
                cached_key = self._sanitize_map_key(payload["map_file"])
            if cached_key != self._map_cache_key():
                raise ValueError(
                    f"cache map_key={cached_key!r} does not match "
                    f"current map_key={self._map_cache_key()!r}"
                )
            return self._validate_T_map_world(payload.get("T_map_world"))
        except (OSError, json.JSONDecodeError, ValueError):
            # 缓存损坏不能影响机器人启动；记录原因后退回原全局定位。
            logger.exception(f"Failed to load relocalization transform cache: path={path}")
            return None

    def _load_cached_transform_on_start(self) -> None:
        # 任一开关关闭时都不读取磁盘，确保可以一键恢复旧行为。
        if not self.config.fast_icp_enabled or not self.config.load_cached_transform_on_start:
            return
        matrix = self._load_latest_T_map_world()
        if matrix is None:
            return
        self._last_T_map_world = matrix
        self._loaded_T_map_world_from_json = True
        logger.info(
            f"Loaded relocalization transform cache: path={self._latest_transform_path()} "
            f"cached_start_min_local_points={self.config.cached_start_min_local_points}"
        )

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
        """Write JSON through a sibling temporary file, then atomically replace."""
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{time.time_ns()}.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(path)
        finally:
            # replace 成功后临时文件已不存在；失败时清理残留，不影响下次重试。
            temporary.unlink(missing_ok=True)

    def _save_first_transform_json_if_needed(
        self,
        T_map_world: np.ndarray,
        world_to_map_tf: Transform,
        fitness: float,
        n_pts: int,
        match_mode: str,
    ) -> None:
        """Persist only the first successful TF publication of this run."""
        # 已成功保存过或用户关闭保存时，不进行任何磁盘写入。
        if self._first_published_tf_saved or not self.config.save_first_transform_json:
            return

        created_at = datetime.now().astimezone()
        payload: dict[str, Any] = {
            "schema_version": 1,
            "created_at": created_at.isoformat(timespec="seconds"),
            "map_file": self.config.map_file,
            "map_key": self._map_cache_key(),
            "frame": "map<-world",
            "source": "first_published_tf",
            "match_mode": match_mode,
            "fitness": fitness,
            "n_pts": n_pts,
            "T_map_world": T_map_world.tolist(),
            "T_world_map": world_to_map_tf.to_matrix().tolist(),
        }
        timestamped = self._map_cache_dir() / (
            f"{created_at.strftime('%Y%m%d-%H%M%S-%f')}-first-tf.json"
        )
        latest = self._latest_transform_path()
        try:
            # 先保留不可覆盖的历史文件，再原子刷新下次启动读取的 latest。
            self._write_json_atomic(timestamped, payload)
            self._write_json_atomic(latest, payload)
        except OSError:
            # TF 已成功发布，磁盘错误不能阻断 merge；下次成功匹配时继续重试。
            logger.exception(f"Failed to save first relocalization transform: latest_path={latest}")
            return

        self._first_published_tf_saved = True
        logger.info(f"Saved first relocalization transform: file={timestamped} latest={latest}")

    def _maybe_log_skip(self, msg: PointCloud2) -> None:
        # 点数已经够时不需要记录 skip，后续会进入真正的重定位流程。
        if self._has_enough_points(msg):
            return
        now = time.monotonic()
        # 点数不足可能持续很久，日志每 5 秒打一条即可。
        if now - self._last_skip_log > 5.0:
            required = self._required_local_points()
            logger.warning(
                f"relocalize skipped: n_pts={len(msg)} < required_points={required} "
                f"mode={self._current_relocalization_mode()}"
            )
            self._last_skip_log = now

    def _can_try_cached_start_fast_icp(self) -> bool:
        # 第二次独立运行的第一次匹配：必须确实从 JSON 加载到合法矩阵。
        return (
            self.config.fast_icp_enabled
            and self._loaded_T_map_world_from_json
            and self._last_T_map_world is not None
            and not self._has_published_tf_this_run
        )

    def _can_try_subsequent_fast_icp(self) -> bool:
        # 本次运行发布过首个 TF 后，严格服从 subsequent_relocalization_mode。
        return (
            self.config.fast_icp_enabled
            and self.config.subsequent_relocalization_mode == "fast_icp"
            and self._last_T_map_world is not None
            and self._has_published_tf_this_run
        )

    def _can_try_fast_icp(self) -> bool:
        # 两种快速路径共用算法，但点数门槛和日志语义不同。
        return self._can_try_cached_start_fast_icp() or self._can_try_subsequent_fast_icp()

    def _required_local_points(self) -> int:
        # 有跨运行缓存时，按需求把全新启动的首次匹配门槛降到 1 万点。
        if self._can_try_cached_start_fast_icp():
            return self.config.cached_start_min_local_points
        # 同一次运行后续选择 fast ICP 时也允许使用较低点数门槛。
        if self._can_try_subsequent_fast_icp():
            return self.config.fast_icp_min_local_points
        # 没有缓存、关闭 fast 或后续模式为 global 时保持原 5 万点逻辑。
        return self.config.min_local_points

    def _current_relocalization_mode(self) -> str:
        # 该字符串只用于诊断日志，不参与状态判断。
        if self._can_try_cached_start_fast_icp():
            return "cached_start_fast_icp"
        if self._can_try_subsequent_fast_icp():
            return "subsequent_fast_icp"
        return "global"

    def _has_enough_points(self, msg: PointCloud2) -> bool:
        # 门槛由“跨运行快配 / 本次后续快配 / 全局匹配”三种状态动态决定。
        return len(msg) >= self._required_local_points()

    def _publish_tf(self, tf: Transform | None) -> None:
        # _try_relocalize 失败时返回 None，这里直接忽略，保留上一成功 TF。
        if tf is None:
            return
        # 将新的 world<-map 变换推给 merge 流和周期 TF 发布流。
        self._world_to_map.on_next(tf)
        # 只有 on_next 完成后才算本次运行真正发布过成功 TF。
        self._has_published_tf_this_run = True

        # JSON 必须记录“已发布”的结果，因此保存动作放在 on_next 之后执行。
        pending = self._pending_cache_record
        self._pending_cache_record = None
        if pending is not None:
            T_map_world, fitness, n_pts, match_mode = pending
            self._save_first_transform_json_if_needed(
                T_map_world,
                tf,
                fitness,
                n_pts,
                match_mode,
            )

    @staticmethod
    def _tf_from_T_map_world(T_map_world: np.ndarray) -> Transform:
        """Convert the algorithm's map<-world matrix to the published world<-map TF."""
        matrix = RelocalizationModule._validate_T_map_world(T_map_world)
        # 算法输出 map<-world；merge/TF 需要 world<-map，所以必须取逆。
        T_world_map = np.linalg.inv(matrix)
        return Transform(
            translation=Vector3(*T_world_map[:3, 3]),
            rotation=Quaternion.from_rotation_matrix(T_world_map[:3, :3]),
            frame_id=FRAME_WORLD,
            child_frame_id=FRAME_MAP,
        )

    def _record_relocalization_success(
        self,
        T_map_world: np.ndarray,
        world_to_map_tf: Transform,
        fitness: float,
        n_pts: int,
        match_mode: str,
    ) -> None:
        # 更新内存初值；同一次运行后续 fast ICP 使用最新成功结果而非旧 JSON。
        self._last_T_map_world = np.asarray(T_map_world, dtype=float).copy()
        self._last_world_to_map_tf = world_to_map_tf
        self._fast_icp_fail_count = 0
        # 保存必须发生在 _publish_tf 的 on_next 之后，这里只暂存本次成功元数据。
        if self.config.save_first_transform_json and not self._first_published_tf_saved:
            self._pending_cache_record = (
                self._last_T_map_world.copy(),
                fitness,
                n_pts,
                match_mode,
            )

    def _try_relocalize(self, msg: PointCloud2) -> Transform | None:
        # start() 已加载 premap 后才会注册本回调；这里用 assert 保护类型假设。
        assert self._premap is not None

        # 有合法缓存且策略允许时，先走不含 FPFH/RANSAC 的快速 ICP。
        if self._can_try_fast_icp():
            fast_tf = self._try_fast_icp_relocalize(msg)
            if fast_tf is not None:
                return fast_tf
            # 现场可关闭 fallback，用于单独测量快配成功率和耗时。
            if not self.config.fast_icp_fallback_global:
                return None
            # 快配只需 1 万点，但原全局算法仍需 5 万点；不足时延后 fallback。
            if len(msg) < self.config.min_local_points:
                logger.warning(
                    f"global fallback deferred: n_pts={len(msg)} "
                    f"< min_local_points={self.config.min_local_points}"
                )
                return None

        # 没有缓存、后续模式为 global，或快配失败后的 fallback 都走原算法。
        return self._try_global_relocalize(msg)

    def _try_fast_icp_relocalize(self, msg: PointCloud2) -> Transform | None:
        # 调用前由 _can_try_fast_icp 保证 premap 和初始矩阵都已存在。
        assert self._premap is not None
        assert self._last_T_map_world is not None
        match_mode = self._current_relocalization_mode()
        threshold = self.config.fast_icp_min_fitness
        if threshold is None:
            threshold = self.config.fitness_threshold

        t0 = time.monotonic()
        try:
            # 仅做墙面 ICP + final ICP；max_iteration 默认并显式传入 50。
            T_map_world, fitness = _relocalize_with_initial(
                self._premap.pointcloud,
                msg.pointcloud,
                self._last_T_map_world,
                max_correspondence_distance=self.config.fast_icp_max_dist,
                max_iteration=self.config.fast_icp_max_iter,
                crop_radius=self.config.fast_icp_crop_radius,
            )
        except Exception:
            self._fast_icp_fail_count += 1
            logger.exception(
                f"fast ICP failed: mode={match_mode} fail_count={self._fast_icp_fail_count}"
            )
            return None
        dt = time.monotonic() - t0
        n_pts = len(msg)

        # 快配低于阈值时不更新内存矩阵，随后由主流程决定是否 fallback。
        if fitness < threshold:
            self._fast_icp_fail_count += 1
            logger.warning(
                f"fast ICP rejected: mode={match_mode} fitness={fitness:.3f} "
                f"< threshold={threshold} time_cost={dt:.3f}s n_pts={n_pts} "
                f"fail_count={self._fast_icp_fail_count}"
            )
            return None

        try:
            new_tf = self._tf_from_T_map_world(T_map_world)
        except (ValueError, np.linalg.LinAlgError):
            self._fast_icp_fail_count += 1
            logger.exception("fast ICP returned an invalid transform")
            return None

        self._record_relocalization_success(
            T_map_world,
            new_tf,
            fitness,
            n_pts,
            match_mode,
        )
        logger.info(
            f"fast ICP accepted: mode={match_mode} fitness={fitness:.3f} "
            f"time_cost={dt:.3f}s n_pts={n_pts} "
            f"reloc_t={T_map_world[:3, 3].round(3).tolist()}"
        )
        return new_tf

    def _try_global_relocalize(self, msg: PointCloud2) -> Transform | None:
        # start() 已加载 premap 后才会进入这里。
        assert self._premap is not None
        # 记录本次 RANSAC+ICP 总耗时，日志用于现场判断性能瓶颈。
        t0 = time.monotonic()
        try:
            # 核心算法调用：输入 premap(map) 和 local(world)，返回 T_map_world。
            T_map_world, fitness = _relocalize(self._premap.pointcloud, msg.pointcloud)
        except Exception:
            # Open3D 配准偶发异常时不能杀死模块，只记录并等待下一次尝试。
            logger.exception("relocalize() failed")
            return None
        dt = time.monotonic() - t0
        n_pts = len(msg)

        # fitness 越高代表局部点云与 premap 的重合比例越好，低于阈值拒绝。
        if fitness < self.config.fitness_threshold:
            logger.warning(
                f"relocalize rejected: fitness={fitness:.3f} < threshold={self.config.fitness_threshold} "
                f"time_cost={dt:.1f}s n_pts={n_pts}"
            )
            return None

        try:
            # 全局和快速路径复用同一个方向转换 helper，避免 map/world 取反不一致。
            new_tf = self._tf_from_T_map_world(T_map_world)
        except (ValueError, np.linalg.LinAlgError):
            logger.exception("relocalize() returned an invalid transform")
            return None

        self._record_relocalization_success(
            T_map_world,
            new_tf,
            fitness,
            n_pts,
            "global",
        )
        T_world_map = new_tf.to_matrix()
        logger.info(
            f"relocalize: fitness={fitness:.3f} time_cost={dt:.1f}s n_pts={n_pts} "
            f"reloc_t={T_map_world[:3, 3].round(3).tolist()} "
            f"TF {FRAME_WORLD!r} -> {FRAME_MAP!r} "
            f"published_t={T_world_map[:3, 3].round(3).tolist()} "
        )
        return new_tf

    def _publish_periodic(self, pair: tuple[int, Transform]) -> None:
        _, tf = pair
        # 如果模块还没加载 premap，周期发布没有有效数据，直接退出。
        if self._premap is None:
            return
        # 调试开关：需要直接观察原始 premap 时才发布，默认避免占带宽。
        if self.config.publish_loaded_map:
            self.loaded_map.publish(self._premap)
        # 周期性刷新 TF 时间戳，保证下游/可视化能持续看到最新坐标变换。
        self.tf.publish(tf.now())

    def _on_merge_input(self, pair: tuple[PointCloud2, Transform | None]) -> None:
        local, tf = pair
        # 没有 premap 时无法合并旧地图，直接返回。
        if self._premap is None:
            return
        # 重定位成功前还没有 world<-map，premap 不能放进当前 world。
        if tf is None:
            # self.merged_map.publish(local)
            # costmap fallbacks to local map, skip publishing
            return
        # 使用 world<-map 把离线 premap 变换到当前运行的 world 坐标系。
        premap_in_world = self._premap.transform(tf)
        # carving 模式会按 XY 列清理旧点，让当前 local 观测覆盖 premap。
        if self.config.use_carving:
            # 临时 VoxelGrid 只用于本次合并，frame_id 跟 local 保持一致。
            grid = VoxelGrid(carve_columns=True, frame_id=local.frame_id, show_startup_log=False)
            try:
                # 先放 premap，再放 local；local 后插入，才能覆盖同列旧结构。
                grid.add_frame(premap_in_world)
                grid.add_frame(local)
                # 导出合并后的体素点云，并发布给 CostMapper 优先使用。
                self.merged_map.publish(grid.get_global_pointcloud2())
            finally:
                # VoxelGrid 可能占 GPU/CPU 资源，即使 publish 出错也要释放。
                grid.dispose()
        else:
            # 非 carving 模式只做点云拼接，速度快但不会清理重叠列的旧点。
            self.merged_map.publish(local + premap_in_world)
