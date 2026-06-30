# Go2 重定位 TF 缓存 + 快速 ICP 优化实施计划

日期：2026-06-30  
目标目录：`dimos/mapping/relocalization/`  
参考文档：`jiangtao/doc/2026-06-29-145420-go2-relocalization-icp-map-merge.md`

## 1. 背景与目标

当前 `unitree-go2-relocalization` 的在线重定位链路是：

```text
global_map(local/world)
  -> relocalize(premap/map, local/world)
  -> FPFH + 多尺度 RANSAC
  -> wall-only top10 ICP
  -> final ICP
  -> 输出 T_map_world
  -> 取逆发布 world<-map TF
```

这套流程是全局搜索，优点是机器人可以从未知位置找回自己；缺点是每次 `_try_relocalize()` 都要走 FPFH、多尺度 RANSAC、候选排序和多次 ICP。现场看到首次或后续重定位匹配 premap 可能耗时很久。

本需求要做的是：

```text
第一次成功重定位：
  走原全局 RANSAC + ICP
  成功后记录本次 TF / T_map_world

后续重定位：
  先用记录下来的 T_map_world 作为初值
  直接跑快速 final ICP
  如果成功，发布 TF，并更新缓存
  如果失败，再 fallback 到原全局 RANSAC + ICP
```

核心目标：

- 大幅缩短第二次及之后的重定位时间。
- 保留原全局重定位能力，避免缓存 TF 错误后系统不可恢复。
- 不改变下游 `merged_map -> CostMapper -> A* / MovementManager` 的接口。
- 不要求机器人每次固定同一启动点；这是“同一次运行内的增量加速”。后续可扩展到把首次 TF 持久化到文件，实现跨启动复用。

## 2. 关键坐标语义

当前 `relocalize()` 返回的是：

```text
T_map_world: map <- world
```

含义是：把当前 `world` 坐标系下的 local/global_map 点云变换到 premap 的 `map` 坐标系。

当前 `RelocalizationModule` 发布的是：

```text
T_world_map = inverse(T_map_world): world <- map
```

也就是 `Transform(frame_id="world", child_frame_id="map")`。

本优化建议缓存两个状态：

```python
self._last_T_map_world: np.ndarray | None
self._last_world_to_map_tf: Transform | None
```

其中：

- `_last_T_map_world`：给后续快速 ICP 当初值。
- `_last_world_to_map_tf`：如果需要调试或复用发布消息，可直接使用。

不要只缓存发布出去的 `Transform(world<-map)`，否则每次快速 ICP 前还要转换并取逆，容易把方向弄错。

## 3. 预期行为

### 3.1 第一次重定位

启动后还没有缓存：

```text
self._last_T_map_world is None
```

流程：

1. 当前 local 点数达到阈值。
2. 调原来的 `_relocalize(self._premap.pointcloud, msg.pointcloud)`。
3. 如果 `fitness < fitness_threshold`，拒绝，不缓存。
4. 如果成功：
   - 缓存 `T_map_world`。
   - 取逆生成 `Transform(world<-map)`。
   - 发布 TF。
   - 后续 merge 开始使用这个 TF。

### 3.2 第二次及以后重定位

已有缓存：

```text
self._last_T_map_world is not None
```

流程：

1. 先调用快速 ICP：

```python
T_fast, fitness_fast = _relocalize_with_initial(
    self._premap.pointcloud,
    msg.pointcloud,
    self._last_T_map_world,
    ...
)
```

2. 如果 `fitness_fast >= fast_icp_fitness_threshold`：
   - 认为快速 ICP 成功。
   - 更新 `self._last_T_map_world = T_fast`。
   - 发布新的 `world<-map` TF。
   - 不再跑全局 RANSAC。

3. 如果快速 ICP 失败：
   - 打 warning，记录 fast ICP 耗时、fitness、点数。
   - fallback 到原 `_relocalize()`。
   - 全局重定位成功则更新缓存并发布。
   - 全局重定位也失败则返回 `None`，保持上一成功 TF 不变。

