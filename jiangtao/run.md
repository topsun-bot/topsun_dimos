# Go2 录制与导航流程

> 环境：在 `topsun_dimos` 根目录执行 `deactivate 2>/dev/null; source .venv/bin/activate`，确认 `echo $VIRTUAL_ENV` 以 `topsun_dimos/.venv` 结尾。详见 `jiangtao/bugfix.md` §9/§13。

## Step 1: 录制（真机遛一圈）

```bash
dimos --robot-ip 192.168.12.1 run unitree-go2-memory
# 生成 recording_go2.db
```

## Step 2: 离线 PGO 导出 premap

前置：需安装 `gtsam-extended`（`uv pip install "gtsam-extended>=4.3a1.post1"` 或 `uv sync --extra mapping`）。

```bash
dimos map global recording_go2 --export
# 生成 ./recording_go2.pc2.lcm
# CUDA 异常时加 --device CPU:0
```

## Step 3: 重定位导航

回放测试：

```bash
dimos --replay --replay-db recording_go2 run unitree-go2-relocalization \
  -o relocalizationmodule.map_file=recording_go2
```

真机：

```bash
dimos --robot-ip 192.168.12.1 run unitree-go2-relocalization \
  -o relocalizationmodule.map_file=recording_go2
```

---

## 数据解析脚本

```bash
# 完整解析 (首次运行, 约 3 分钟)
python jiangtao/scripts/parse_recording_db.py

# 只重新生成点云 (图像和 odom 已有)
python jiangtao/scripts/parse_recording_db.py --skip-imgs --skip-odom

# 更改体素大小
python jiangtao/scripts/parse_recording_db.py --skip-imgs --skip-odom --voxel 0.03
```

## 可视化脚本

```bash
# 可视化地图 (保存图片)
python jiangtao/scripts/visualize_global_map.py --save output.png

# 可视化地图 (交互式, 需要显示器)
python jiangtao/scripts/visualize_global_map.py --backend open3d

# 去掉地面 (Z < 0)
python jiangtao/scripts/visualize_global_map.py --z-min 0 --save output.png

# 只看某个高度区间, 比如 0.2m ~ 0.8m (桌腿/椅子高度)
python jiangtao/scripts/visualize_global_map.py --z-min 0.2 --z-max 0.8 --save output.png

# 俯视图
python jiangtao/scripts/visualize_global_map.py --save topview.png --elevation 90 --azimuth -90
```

## 开机 odom 一致性诊断

同一物理位置固定 T 重定位多次结果不同时, 用此脚本验证 odom 开机原点是否漂移:

```bash
# 终端 A: 已启动 unitree-go2-relocalization
# 终端 B: 狗站定, 开机后尽快录
.venv/bin/python jiangtao/scripts/record_boot_consistency.py record \
  --mode lcm --duration 25 --tag boot1

# 需要 IMU / Unitree 原始 frame_id 时 (不要同时开 blueprint)
.venv/bin/python jiangtao/scripts/record_boot_consistency.py record \
  --mode webrtc --robot-ip 192.168.12.1 --duration 20 --tag boot1

# 对比多次
.venv/bin/python jiangtao/scripts/record_boot_consistency.py compare \
  jiangtao/cache/boot_consistency/boot1_* \
  jiangtao/cache/boot_consistency/boot2_*
```

输出目录: `jiangtao/cache/boot_consistency/<tag>_<time>_<mode>/`
关键看 `summary.json` 的 `odom_first` / `lidar_base_*` / `global_map_first`.

分析报告（含 odom 来源、0.31°/s 漂移、对建图/固定 T 的影响）:
`jiangtao/20260706-开机odom一致性与yaw漂移分析报告.md`

## odom txt 格式

每行一个值，共 11 行：


| 行   | 字段        | 说明         |
| --- | --------- | ---------- |
| 1   | timestamp | Unix 时间戳   |
| 2   | x         | 世界坐标 X (m) |
| 3   | y         | 世界坐标 Y (m) |
| 4   | z         | 世界坐标 Z (m) |
| 5   | qx        | 四元数 x      |
| 6   | qy        | 四元数 y      |
| 7   | qz        | 四元数 z      |
| 8   | qw        | 四元数 w      |
| 9   | yaw       | 偏航角 (rad)  |
| 10  | pitch     | 俯仰角 (rad)  |
| 11  | roll      | 翻滚角 (rad)  |
