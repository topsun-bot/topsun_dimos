# 直接通过 UnitreeWebRTCConnection 调取 Go2 数据

本目录的脚本演示**不通过 dimos blueprint**，直接用底层 `UnitreeWebRTCConnection`
从 Go2 上拿到 **点云 / 里程计 / 摄像头** 三种数据。

适用场景：
- 自己写一个独立小程序，想拿数据做点测试 / 录制 / 算法验证
- 在 Notebook 里交互式调试某个传感器
- 想搞清楚每种数据的真实结构和数值范围

如果你最终是要把模块挂到 dimos 流水线里跑，**还是用 `GO2Connection.blueprint()`** —— 那个是
正路，本目录只是教学和探索用。

---

## 一、`UnitreeWebRTCConnection` 是什么

文件：`dimos/robot/unitree/connection.py:81`

**底层是什么**：宇树官方的 WebRTC 数据通道（依赖 PyPI 包
`unitree-webrtc-connect-leshy`，对应 `unitree_webrtc_connect.webrtc_driver.UnitreeWebRTCConnection`，
代号 `LegionConnection`）。机器人和上位机用 **WebRTC** 对等连接，传感器数据走
DataChannel，视频走 RTP track。

**dimos 这层包了什么**：

1. **生命周期**：构造时自动 `connect()`；起一个后台 `asyncio` 事件循环跑在独立线程里，
   连上后通过 `MOTION_SWITCHER` 切到 `mode="ai"`（默认）
2. **回调 → Observable**：把宇树的 `pub_sub.subscribe(topic, cb)` 包成 `reactivex.Observable`，
   方便链式处理（`.pipe(ops.map(...))`）
3. **数据类型转换**：
   - 雷达：原始字典 → `PointCloud2`（`pointcloud2_from_webrtc_lidar`）
   - 里程计：原始字典 → `Odometry`/`PoseStamped`（`Odometry.from_msg`）
   - 视频：`av.VideoFrame` → `dimos.msgs.sensor_msgs.Image`（rgb24）
4. **控制 API**：`move(twist)`、`standup()`、`balance_stand()`、`liedown()`、
   `set_obstacle_avoidance(...)` 等

## 二、API 三层抽象

| 层 | 方法 | 数据类型 | 适合 |
|----|------|---------|------|
| **最高层（推荐）** | `lidar_stream()` / `odom_stream()` / `video_stream()` | `PointCloud2` / `Odometry` / `Image`（dimos 类型） | 直接拿就能用，自带 backpressure |
| **中间层** | `raw_lidar_stream()` / `raw_odom_stream()` / `raw_video_stream()` | 宇树原始字典 / `av.VideoFrame` | 想绕开 dimos 数据类型；自己解析字段 |
| **最底层** | `unitree_sub_stream(RTC_TOPIC[...])` | 任意主题原始字典 | 订阅 dimos 还没封装的主题（lowstate 之外的诊断主题等） |

宇树所有可订阅主题在 `unitree_webrtc_connect.constants.RTC_TOPIC`，常用：

| 主题 | 含义 | dimos 是否封装 |
|------|------|---------------|
| `ULIDAR_ARRAY` | 4D 激光点云 | ✓ `lidar_stream()` |
| `ROBOTODOM` | 里程计 | ✓ `odom_stream()` |
| `LOW_STATE` | 关节状态 / IMU / 电池 | ✓ `lowstate_stream()` |
| Video track | 高清相机 RGB（不走 datachannel） | ✓ `video_stream()` |
| `SPORT_MOD_STATE` | 步态状态 | 否（用 `unitree_sub_stream`） |
| `MOTION_SWITCHER` | 运动模式切换 | 否（控制类） |

## 三、三种数据的实际结构

### `PointCloud2`（来自 `lidar_stream()`）

文件：`dimos/msgs/sensor_msgs/PointCloud2.py`

最关键的方法：

```python
points, colors = cloud.as_numpy()
# points: np.ndarray (N, 3)，单位 m，xyz 在世界坐标系（已经过 SLAM 对齐）
# colors: np.ndarray (N, 3) 或 None（雷达没颜色，是 None）
print(f"点数 {len(points)}, x 范围 {points[:,0].min():.2f}~{points[:,0].max():.2f}")
```

字段：`cloud.frame_id`（默认 `"world"`）、`cloud.ts`（时间戳，秒）。

### `Odometry`（来自 `odom_stream()`，本质是 `PoseStamped`）

文件：`dimos/robot/unitree/type/odometry.py:76`