## 4. 推荐新增配置

在 `dimos/mapping/relocalization/module.py::Config` 增加：

```python
fast_icp_enabled: bool = True
fast_icp_after_first_success: bool = True
fast_icp_fallback_global: bool = True
fast_icp_max_dist: float = 0.3
fast_icp_max_iter: int = 30
fast_icp_crop_radius: float = 8.0
fast_icp_min_fitness: float | None = None
fast_icp_min_local_points: int = 20_000
min_local_points: int = 50_000
```

说明：

- `fast_icp_enabled`：总开关，便于一键回滚到旧行为。
- `fast_icp_after_first_success`：只有第一次全局重定位成功后才启用快速 ICP。
- `fast_icp_fallback_global`：快速 ICP 失败后是否走原全局 RANSAC。
- `fast_icp_max_dist`：ICP 最大对应点距离。建议从 `0.3m` 开始。
- `fast_icp_max_iter`：快速 ICP 迭代次数。建议从 `30` 开始。
- `fast_icp_crop_radius`：以当前 local 初始落点为中心裁剪 premap 的半径，减少 target 点数。
- `fast_icp_min_fitness`：快速 ICP 质量阈值。默认 `None` 时复用 `fitness_threshold`。
- `fast_icp_min_local_points`：快速 ICP 的点数门槛可低于全局 RANSAC，因为已有初值。
- `min_local_points`：把当前硬编码的 `MIN_LOCAL_POINTS = 50_000` 改成配置，方便现场调参。

推荐默认策略：

```text
第一次：要求 local >= min_local_points，走全局 RANSAC。
后续：local >= fast_icp_min_local_points 即可先尝试快速 ICP。
```

这样第二次之后不必每次都等到 50,000 点。

## 5. 文件改动计划

### 5.1 `module.py`

目标：控制“先 fast ICP，失败 fallback 全局 RANSAC”的状态机。

新增成员变量：

```python
self._last_T_map_world: np.ndarray | None = None
self._last_world_to_map_tf: Transform | None = None
self._fast_icp_fail_count = 0
```

新增 helper：

```python
def _tf_from_T_map_world(self, T: np.ndarray) -> Transform:
    T_inv = np.linalg.inv(T)
    return Transform(
        translation=Vector3(*T_inv[:3, 3]),
        rotation=Quaternion.from_rotation_matrix(T_inv[:3, :3]),
        frame_id=FRAME_WORLD,
        child_frame_id=FRAME_MAP,
    )
```

理由：当前 `_try_relocalize()` 里直接写了取逆和构造 Transform。后续全局 RANSAC 成功、fast ICP 成功都要复用同样逻辑，抽成 helper 可以避免方向写错。

调整 `_has_enough_points()`：

```python
def _has_enough_points(self, msg: PointCloud2) -> bool:
    if self._can_try_fast_icp():
        return len(msg) >= self.config.fast_icp_min_local_points
    return len(msg) >= self.config.min_local_points
```

新增：

```python
def _can_try_fast_icp(self) -> bool:
    return (
        self.config.fast_icp_enabled
        and self.config.fast_icp_after_first_success
        and self._last_T_map_world is not None
    )
```

重写 `_try_relocalize()` 的主流程为：

```python
def _try_relocalize(self, msg: PointCloud2) -> Transform | None:
    assert self._premap is not None

    if self._can_try_fast_icp():
        tf = self._try_fast_icp_relocalize(msg)
        if tf is not None:
            return tf
        if not self.config.fast_icp_fallback_global:
            return None

    return self._try_global_relocalize(msg)
```

拆分两个方法：

```python
def _try_fast_icp_relocalize(self, msg: PointCloud2) -> Transform | None:
    ...

def _try_global_relocalize(self, msg: PointCloud2) -> Transform | None:
    ...
```

`_try_global_relocalize()` 基本复用当前 `_try_relocalize()` 逻辑，但成功后增加：

