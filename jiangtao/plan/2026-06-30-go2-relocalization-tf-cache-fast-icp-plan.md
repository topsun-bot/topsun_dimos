# Go2 重定位 TF 缓存 + 快速 ICP 优化实施计划

创建日期：2026-06-30
修订日期：2026-07-03
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

本需求要做的是两层加速：

```text
第一次独立运行：
  走原全局 RANSAC + ICP
  第一次成功发布 TF 时，把 T_map_world 写入一个按时间命名的 JSON 文件

第二次及以后全新启动：
  启动时从 JSON 文件读取上一次保存的首次 TF / T_map_world
  local 点数达到 10,000 后，先用缓存 T_map_world 作为初值跑快速 ICP
  如果成功，发布 TF，更新内存缓存；若这是本次运行第一次发布，则写新的时间命名 JSON
  如果失败，再 fallback 到原全局 RANSAC + ICP

同一次运行内后续每 2 秒重定位：
  由配置项选择继续使用缓存快速 ICP，还是完全按当前全局 RANSAC 逻辑匹配
```

核心目标：

- 大幅缩短第二次及以后“全新启动运行”的首次重定位时间。
- 保留原全局重定位能力，避免缓存 TF 错误后系统不可恢复。
- 不改变下游 `merged_map -> CostMapper -> A* / MovementManager` 的接口。
- 只保存每次运行中“第一次成功发布的 TF / T_map_world”，不要每 2 秒覆盖一次缓存文件。
- 通过配置决定同一次运行内后续匹配走 fast ICP 还是原全局 RANSAC。

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
self._first_published_tf_saved: bool
```

其中：

- `_last_T_map_world`：给后续快速 ICP 当初值。
- `_last_world_to_map_tf`：如果需要调试或复用发布消息，可直接使用。
- `_first_published_tf_saved`：保证同一次运行里只把第一次成功发布的 TF 写入 JSON，不被后续 2 秒一次的重定位覆盖。

不要只缓存发布出去的 `Transform(world<-map)`，否则每次快速 ICP 前还要转换并取逆，容易把方向弄错。

此外，本需求必须有持久化缓存：

```text
JSON 文件保存 T_map_world，供下一次完全独立的 dimos run 读取。
```

推荐每次运行第一次成功发布 TF 时，写一个按时间命名的文件，例如：

```text
jiangtao/cache/relocalization_tf/recording_go2/20260703-153012-first-tf.json
```

同时维护一个 latest 指针文件，便于下次启动自动读取：

```text
jiangtao/cache/relocalization_tf/recording_go2/latest.json
```

`latest.json` 可以是实际矩阵文件的拷贝，也可以是一个引用：

```json
{
  "latest": "20260703-153012-first-tf.json"
}
```

第一版建议直接复制一份矩阵到 `latest.json`，实现更简单，启动时只读一个文件。

## 3. 预期行为

### 3.1 第一次独立运行，没有 JSON 缓存

启动后内存和磁盘都没有缓存：

```text
self._last_T_map_world is None
latest.json 不存在或禁用读取
```

流程：

1. 当前 local 点数达到阈值。
2. 调原来的 `_relocalize(self._premap.pointcloud, msg.pointcloud)`。
3. 如果 `fitness < fitness_threshold`，拒绝，不缓存。
4. 如果成功：
   - 缓存 `T_map_world`。
   - 取逆生成 `Transform(world<-map)`。
   - 发布 TF。
   - 如果这是本次运行第一次发布 TF，则写入按时间命名的 JSON 文件，并更新 `latest.json`。
   - 后续 merge 开始使用这个 TF。

### 3.2 第二次及以后完全独立运行，有 JSON 缓存

启动时读取 `latest.json`：

```text
self._last_T_map_world is not None
_loaded_T_map_world_from_json is not None
```

流程：

1. 不再等 50,000 点；当前 local 点数达到 `cached_start_min_local_points=10_000` 后，先调用快速 ICP：

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
   - 如果这是本次运行第一次发布 TF，则写入新的时间命名 JSON，并更新 `latest.json`。
   - 不再跑全局 RANSAC。

3. 如果快速 ICP 失败：
   - 打 warning，记录 fast ICP 耗时、fitness、点数。
   - fallback 到原 `_relocalize()`。
   - 全局重定位成功则更新缓存并发布。
   - 如果这是本次运行第一次成功发布 TF，同样写入新的时间命名 JSON。
   - 全局重定位也失败则返回 `None`，保持上一成功 TF 不变。

### 3.3 同一次运行内后续 2 秒重定位

当前模块每 `RELOC_INTERVAL = 2.0` 秒最多尝试一次重定位。第一次发布 TF 之后，同一次运行内后续每次尝试应该由配置项决定：

```text
subsequent_relocalization_mode = "fast_icp" | "global"
```

- `"fast_icp"`：后续每 2 秒优先用当前内存里的 `_last_T_map_world` 做快速 ICP；失败后是否 fallback 由 `fast_icp_fallback_global` 控制。
- `"global"`：后续每 2 秒完全按旧逻辑走全局 FPFH + RANSAC + ICP，不使用缓存快速匹配。

注意：无论后续模式是什么，都只保存本次运行第一次成功发布的 TF 到 JSON；后续成功只更新内存变量，不覆盖本次运行的 JSON 文件。

## 4. 推荐新增配置

在 `dimos/mapping/relocalization/module.py::Config` 增加：

```python
fast_icp_enabled: bool = True
load_cached_transform_on_start: bool = True
save_first_transform_json: bool = True
cached_transform_dir: str = "jiangtao/cache/relocalization_tf"
cached_transform_latest_file: str | None = None
subsequent_relocalization_mode: str = "fast_icp"  # "fast_icp" | "global"
fast_icp_fallback_global: bool = True
fast_icp_max_dist: float = 0.3
fast_icp_max_iter: int = 50
fast_icp_crop_radius: float = 8.0
fast_icp_min_fitness: float | None = None
cached_start_min_local_points: int = 10_000
fast_icp_min_local_points: int = 10_000
min_local_points: int = 50_000
```

说明：

- `fast_icp_enabled`：总开关，便于一键回滚到旧行为。
- `load_cached_transform_on_start`：启动时是否读取上一次保存的 `latest.json`。
- `save_first_transform_json`：本次运行第一次成功发布 TF 时，是否写 JSON 缓存。
- `cached_transform_dir`：缓存根目录。建议按 `map_file` 再分子目录，避免不同地图互相覆盖。
- `cached_transform_latest_file`：可选指定读取的 JSON 文件；为空时使用 `{cached_transform_dir}/{map_file}/latest.json`。
- `subsequent_relocalization_mode`：同一次运行内，第一次发布 TF 之后每 2 秒重定位走 `"fast_icp"` 还是 `"global"`。
- `fast_icp_fallback_global`：快速 ICP 失败后是否走原全局 RANSAC。
- `fast_icp_max_dist`：ICP 最大对应点距离。建议从 `0.3m` 开始。
- `fast_icp_max_iter`：快速 ICP 迭代次数。按当前 final ICP 一样，先设置为 `50`。
- `fast_icp_crop_radius`：以当前 local 初始落点为中心裁剪 premap 的半径，减少 target 点数。
- `fast_icp_min_fitness`：快速 ICP 质量阈值。默认 `None` 时复用 `fitness_threshold`。
- `cached_start_min_local_points`：全新启动且已读取 JSON 初值时，进入快速匹配的点数门槛，按需求设置为 `10_000`。
- `fast_icp_min_local_points`：同一次运行内后续 fast ICP 的点数门槛，也按需求先设置为 `10_000`。
- `min_local_points`：把当前硬编码的 `MIN_LOCAL_POINTS = 50_000` 改成配置，方便现场调参。

推荐默认策略：

```text
无 JSON 缓存的第一次运行：local >= min_local_points，走全局 RANSAC。
有 JSON 缓存的全新启动：local >= cached_start_min_local_points，即 10,000 点，先尝试快速 ICP。
同一次运行后续匹配：由 subsequent_relocalization_mode 决定 fast ICP 还是 global。
```

这样第二次全新启动不必再等到 50,000 点，10,000 点即可进入快速 ICP。

## 5. 文件改动计划

### 5.1 `module.py`

目标：控制“先 fast ICP，失败 fallback 全局 RANSAC”的状态机。

新增成员变量：

```python
self._last_T_map_world: np.ndarray | None = None
self._last_world_to_map_tf: Transform | None = None
self._loaded_T_map_world_from_json: bool = False
self._first_published_tf_saved: bool = False
self._fast_icp_fail_count = 0
```

启动时新增步骤：

```python
if self.config.load_cached_transform_on_start:
    self._last_T_map_world = self._load_latest_T_map_world()
    self._loaded_T_map_world_from_json = self._last_T_map_world is not None