```python
odom.position.x        # float, m
odom.position.y
odom.position.z
odom.orientation.x     # 四元数 xyzw
odom.orientation.y
odom.orientation.z
odom.orientation.w
odom.orientation.euler # tuple (roll, pitch, yaw)，弧度
odom.frame_id          # "world"
odom.ts                # float 秒
```

### `Image`（来自 `video_stream()`）

文件：`dimos/msgs/sensor_msgs/Image.py:86`

```python
arr = img.as_numpy()   # np.ndarray (H, W, 3)，uint8，RGB（不是 BGR）
img.format             # ImageFormat.RGB
img.shape              # (H, W, 3)
img.frame_id           # "camera_optical"
img.ts                 # float 秒
```

注意 Go2 的相机是 **1280×720 @ ~14Hz**。

## 四、注意事项

1. **机器人必须开机 + 联网**：脚本要在和机器人**同网段**的电脑上跑
   （默认 IP `192.168.123.161`，机器人后台 STA 模式）
2. **第一次连接 ~3-5 秒**：WebRTC 握手 + ICE 协商
3. **不要重复连接**：一个进程一个 `UnitreeWebRTCConnection` 实例就够
4. **video_stream 第一次订阅时**会调 `switchVideoChannel(True)` 开通视频 track，
   要等 1-2 秒第一帧才到
5. **退出一定要 disconnect**，否则机器人 WebRTC 连接会卡住一段时间才释放
6. **本脚本不发任何运动指令**（不会让 Go2 动）。但 `__init__` 会自动切到
   `mode="ai"` —— 如果你已经在用 normal mode 控制，别用本脚本

## 五、运行方法

### 1. 准备环境

复用 dimos 的虚拟环境就行：

```bash
cd /home/jiangtao/huazhijian/gitlab/dimos
source .venv/bin/activate
# 确认依赖装了
python -c "from dimos.robot.unitree.connection import UnitreeWebRTCConnection; print('OK')"
```

如果上面这行报错说 `unitree_webrtc_connect` 找不到，先：

```bash
uv sync --all-extras --no-extra dds
```

### 2. 设置机器人 IP

```bash
export ROBOT_IP=192.168.123.161   # 默认值，按你的实际网段改
```

或者运行时用 `--ip` 参数。

### 3. 跑 demo

```bash
# demo A: 实时订阅 + 打印统计（按 Ctrl+C 退出）
python jiangtao/unitree_connection/subscribe_demo.py

# demo B: 录制到磁盘（默认存到 ./recordings/<timestamp>/）
python jiangtao/unitree_connection/save_demo.py --duration 10
```

### 4. 期望输出

`subscribe_demo.py` 跑起来大约这样：

```
[2026-05-09 19:55:01] 已连接到 192.168.123.161
[2026-05-09 19:55:02] 开始订阅 lidar / odom / video ...
[T=  2.0s] lidar  fps= 9.5  最新: 12345 个点, 范围 x[-3.21,4.55]m  y[-2.10,3.88]m
[T=  2.0s] odom   fps=49.7  最新: pos(0.10, -0.05, 0.32) yaw=  3.1°
[T=  2.0s] video  fps=14.2  最新: (720, 1280, 3) RGB
...
```

## 六、目录文件

```
unitree_connection/
├── README.md          # 本文件
├── subscribe_demo.py  # 实时订阅+打印统计
├── save_demo.py       # 录制到磁盘（点云 .npy / odom .csv / 图像 .jpg）
└── lib.py             # 公共工具：连接管理、信号处理、格式化
```

## 七、不想用本目录的最小代码长什么样

如果你只是想看"最骨架的几行代码"，下面就是。本目录的脚本是这个的扩展版。

```python
import time
from dimos.robot.unitree.connection import UnitreeWebRTCConnection

conn = UnitreeWebRTCConnection(ip="192.168.123.161")

# 三个 Observable
lidar_obs = conn.lidar_stream()
odom_obs = conn.odom_stream()
video_obs = conn.video_stream()

# 订阅
sub1 = lidar_obs.subscribe(lambda c: print(f"lidar: {c.as_numpy()[0].shape}"))
sub2 = odom_obs.subscribe(lambda o: print(f"odom: {o.position}"))
sub3 = video_obs.subscribe(lambda i: print(f"img:  {i.shape}"))

try:
    time.sleep(10)
finally:
    sub1.dispose(); sub2.dispose(); sub3.dispose()
    conn.disconnect()
```