```python
self._last_T_map_world = T
self._last_world_to_map_tf = new_tf
self._fast_icp_fail_count = 0
```

`_try_fast_icp_relocalize()` 成功后同样更新缓存：

```python
self._last_T_map_world = T_fast
self._last_world_to_map_tf = new_tf
self._fast_icp_fail_count = 0
```

fast ICP 失败时：

```python
self._fast_icp_fail_count += 1
logger.warning(...)
```

可选策略：

```python
if self._fast_icp_fail_count >= 3:
    self._last_T_map_world = None
```

但第一版不建议自动清空缓存。因为 fallback 成功会刷新缓存；自动清空可能让系统在轻微动态变化时频繁退回全局搜索。先只记录 fail count。

### 5.2 `relocalize.py`

目标：新增“已有初值时快速精配准”的函数。

当前 `_wall_subset()` 是 `relocalize()` 内部嵌套函数。建议抽到文件顶层：

```python
def _wall_subset(cloud: o3d.geometry.PointCloud) -> o3d.geometry.PointCloud:
    ...
```

原因：原全局流程和新 fast ICP 流程都要复用 wall-only 点云。

新增：

```python
def _prepare_fine_cloud(
    cloud: o3d.geometry.PointCloud,
    voxel_size: float = FINE_VOXEL,
) -> o3d.geometry.PointCloud:
    down = cloud.voxel_down_sample(voxel_size)
    down.estimate_normals(
        o3d.geometry.KDTreeSearchParamHybrid(radius=voxel_size * 2, max_nn=30)
    )
    return down
```

注意：global premap 侧仍优先用 `_global_fine(global_map, FINE_VOXEL)`，不要每次重新处理整张 premap。

新增核心函数：

```python
def relocalize_with_initial(
    global_map: o3d.geometry.PointCloud,
    local_map: o3d.geometry.PointCloud,
    init_T_map_world: np.ndarray,
    *,
    max_correspondence_distance: float = RERANK_DIST,
    max_iteration: int = 30,
    crop_radius: float | None = 8.0,
) -> tuple[np.ndarray, float]:
    ...
```

实现逻辑：

```text
1. local_map -> src_fine
2. global_map -> tgt_fine，使用 _global_fine 缓存
3. 如果 crop_radius 不为空：
   3.1 用 init_T_map_world 把 src_fine 拷贝/变换到 map 坐标
   3.2 求 transformed src 的 AABB
   3.3 AABB 按 crop_radius 扩大
   3.4 crop tgt_fine 得到局部 target
   3.5 如果 crop 后点数太少，fallback 使用完整 tgt_fine
4. 构造 src_walls / tgt_walls
5. 以 init_T_map_world 为初值跑 wall-only ICP
6. 以 wall ICP 结果为初值跑 full-cloud final ICP
7. 返回 final.transformation 和 wall/full fitness
```

第一版 fitness 建议返回 full ICP 的 `result.fitness` 或 wall ICP 与 full ICP 的较小值：

```python
fitness = min(float(wall_result.fitness), float(final_result.fitness))
```

理由：wall ICP 防止 yaw 错，full ICP 防止整体错。取较小值更保守。

但要和旧 `_relocalize()` 的语义保持一致：越大越好。不能返回 RMSE/RMSE²。

### 5.3 避免修改蓝图

不需要改：

- `dimos/robot/unitree/go2/blueprints/smart/unitree_go2.py`
- `dimos/robot/all_blueprints.py`

因为还是同一个 `RelocalizationModule`，只是内部策略变了。CLI 仍然使用：

```bash
dimos run unitree-go2-relocalization --robot-ip 192.168.123.161 \
  -o relocalizationmodule.map_file=recording_go2
```

需要调参时再追加：

```bash
-o relocalizationmodule.fast_icp_enabled=true \
-o relocalizationmodule.fast_icp_max_dist=0.3 \
-o relocalizationmodule.fast_icp_max_iter=30 \
-o relocalizationmodule.fast_icp_crop_radius=8.0
```

