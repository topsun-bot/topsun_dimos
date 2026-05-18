# macOS 上 Rerun 看不到 Go2 摄像头画面的修复

> 适用平台:macOS (Apple Silicon)
> 影响 Blueprint:`unitree-go2`、`unitree-go2-agentic` 以及所有走 `pSHMTransport` 的图像/点云流
> 严重程度:静默丢消息 (publisher 和 subscriber 看起来都正常运行,但完全没有数据流过)

## 一句话总结

`pSHMTransport.__reduce__` 在 pickle 时丢失了 `default_capacity`,导致发布端进程和订阅端进程在跨进程 pickle 后选用了不同的 SHM segment 名,publisher 写入的 segment 和 subscriber 监听的 segment 完全不同,所有帧静默丢弃。

修复方法是让 `__reduce__` 保留原始构造参数,Rerun viewer 升级到与 SDK 匹配的 0.30 系列。

## 症状

启动 `dimos --replay run unitree-go2` 或 `dimos run unitree-go2` 后,Rerun viewer 的 Camera 面板完全空白,但是:

- Sources 树里 `world/color_image` entity **存在**
- Timeline 上 `color_image` 那一行**有数据点**(密集的小三角)
- 右侧 Selection panel 显示 `Visual bounds 2D Range 1280 × 720`(尺寸是对的)
- 3D 面板里点云、地图、costmap 都正常显示
- 没有任何错误日志

肉眼看上去"应该有数据",但视口里就是不渲染图像。

## 排查过程

### 误区 1:Rerun SDK ↔ Viewer 版本不一致

最初看到日志里的 warning:

```
⚠ The version of the Rerun Viewer available on your PATH does not match the version of your Rerun SDK ⚠
> Rerun Viewer: v0.30.0-alpha.1+dev (executable: "dimos-viewer")
> Rerun SDK: v0.29.2
```

Rerun 0.29 → 0.30 之间 `Image` archetype 的字段做过重构,假设 viewer 0.30 拿到 0.29 SDK 发的 archetype 时会静默不渲染。把 `rerun-sdk` 升到 `0.30.0a6` 后版本警告消失,**但 Camera 面板仍然空白**。

这一步**不是根因**,但属于必要的兼容性修复 — 跨大版本的 schema mismatch 会引起其他奇怪问题。

### 误区 2:`world/color_image` entity 出现 = 帧到了 Rerun

看到 Sources 树里有 `world/color_image`,加上 timeline 有数据点,直觉以为图像已经到了 Rerun,只是渲染层有问题。

实际上 `_convert_camera_info` 把 `CameraInfo` 转出来的 archetype 也是 log 到 `/world/color_image` 这个 entity:

```184:188:dimos/robot/unitree/go2/blueprints/basic/unitree_go2_basic.py
def _convert_camera_info(camera_info: Any) -> Any:
    return camera_info.to_rerun(
        image_topic="/world/color_image",
        optical_frame="camera_optical",
    )
```

它产生的是 `Pinhole` archetype(只含相机内参 + image plane 尺寸 1280×720),**完全不包含像素数据**。所以:

- entity 是被创建出来的(Pinhole archetype 在用)
- Visual bounds 2D Range 显示 1280×720(从 Pinhole 内参推出来)
- timeline 上的数据点全部是 `CameraInfo` 每帧 publish 的 Pinhole 更新
- 但 **`Image` archetype 一帧都没到**

为了证明这一点,在 `_ColorImageSHMSubscriber.subscribe_all` 里加 debug log,每收到一帧打印 shape/dtype/format。重启 dimos 后日志里**完全没有任何 SHM frame 行**,跟"SHM 通道根本没数据"完全吻合。

### 根因发现:`pSHMTransport.__reduce__` 丢失 capacity

在 `dimos/core/transport.py` 里:

```python skip
class pSHMTransport(PubSubTransport[T]):
    def __init__(self, topic: str, **kwargs) -> None:
        super().__init__(topic)
        self.shm = PickleSharedMemory(**kwargs)  # ← kwargs 里有 default_capacity

    def __reduce__(self):
        return (pSHMTransport, (self.topic,))   # ← kwargs 全部丢失!
```

而 SHM segment 命名 (`dimos/protocol/pubsub/impl/shmpubsub.py`):

```python skip
def _names_for_topic(topic: str, capacity: int) -> tuple[str, str]:
    h = hashlib.blake2b(f"{topic}:{capacity}".encode(), digest_size=8).hexdigest()
    return f"psm_{h}_data", f"psm_{h}_ctrl"
```

