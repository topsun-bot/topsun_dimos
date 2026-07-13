# Go2 录制与导航流程

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

---

## Step 4: 空间找物（单次会话, 无 premap）

环境变量：

```bash
export UNITREE_AES_128_KEY='0a7d97828984ec332984571244318f30'
export OPENAI_API_KEY="sk-fb774f182a3c4c28aaa8b6878ee32b60"
export OPENAI_BASE_URL="https://api.deepseek.com"
export DIMOS_VLM_API_KEY="EMPTY"
export DIMOS_VLM_BASE_URL="http://10.10.153.172:8080/v1"
export DIMOS_VLM_MODEL_NAME="/Users/dijia/models/Qwen3-VL-8B-4bit"
export DIMOS_ROTATION_STEP_DEG=60   # 每次旋转角度
export DIMOS_ROOM_SCAN_ROTATIONS=3  # 旋转次数

# 云端 VLM fallback (本地不可用时自动切换)
export DIMOS_VLM_CLOUD_API_KEY="sk-ws-H.EDDREER.p3xq.MEUCIQDdBEwnddKuZEg2EXYSeqpWRBGlATod78ixRpzjHrew8wIgH0sJMDzpuZJdyORYcnsOQLDuP5gpF5ZDqfnW_WDpbto"
export DIMOS_VLM_CLOUD_BASE_URL="https://ws-sy431890c06kqcoz.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
export DIMOS_VLM_CLOUD_MODEL_NAME="qwen3-vl-plus"
export DIMOS_VLM_FALLBACK_COOLDOWN=60

# unset 代理是为了避免局域网请求(Go2 WebRTC信令 / 本地VLM)走代理导致连接失败
# 云端VLM走公网, 如果机器没有直连能力需要保留代理, 改用 NO_PROXY 排除局域网IP即可
unset ALL_PROXY all_proxy HTTP_PROXY http_proxy HTTPS_PROXY https_proxy
```

真机：

```bash
dimos --robot-ip 10.206.176.64 run unitree-go2-agentic-deepseek --disable security-module
```

找物交互：

```bash
dimos tell '标记一下当前是办公室'
dimos tell '去找饮水机'
```

## Step 5: 重定位空间找物（基于 premap, 跨 session 复用空间记忆）

前置：已完成 Step 1 ~ Step 2, 生成了 `recording_go2.pc2.lcm`。

环境变量同 Step 4。

真机：

```bash
dimos --robot-ip 10.206.176.64 run unitree-go2-relocalization-memory-agentic-deepseek \
  --disable security-module \
  -o relocalizationmodule.map_file=recording_go2
```

找物交互：

```bash
dimos tell '标记一下当前是办公室'
dimos tell '去找饮水机'
```