## 6. 推荐伪代码

### 6.1 `module.py`

```python
from dimos.mapping.relocalization.relocalize import (
    relocalize as _relocalize,
    relocalize_with_initial as _relocalize_with_initial,
)

class RelocalizationModule(Module):
    def __init__(...):
        ...
        self._last_T_map_world = None
        self._last_world_to_map_tf = None
        self._fast_icp_fail_count = 0

    def _try_relocalize(self, msg):
        assert self._premap is not None

        if self._can_try_fast_icp():
            tf = self._try_fast_icp_relocalize(msg)
            if tf is not None:
                return tf
            if not self.config.fast_icp_fallback_global:
                return None

        return self._try_global_relocalize(msg)

    def _try_fast_icp_relocalize(self, msg):
        assert self._premap is not None
        assert self._last_T_map_world is not None

        t0 = time.monotonic()
        try:
            T, fitness = _relocalize_with_initial(
                self._premap.pointcloud,
                msg.pointcloud,
                self._last_T_map_world,
                max_correspondence_distance=self.config.fast_icp_max_dist,
                max_iteration=self.config.fast_icp_max_iter,
                crop_radius=self.config.fast_icp_crop_radius,
            )
        except Exception:
            logger.exception("fast ICP relocalize failed")
            return None

        threshold = (
            self.config.fast_icp_min_fitness
            if self.config.fast_icp_min_fitness is not None
            else self.config.fitness_threshold
        )

        if fitness < threshold:
            logger.warning(...)
            self._fast_icp_fail_count += 1
            return None

        tf = self._tf_from_T_map_world(T)
        self._last_T_map_world = T
        self._last_world_to_map_tf = tf
        self._fast_icp_fail_count = 0
        logger.info(...)
        return tf

    def _try_global_relocalize(self, msg):
        ...
        T, fitness = _relocalize(...)
        ...
        tf = self._tf_from_T_map_world(T)
        self._last_T_map_world = T
        self._last_world_to_map_tf = tf
        self._fast_icp_fail_count = 0
        return tf
```

### 6.2 `relocalize.py`

```python
def relocalize_with_initial(...):
    src_fine = _prepare_fine_cloud(local_map, FINE_VOXEL)
    tgt_fine = _global_fine(global_map, FINE_VOXEL)

    if crop_radius is not None and crop_radius > 0:
        tgt_fine = _crop_target_around_source(
            src_fine,
            tgt_fine,
            init_T_map_world,
            crop_radius,
        )

    src_walls = _wall_subset(src_fine)
    tgt_walls = _wall_subset(tgt_fine)
    tukey = _reg.TransformationEstimationPointToPlane(_reg.TukeyLoss(k=max_correspondence_distance))

    wall = _reg.registration_icp(
        src_walls,
        tgt_walls,
        max_correspondence_distance,
        init_T_map_world,
        tukey,
        _reg.ICPConvergenceCriteria(max_iteration=max_iteration),
    )

    final = _reg.registration_icp(
        src_fine,
        tgt_fine,
        max_correspondence_distance,
        np.asarray(wall.transformation),
        tukey,
        _reg.ICPConvergenceCriteria(max_iteration=max_iteration),
    )

    fitness = min(float(wall.fitness), float(final.fitness))
    return np.asarray(final.transformation), fitness
```

## 7. 性能预期

当前完整流程每次可能包含：

```text
local 多尺度 FPFH
global premap 多尺度 FPFH 首次缓存
17 次 RANSAC
候选翻倍
top10 wall ICP
1 次 final ICP
```

优化后，第二次及之后的成功 fast path 只包含：

```text
local fine downsample + normals
global fine cache
可选 target crop
1 次 wall ICP
1 次 full ICP
```

预期：

- 首次重定位：耗时基本不变。
- 第二次及之后：如果机器人位姿连续、缓存 TF 没漂太远，耗时应从多秒甚至几十秒降到 1-5 秒级，具体取决于点数、crop 半径和 CPU。
- 若 fast ICP 失败：最多多付出一次 fast ICP 的成本，然后回到原全局 RANSAC。