**`capacity` 是 segment 名哈希的一部分。**

时序:

1. 主进程 `import unitree_go2_basic` 时,在模块顶层执行:
   ```python skip
   pSHMTransport("color_image", default_capacity=DEFAULT_CAPACITY_COLOR_IMAGE)  # 6220800
   ```

<!--Error:-->
```
File "/var/folders/cq/fll2q9993y58l355fsz1z3jh0000gn/T/tmpzz499dc4.py", line 1
    pSHMTransport("color_image", default_capacity=DEFAULT_CAPACITY_COLOR_IMAGE)  # 6220800
IndentationError: unexpected indent

Exit code: 1
```
2. `ModuleCoordinator` 把这个 `pSHMTransport` 对象 pickle 后送到 `GO2Connection` 的 forkserver worker
3. Worker 进程 unpickle 时,`__reduce__` 返回 `(pSHMTransport, ("color_image",))`,重建调用 `pSHMTransport("color_image")` —— 没传 `default_capacity`,用了默认 `3686400`
4. Worker 进程的 publisher 把图像写到 segment `psm_<hash("color_image:3686400")>_data`
5. Rerun bridge 进程里的 `_ColorImageSHMSubscriber` 用 `default_capacity=DEFAULT_CAPACITY_COLOR_IMAGE=6220800` 显式创建,attach 到 segment `psm_<hash("color_image:6220800")>_data`
6. **两个 segment 名完全不同,publisher 和 subscriber 永远碰不到一起**

Pickle round-trip 验证(修复前):

```text
orig capacity:    6220800   →  psm_a8e59af2d97f9c7f_data
rebuilt capacity: 3686400   →  psm_832a9bc625d1d502_data
```

## 修复

### 1. 让 `pSHMTransport.__reduce__` 保留构造参数

`dimos/core/transport.py`:

```python skip
def _reconstruct_pshm_transport(topic: str, kwargs: dict[str, Any]) -> "pSHMTransport[Any]":
    return pSHMTransport(topic, **kwargs)


class pSHMTransport(PubSubTransport[T]):
    def __init__(self, topic: str, **kwargs) -> None:
        super().__init__(topic)
        # Preserve init kwargs so that pickled instances (e.g. those shipped to
        # forkserver workers) reconstruct with the same configuration.
        # Without this, default_capacity is lost on the unpickle side and the
        # SHM segment names diverge (segment names are deterministic on
        # capacity), causing the publisher and subscriber to attach to
        # different segments and silently drop all messages.
        self._init_kwargs: dict[str, Any] = dict(kwargs)
        self.shm = PickleSharedMemory(**kwargs)

    def __reduce__(self):
        return (_reconstruct_pshm_transport, (self.topic, self._init_kwargs))
```

Pickle round-trip 验证(修复后):

```text
orig capacity:    6220800   →  psm_a8e59af2d97f9c7f_data
rebuilt capacity: 6220800   →  psm_a8e59af2d97f9c7f_data   ✅ 一致
```

### 2. 在 Go2 blueprint 里把 SHM 色图接入 Rerun bridge

`dimos/robot/unitree/go2/blueprints/basic/unitree_go2_basic.py`:

macOS 默认就把 `color_image` 流的 transport 重映射成 `pSHMTransport`(避开 macOS 上多播 UDP 的高带宽问题):

```python skip
_mac_transports: dict[tuple[str, type], pSHMTransport[Image]] = {
    ("color_image", Image): pSHMTransport(
        "color_image", default_capacity=DEFAULT_CAPACITY_COLOR_IMAGE
    ),
}
```

但 `vis_module` 默认 `rerun_config.setdefault("pubsubs", [LCM()])`,只订阅 LCM。所以图像虽然在 SHM 里跑着,Rerun 永远收不到。

加一个 SHM bridge 类把 `color_image` SHM 流暴露给 RerunBridgeModule:

```python skip
class _ColorImageSHMSubscriber:
    """Expose the macOS color_image shared-memory stream to the Rerun bridge."""

    def __init__(self) -> None:
        self.shm: PickleSharedMemory | None = None

    def start(self) -> None:
        self.shm = PickleSharedMemory(default_capacity=DEFAULT_CAPACITY_COLOR_IMAGE)
        self.shm.start()
        # ... diagnostic log: prints capacity + segment name ...

    def stop(self) -> None:
        if self.shm:
            self.shm.stop()
            self.shm = None

    def subscribe_all(self, callback: Any) -> Any:
        if self.shm is None:
            self.start()
        # ... wraps each frame and forwards to RerunBridge with topic "/color_image"
        # so it maps to entity world/color_image ...
        return self.shm.subscribe("color_image", on_color_image)
```