```

JSON 加载失败时只打 warning，不阻止模块启动；然后走原全局 RANSAC。

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
    if self._can_try_cached_start_fast_icp():
        return len(msg) >= self.config.cached_start_min_local_points
    if self._can_try_fast_icp():
        return len(msg) >= self.config.fast_icp_min_local_points
    return len(msg) >= self.config.min_local_points
```

新增：

```python
def _has_published_tf_this_run(self) -> bool:
    return self._last_world_to_map_tf is not None and self._first_published_tf_saved

def _can_try_cached_start_fast_icp(self) -> bool:
    return (
        self.config.fast_icp_enabled
        and self.config.load_cached_transform_on_start
        and self._loaded_T_map_world_from_json
        and self._last_T_map_world is not None
        and not self._first_published_tf_saved
    )

def _can_try_fast_icp(self) -> bool:
    return (
        self.config.fast_icp_enabled
        and self.config.subsequent_relocalization_mode == "fast_icp"
        and self._last_T_map_world is not None
        and self._first_published_tf_saved
    )
```

重写 `_try_relocalize()` 的主流程为：

```python
def _try_relocalize(self, msg: PointCloud2) -> Transform | None:
    assert self._premap is not None

    if self._can_try_cached_start_fast_icp() or self._can_try_fast_icp():
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
self._save_first_transform_json_if_needed(T, new_tf)
```