## 8. 风险与应对

### 8.1 缓存 TF 方向弄反

风险：

```text
T_map_world 和 T_world_map 混用
```

应对：

- state 变量命名必须带方向：`_last_T_map_world`。
- helper 命名必须明确：`_tf_from_T_map_world()`。
- 日志同时打印 `T[:3, 3]` 和 `T_inv[:3, 3]`。

### 8.2 ICP 局部最优

风险：如果缓存初值偏差太大，ICP 会收敛到错误位置。

应对：

- 设置 `fast_icp_max_dist` 不要太大，先用 `0.3m`。
- 设置 `fast_icp_min_fitness`，低质量不发布。
- fast ICP 失败必须 fallback 全局 RANSAC。

### 8.3 动态障碍导致 fitness 假高或假低

风险：人、门、移动障碍会影响 ICP。

应对：

- fast ICP 使用 wall-only + full-cloud 双阶段。
- fitness 取 wall/full 较小值，偏保守。
- 失败时不清空上一成功 TF，下游仍可用上一合并图。

### 8.4 crop 半径过小

风险：初值有偏差时，crop 后的 premap 局部 target 不包含真实匹配区域。

应对：

- 默认 `fast_icp_crop_radius=8.0`，先偏大。
- crop 后 target 点数小于阈值时退回完整 `tgt_fine`。
- 日志记录 crop target 点数。

### 8.5 点数门槛过低

风险：后续 fast ICP 若只用很少 local 点，容易被局部墙面误导。

应对：

- `fast_icp_min_local_points` 初始建议 `20_000`，不是特别激进。
- 实测稳定后再尝试 8,000 或 10,000。

## 9. 日志与可观测性

新增日志必须区分全局路径和快速路径。

全局成功：

```text
relocalize global: fitness=0.xxx time_cost=xs n_pts=xxxxx reloc_t=[...] published_t=[...]
```

fast ICP 成功：

```text
relocalize fast_icp: fitness=0.xxx time_cost=xs n_pts=xxxxx crop_pts=xxxxx reloc_t=[...] published_t=[...]
```

fast ICP 失败：

```text
relocalize fast_icp rejected: fitness=0.xxx < threshold=0.xxx time_cost=xs n_pts=xxxxx fallback_global=true
```

全局 fallback 成功：

```text
relocalize fallback_global: fitness=0.xxx time_cost=xs n_pts=xxxxx
```

这些日志用于现场判断：

- fast ICP 是否真的命中。
- fallback 是否频繁发生。
- crop 点数是否过少。
- 第二次之后耗时是否明显下降。

## 10. 测试计划

### 10.1 单元测试：矩阵方向

新增测试文件建议：

```text
dimos/mapping/relocalization/test_module_fast_icp.py
```

测试：

1. 构造一个简单 `T_map_world`。
2. 调 `_tf_from_T_map_world()`。
3. 验证输出 `Transform` 的矩阵等于 `np.linalg.inv(T_map_world)`。
4. 验证 `frame_id == "world"`，`child_frame_id == "map"`。

### 10.2 单元测试：第一次成功后缓存

用 monkeypatch 替换 `_relocalize()`：

1. 第一次返回 `(T1, 0.8)`。
2. 调 `_try_global_relocalize()`。
3. 验证：
   - 返回非 None。
   - `_last_T_map_world is T1` 或数值相等。
   - `_last_world_to_map_tf` 非 None。

### 10.3 单元测试：fast ICP 成功不走 fallback

monkeypatch：

- `_relocalize_with_initial()` 返回 `(T2, 0.8)`。
- `_relocalize()` 如果被调用就 fail。

验证：

- `_try_relocalize()` 返回 fast ICP 的 TF。
- `_last_T_map_world` 更新为 `T2`。
- fallback 未调用。

### 10.4 单元测试：fast ICP 失败 fallback 到全局

