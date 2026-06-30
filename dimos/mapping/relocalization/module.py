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

import time
from typing import Any

import numpy as np
import reactivex as rx
from reactivex import Subject, combine_latest, operators as ops

from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.core.stream import In, Out
from dimos.mapping.relocalization.relocalize import relocalize as _relocalize
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
    fitness_threshold: float = 0.45
    # merge 时是否用列雕刻：local 当前观测覆盖 premap 的同 XY 列旧点。
    use_carving: bool = True


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

    def _maybe_log_skip(self, msg: PointCloud2) -> None:
        # 点数已经够时不需要记录 skip，后续会进入真正的重定位流程。
        if self._has_enough_points(msg):
            return
        now = time.monotonic()
        # 点数不足可能持续很久，日志每 5 秒打一条即可。
        if now - self._last_skip_log > 5.0:
            logger.warning(
                f"relocalize skipped: n_pts={len(msg)} < MIN_LOCAL_POINTS={MIN_LOCAL_POINTS}"
            )
            self._last_skip_log = now

    def _has_enough_points(self, msg: PointCloud2) -> bool:
        # 判断当前局部地图是否已经包含足够几何信息，避免小点云误匹配。
        return len(msg) >= MIN_LOCAL_POINTS

    def _publish_tf(self, tf: Transform | None) -> None:
        # _try_relocalize 失败时返回 None，这里直接忽略，保留上一成功 TF。
        if tf is None:
            return
        # 将新的 world<-map 变换推给 merge 流和周期 TF 发布流。
        self._world_to_map.on_next(tf)

    def _try_relocalize(self, msg: PointCloud2) -> Transform | None:
        # start() 已加载 premap 后才会注册本回调；这里用 assert 保护类型假设。
        assert self._premap is not None
        # 记录本次 RANSAC+ICP 总耗时，日志用于现场判断性能瓶颈。
        t0 = time.monotonic()
        try:
            # 核心算法调用：输入 premap(map) 和 local(world)，返回 T_map_world。
            T, fitness = _relocalize(self._premap.pointcloud, msg.pointcloud)
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

        # relocalize(scan, map) returns T such that scan_in_map_frame = T(scan_raw).
        # We are publishing a TF for map_in_scan_frame, notice that the base frame is `world`
        # so inverse the transform T here to get map_in_scan_frame
        # 算法输出的是 map<-world；merge/TF 需要 world<-map，所以这里取逆。
        T_inv = np.linalg.inv(T)
        # 把 4x4 矩阵拆成 DimOS Transform 消息，供 TF 和 PointCloud2.transform 使用。
        new_tf = Transform(
            translation=Vector3(*T_inv[:3, 3]),
            rotation=Quaternion.from_rotation_matrix(T_inv[:3, :3]),
            frame_id=FRAME_WORLD,
            child_frame_id=FRAME_MAP,
        )
        logger.info(
            f"relocalize: fitness={fitness:.3f} time_cost={dt:.1f}s n_pts={n_pts} "
            f"reloc_t={T[:3, 3].round(3).tolist()} "
            f"TF {FRAME_WORLD!r} -> {FRAME_MAP!r} "
            f"published_t={T_inv[:3, 3].round(3).tolist()} "
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