`_try_fast_icp_relocalize()` 成功后同样更新缓存：

```python
self._last_T_map_world = T_fast
self._last_world_to_map_tf = new_tf
self._fast_icp_fail_count = 0
self._save_first_transform_json_if_needed(T_fast, new_tf)
```

`_save_first_transform_json_if_needed()` 必须保证：

```python
if self._first_published_tf_saved:
    return
if not self.config.save_first_transform_json:
    return
write_timestamped_json(...)
update_latest_json(...)
self._first_published_tf_saved = True
```

如果写 JSON 失败：

- 记录 `logger.exception(...)` 或 warning。
- 不影响 TF 发布和地图 merge。
- 不把 `_first_published_tf_saved` 置为 True，下一次成功发布时可以再尝试保存。

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

### 5.2 JSON 缓存格式

建议 JSON 内容保存完整矩阵、方向、地图名、时间和来源，便于排查：

```json
{
  "schema_version": 1,
  "created_at": "2026-07-03T15:30:12+08:00",
  "map_file": "recording_go2",
  "frame": "map<-world",
  "source": "first_published_tf",
  "fitness": 0.681,
  "n_pts": 57385,
  "T_map_world": [
    [1.0, 0.0, 0.0, 0.0],
    [0.0, 1.0, 0.0, 0.0],
    [0.0, 0.0, 1.0, 0.0],
    [0.0, 0.0, 0.0, 1.0]
  ],
  "T_world_map": [
    [1.0, 0.0, 0.0, 0.0],
    [0.0, 1.0, 0.0, 0.0],
    [0.0, 0.0, 1.0, 0.0],
    [0.0, 0.0, 0.0, 1.0]
  ]
}
```

读取时只信任：

```text
schema_version == 1
frame == "map<-world"
T_map_world 是 4x4 数值矩阵
map_file 与当前配置一致，或配置允许忽略 map_file 校验
```

### 5.3 `relocalize.py`

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
    max_iteration: int = 50,
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

### 5.4 避免修改蓝图

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
-o relocalizationmodule.fast_icp_max_iter=50 \
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
        self._loaded_T_map_world_from_json = False
        self._first_published_tf_saved = False
        self._fast_icp_fail_count = 0

    def start(self):
        ...
        if self.config.load_cached_transform_on_start:
            self._last_T_map_world = self._load_latest_T_map_world()
            self._loaded_T_map_world_from_json = self._last_T_map_world is not None

    def _try_relocalize(self, msg):
        assert self._premap is not None

        if self._can_try_cached_start_fast_icp() or self._can_try_fast_icp():
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
        self._save_first_transform_json_if_needed(T, tf, fitness=fitness, n_pts=len(msg))
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
        self._save_first_transform_json_if_needed(T, tf, fitness=fitness, n_pts=len(msg))
        return tf

    def _save_first_transform_json_if_needed(self, T, tf, *, fitness, n_pts):
        if self._first_published_tf_saved:
            return
        if not self.config.save_first_transform_json:
            return
        self._write_timestamped_transform_json(T, tf, fitness=fitness, n_pts=n_pts)
        self._write_latest_transform_json(T, tf, fitness=fitness, n_pts=n_pts)
        self._first_published_tf_saved = True
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

优化后，第二次及以后全新启动，如果 `latest.json` 命中并且 fast ICP 成功，只包含：