monkeypatch：

- `_relocalize_with_initial()` 返回 `(T_bad, 0.1)`。
- `_relocalize()` 返回 `(T_global, 0.8)`。

验证：

- 最终返回 `T_global` 对应 TF。
- `_last_T_map_world` 更新为 `T_global`。
- `_fast_icp_fail_count` 有记录或被全局成功清零。

### 10.5 算法测试：简单点云平移/旋转

在 `relocalize.py` 侧构造简单几何点云：

1. 生成一个带墙面的 target point cloud。
2. 对它施加已知 `T_world_map` 或 `T_map_world` 得到 source。
3. 给 `relocalize_with_initial()` 一个接近真实的初值。
4. 验证输出矩阵接近真实值，fitness 高于阈值。

### 10.6 回放验证

使用已有 replay：

```bash
dimos --replay --replay-db recording_go2 run unitree-go2-relocalization \
  -o relocalizationmodule.map_file=recording_go2 \
  -o relocalizationmodule.fast_icp_enabled=true
```

观察日志：

1. 第一次应出现 `relocalize global`。
2. 后续应优先出现 `relocalize fast_icp`。
3. 如果 fast ICP 成功，后续不应频繁出现 `fallback_global`。
4. Rerun 中 `global_map` 与 `merged_map` 不应错位。

## 11. 验收标准

必须满足：

- 默认行为兼容：不传新参数时，第一次仍可原样全局重定位。
- 第一次全局成功后，后续优先 fast ICP。
- fast ICP 成功时发布 TF，并更新缓存。
- fast ICP 失败时 fallback 到原全局 RANSAC。
- 失败时不发布低质量 TF，不污染 `_world_to_map`。
- `merged_map` 与当前 local 不出现明显错位。
- `ruff check` 通过。
- relocalization 单元测试通过。

建议性能门槛：

| 场景 | 当前预期 | 优化后目标 |
|---|---:|---:|
| 首次重定位 | 不变 | 不要求变快 |
| 后续 fast ICP 命中 | 多秒到几十秒 | 1-5 秒 |
| fast ICP 失败 fallback | 比原逻辑多一次 fast ICP | 可接受 |
| fallback 频率 | 无 | 稳定环境下低于 20% |

## 12. 实施顺序

1. 只做 `module.py` 状态机拆分，不改算法：
   - 抽 `_tf_from_T_map_world()`。
   - 抽 `_try_global_relocalize()`。
   - 成功后缓存 `T_map_world`。

2. 在 `relocalize.py` 新增 `relocalize_with_initial()`：
   - 先不做 crop，只跑 wall ICP + full ICP。
   - 保证返回 fitness 语义仍是“越大越好”。

3. 接入 fast ICP：
   - `_try_relocalize()` 先尝试 fast ICP。
   - 失败 fallback 全局。

4. 增加 crop 优化：
   - 以 `init_T_map_world` 变换 source。
   - AABB 扩张后裁剪 target。
   - crop 点数太少则退回完整 target。

5. 补测试和日志。

6. 用 replay 验证。

7. 真机验证：
   - 原地启动。
   - 等第一次 global 成功。
   - 后续观察 fast ICP 命中和 merged_map 对齐。

## 13. 后续扩展：跨启动复用

当前需求只要求“第一次重定位记录下来，后面重定位先用这个 TF”。这默认是在同一次 `dimos run` 生命周期内。

后续如果希望“每次启动 Go2 都用上一次成功 TF”，可以追加：

```python
cached_transform_file: str | None = None
load_cached_transform_on_start: bool = False
save_cached_transform_on_success: bool = False
```

流程：

- 启动时从 JSON 读取 `T_map_world`。
- 第一帧满足 `fast_icp_min_local_points` 后先跑 fast ICP。
- 成功则覆盖 JSON。
- 失败则 fallback 全局 RANSAC。

这能进一步缩短每次开机首次定位时间，但对固定启动点/地图稳定性要求更高，建议作为第二阶段。