然后在 `rerun_config` 里把它加到 `pubsubs`:

```python skip
rerun_config = {
    "blueprint": _go2_rerun_blueprint,
    "pubsubs": [LCM(), _ColorImageSHMSubscriber()] if platform.system() != "Linux" else [LCM()],
    # ...
}
```

注意 callback 里把 topic 传成 `/color_image`(带前导斜杠),这样 RerunBridge 的 `entity_prefix="world"` + topic split 出来的路径会拼成 `world/color_image`,跟 Camera view origin 一致。

### 3. 锁定 Rerun SDK ↔ Viewer 版本

`pyproject.toml`:

```toml
# NOTE: rerun-sdk must match the major/minor of dimos-viewer (0.30.x), otherwise
# the viewer silently drops Image archetypes due to schema mismatch.
"rerun-sdk>=0.30.0a6,<0.31",
"dimos-viewer==0.30.0a6.dev99",
```

防止下次 `uv sync` 退回到 0.29 系列(里面的 schema 跟 dimos-viewer 0.30 alpha 不兼容)。

## 如何验证修复

启动 dimos 后,在日志里应该能看到这两行:

```text
[inf][.../unitree_go2_basic.py] ColorImageSHMSubscriber started: capacity=6220800
                                 (DEFAULT_CAPACITY_COLOR_IMAGE) segment=psm_a8e59af2d97f9c7f_data
[inf][.../unitree_go2_basic.py] color_image SHM frame #1 shape=(720, 1280, 3) dtype=uint8 format=ImageFormat.RGB
```

`color_image SHM frame #1` 这行一旦出现就说明 SHM 通道连通了。Rerun viewer 里 Camera 面板会开始显示实时画面。

如果出现 #1 启动 log 但没有 #N 的帧 log,说明 capacity 已经一致但 SHM channel 自身有问题(比较罕见,需要继续往 `shmpubsub.py` 的 IPC 同步原语方向查)。

## 修改的文件清单

| 文件 | 类型 | 说明 |
|---|---|---|
| `dimos/core/transport.py` | bug fix | `pSHMTransport.__reduce__` 保留 `_init_kwargs`(根因修复) |
| `dimos/robot/unitree/go2/blueprints/basic/unitree_go2_basic.py` | feature | 加 `_ColorImageSHMSubscriber` + 接入 `rerun_config["pubsubs"]` + 诊断 log |
| `pyproject.toml` | dep pin | `rerun-sdk` 限制在 `0.30.x` 系列 |
| `scripts/dimos-macos-local.sh` | helper | 临时把 macOS 多播路由设到 `lo0`(独立问题,见下) |

## 附:macOS 多播路由助手脚本

跟本 issue 无关但容易和它一起遇到的另一个问题:macOS 26 (Tahoe) 上 LCM 多播默认走 `en0` 但常被系统标记 `reject`,导致 `OSError: [Errno 49] Can't assign requested address`。

直接 `sudo route -n add -net 224.0.0.0/4 -interface lo0` 会让本机其他 mDNS / 多播服务全挂(整机断网)。

`scripts/dimos-macos-local.sh` 是一个临时路由助手:

1. 启动时记住当前 `224.0.0.0/4` 走哪个网卡
2. 把它临时切到 `lo0` 然后启动 dimos
3. dimos 退出时(Ctrl-C / SIGTERM / 异常退出)自动还原回原网卡

用法:

```bash
scripts/dimos-macos-local.sh dimos run unitree-go2
scripts/dimos-macos-local.sh dimos run unitree-go2-agentic
# 等价于:
scripts/dimos-macos-local.sh   # 不传参数时默认 dimos --replay run unitree-go2
```

## 后续改进建议

1. **统一 SHM transport 的跨进程契约**:把 `__reduce__` 模式作为 PR 推到上游(`dimos/core/transport.py`),否则将来其他 transport 加可调参数时都可能再踩同样的坑。
2. **`vis_module` 自动接管 SHM transport**:`dimos/visualization/vis_module.py` 可以扫描 blueprint 里所有的 `pSHMTransport`,自动给每个 SHM topic 创建一个 SHM subscriber 加到 `pubsubs`,这样个别 blueprint 就不用手写 `_ColorImageSHMSubscriber` 这种胶水类了。
3. **`pSHMTransport` 集成自检**:启动时由 publisher 通过 ctrl segment 写一个 magic byte,subscriber 主动校验,segment mismatch 时直接报错而不是静默丢消息。