```text
local fine downsample + normals
global fine cache
可选 target crop
1 次 wall ICP
1 次 full ICP
```

预期：

- 第一次没有 JSON 缓存的运行：耗时基本不变，但第一次成功发布 TF 会写入 JSON。
- 第二次及以后全新启动：不再等 50,000 点，10,000 点后即可尝试 fast ICP；如果缓存 TF 没漂太远，耗时应从多秒甚至几十秒降到 1-5 秒级，具体取决于点数、crop 半径和 CPU。
- 同一次运行内后续每 2 秒匹配：由 `subsequent_relocalization_mode` 决定。`fast_icp` 模式追求速度，`global` 模式保持旧逻辑。
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

- 按需求默认使用 `10_000`，但必须保留 `cached_start_min_local_points` / `fast_icp_min_local_points` 配置项。
- 如果现场出现误匹配，可以临时调高点数门槛，或打开 `subsequent_relocalization_mode=global`。

### 8.6 JSON 缓存污染

风险：如果第一次成功发布的 TF 本身是错的，后续独立运行会优先用错误初值跑 fast ICP。

应对：

- 只在 `fitness >= threshold` 后保存 JSON。
- JSON 里保存 `fitness`、`n_pts`、`map_file`、时间戳，便于人工检查。
- fast ICP 失败必须 fallback 全局 RANSAC。
- fallback 全局成功且是本次运行第一次发布 TF 时，会写入新的时间命名 JSON，并更新 `latest.json`。
- 可加配置 `load_cached_transform_on_start=false` 作为现场回滚开关。

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
- 第二次全新启动是否从 10,000 点开始进入 fast ICP。
- 同一次运行后续匹配走的是 `fast_icp` 还是 `global`。

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
   - 第一次成功发布时写入时间命名 JSON。
   - `latest.json` 被更新。
   - 第二次同一运行成功发布不会再次覆盖本次时间命名 JSON。

### 10.3 单元测试：第二次独立运行读取 JSON 后 10,000 点触发 fast ICP

使用临时目录写入一个 `latest.json`：

1. 初始化模块，配置 `load_cached_transform_on_start=true`。
2. 调 `start()` 或直接调用 `_load_latest_T_map_world()`。
3. 验证 `_last_T_map_world` 非 None。
4. 构造一个 `len(msg) == 10_000` 的 `PointCloud2`。
5. 验证 `_has_enough_points(msg)` 为 True。
6. 验证 `_try_relocalize()` 会先进入 `_try_fast_icp_relocalize()`，而不是等待 50,000 点。

### 10.4 单元测试：fast ICP 成功不走 fallback

monkeypatch：

- `_relocalize_with_initial()` 返回 `(T2, 0.8)`。
- `_relocalize()` 如果被调用就 fail。

验证：

- `_try_relocalize()` 返回 fast ICP 的 TF。
- `_last_T_map_world` 更新为 `T2`。
- fallback 未调用。

### 10.5 单元测试：fast ICP 失败 fallback 到全局

monkeypatch：

- `_relocalize_with_initial()` 返回 `(T_bad, 0.1)`。
- `_relocalize()` 返回 `(T_global, 0.8)`。

验证：

- 最终返回 `T_global` 对应 TF。
- `_last_T_map_world` 更新为 `T_global`。
- `_fast_icp_fail_count` 有记录或被全局成功清零。

### 10.6 单元测试：同一次运行后续模式选择

准备 `_last_T_map_world` 和 `_first_published_tf_saved=True`：

1. `subsequent_relocalization_mode="fast_icp"` 时，验证 `_try_relocalize()` 先走 fast ICP。
2. `subsequent_relocalization_mode="global"` 时，验证 `_try_relocalize()` 直接走 `_try_global_relocalize()`。
3. 两种模式下都验证 JSON 只保存第一次发布，不被后续发布覆盖。

### 10.7 算法测试：简单点云平移/旋转

在 `relocalize.py` 侧构造简单几何点云：

1. 生成一个带墙面的 target point cloud。
2. 对它施加已知 `T_world_map` 或 `T_map_world` 得到 source。
3. 给 `relocalize_with_initial()` 一个接近真实的初值。
4. 验证输出矩阵接近真实值，fitness 高于阈值。

### 10.8 回放验证

使用已有 replay：

```bash
dimos --replay --replay-db recording_go2 run unitree-go2-relocalization \
  -o relocalizationmodule.map_file=recording_go2 \
  -o relocalizationmodule.fast_icp_enabled=true \
  -o relocalizationmodule.save_first_transform_json=true
```

观察日志：

1. 第一次无 JSON 时应出现 `relocalize global`。
2. 第一次成功发布 TF 后，应写入时间命名 JSON 和 `latest.json`。
3. 停掉进程，重新启动同一命令。
4. 第二次启动应读取 `latest.json`。
5. 第二次启动 local 点数达到 10,000 后，应优先出现 `relocalize fast_icp`。
6. 如果 fast ICP 成功，首次 TF 发布时间应明显早于等待 50,000 点的旧路径。
7. Rerun 中 `global_map` 与 `merged_map` 不应错位。

## 11. 验收标准

必须满足：

- 默认行为兼容：不传新参数时，第一次仍可原样全局重定位。
- 第一次成功发布 TF 时，保存按时间命名的 JSON，并更新 `latest.json`。
- 第二次完全独立运行时，能读取 `latest.json`。
- 第二次完全独立运行时，local 点数达到 10,000 即可进入 fast ICP，不需要等 50,000。
- 同一次运行内后续匹配可通过 `subsequent_relocalization_mode` 选择 `fast_icp` 或 `global`。
- fast ICP 成功时发布 TF，并更新缓存。
- fast ICP 失败时 fallback 到原全局 RANSAC。
- 失败时不发布低质量 TF，不污染 `_world_to_map`。
- 同一次运行里只保存第一次成功发布的 TF，不被后续 2 秒一次的重定位覆盖。
- `merged_map` 与当前 local 不出现明显错位。
- `ruff check` 通过。
- relocalization 单元测试通过。

建议性能门槛：

| 场景 | 当前预期 | 优化后目标 |
|---|---:|---:|
| 第一次无 JSON 运行 | 不变 | 成功后保存 JSON |
| 第二次独立运行 fast ICP 命中 | 等 50,000 点 + 多秒到几十秒 | 10,000 点后 1-5 秒 |
| 同一次运行后续 fast ICP 命中 | 多秒到几十秒 | 1-5 秒 |
| fast ICP 失败 fallback | 比原逻辑多一次 fast ICP | 可接受 |
| fallback 频率 | 无 | 稳定环境下低于 20% |

## 12. 实施顺序

1. 只做 `module.py` 状态机拆分，不改算法：
   - 抽 `_tf_from_T_map_world()`。
   - 抽 `_try_global_relocalize()`。
   - 成功后缓存 `T_map_world`。

2. 增加 JSON 持久化：
   - 启动时读取 `{cached_transform_dir}/{map_file}/latest.json`。
   - 第一次成功发布 TF 时写入 `{timestamp}-first-tf.json`。
   - 同时更新 `latest.json`。
   - 保证同一次运行只保存第一次发布的 TF。

3. 在 `relocalize.py` 新增 `relocalize_with_initial()`：
   - 先不做 crop，只跑 wall ICP + full ICP。
   - 保证返回 fitness 语义仍是“越大越好”。
   - ICP 迭代次数默认按当前 final ICP，设置为 50。

4. 接入跨启动 fast ICP：
   - 如果启动时读到 JSON，local 点数达到 10,000 后先尝试 fast ICP。
   - 失败 fallback 全局。

5. 接入同一次运行后续策略：
   - `subsequent_relocalization_mode="fast_icp"` 时，后续每 2 秒优先 fast ICP。
   - `subsequent_relocalization_mode="global"` 时，后续每 2 秒走旧全局逻辑。

6. 增加 crop 优化：
   - 以 `init_T_map_world` 变换 source。
   - AABB 扩张后裁剪 target。
   - crop 点数太少则退回完整 target。

7. 补测试和日志。

8. 用 replay 验证。

9. 真机验证：
   - 第一次启动：等全局重定位成功，并确认 JSON 写入。
   - 停止进程。
   - 第二次全新启动：确认读取 `latest.json`，10,000 点后进入 fast ICP。
   - 分别验证 `subsequent_relocalization_mode=fast_icp` 和 `subsequent_relocalization_mode=global`。
   - 观察 fast ICP 命中和 `merged_map` 对齐。

## 13. 当前计划不做的扩展

以下内容不进入第一版：

- 保存每一次 2 秒重定位的 TF 历史。当前需求只保存每次运行第一次成功发布的 TF。
- 多地图自动选择最近 TF。第一版按 `map_file` 分目录，读取当前地图的 `latest.json`。
- 自动判断 JSON 是否过期。第一版通过 fallback 全局 RANSAC 兜底。
- UI/CLI 单独管理缓存文件。第一版只通过配置开关和文件路径控制。
