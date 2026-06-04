# Open-Goal Visual Navigation

基于 WildOS (RADIO + SigLIP2) 开放词汇目标搜索的 DimOS 导航模块。

## 架构

```
Agent (MCP Client)
  │ search_target("orange flag")
  ▼
OpenGoalNavigationSkillContainer       ← dimos/agents/skills/navigation_open_goal.py
  │ 每帧 camera image → open-goal pipeline
  ▼
WildOSInference                        ← dimos/navigation/open_goal/wildos_inference.py
  │ RADIO backbone → SigLIP2 text-image matching → binary mask → 3D position
  ▼
┌─────────────────────────────────────────────────
│  _vendor/explorfm/                  ← ExploRFM + ExploRFMInference (NVIDIA RADIO 包装)
│  _vendor/nvidia_radio/              ← RADIO backbone, SigLIP2/NACLIP adaptors
│  模型权重: data/models_wildos/      ← frontier_head.ckpt, trav_head.ckpt, siglip2/*
└─────────────────────────────────────────────────
  │
  ▼
NavigationInterfaceSpec  →  _navigate_to(goal_pose)  →  robot moves toward target
```

## 数据流

```
RGB Image (1280×720)
    │
    ▼
WildOSInference.forward(rgb)
    │
    ├── traversability map (H×W float)
    ├── frontier map (H×W float)
    └── ad_spatial_features (D×h×w)  ← SigLIP2-adapted text-aligned features
            │
            ▼
WildOSInference.localize_object(feats, text_emb)
    │ cosine_similarity(text, spatial) → threshold 0.09 → binary mask
    ▼
Binary Mask (H×W uint8)
    │
    ▼
WildOSInference.estimate_3d_position(mask, K, camera_height)
    │ 最低像素点 → back-project → 地平面交点
    ▼
(x, y, z) 相机光心坐标系
    │
    ▼
_camera_to_goal_pose(pos_3d)
    │ 相机光心 → base_link → odom → map frame
    ▼
PoseStamped (map frame) → navigation.set_goal() + goal_pose.publish()
```

## 搜索终止条件

1. **成功**: 距离目标 < 1m + VLM 视觉确认（目标占画面 >5%）→ 自动停止移动
2. **超时**: 120 秒未到达 → 取消导航, 返回失败
3. **目标丢失**: 连续 10 帧无检测 → 取消当前 goal, 重新编码文本特征恢复搜索
4. **手动停止**: `stop_search()` 取消导航并清理搜索状态

## 模型文件

所需文件放在 `data/models_wildos/`:

```
data/models_wildos/
  ├── frontier_head.ckpt         # 前沿检测头
  ├── trav_head.ckpt             # 可通行区域检测头
  └── (SigLIP2 adaptor 权重文件)  # 由 nvidia_radio 自动加载
```

RADIO backbone (`c-radio_v3-b`) 从 HuggingFace 自动下载，首次运行后缓存在 `~/.cache/`。

## 蓝图

```bash
# replay 模式（离线测试）
dimos --replay run unitree-go2-agentic-open-goal-search

# 实机模式
dimos run unitree-go2-agentic-open-goal-search --robot-ip 192.168.123.161
```

## Skills（Agent 工具）

| Skill | 描述 |
|-------|------|
| `search_target(target_name)` | 启动开放词汇目标搜索，用 WildOS 模型在画面中查找目标，估计 3D 位置并导航过去 |
| `stop_search()` | 停止当前搜索，取消导航，清理状态 |
| `tag_location(name)` | 标记当前位置到空间记忆 |
| `navigate_with_text(query)` | 用自然语言导航（标记位置 / 视野内物体 / 语义地图） |
| `stop_navigation()` | 停止移动 |

## 可视化输出

### DimOS Topics

| Topic 流 | 类型 | 内容 |
|----------|------|------|
| `goal_pose` | `PoseStamped` (Out) | 每次推理得到的 map 坐标系目标位置 |
| `debug_viz` | `Image` (Out) | RGB 图上叠加: 红色蒙版=目标mask, 绿色=可通行区域, 黄色十字=3D估计点 |

### 本地 Debug 模式

在 blueprint 中传入 `debug_save_dir` 参数，每帧推理结果另存为 JPEG:

```python
OpenGoalNavigationSkillContainer.blueprint(debug_save_dir="/tmp/open_goal_debug")
```

保存格式: `open_goal_000000.jpg`, `open_goal_000001.jpg`, ...

## 参数

### WildOSInference

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `device` | `"cuda"` 或 `"cpu"` (自动检测) | 推理设备。强制 CPU: `device="cpu"` |
| `model_precision` | `"FP16"` | 模型精度。CPU 运行时建议 `"FP32"` |
| `model_version` | `"c-radio_v3-b"` | RADIO backbone 版本 |
| `ckpt_dir` | `data/models_wildos/` | 模型权重目录 |
| `mask_threshold` | 0.09 | cosine similarity 阈值 |
| `camera_height` | 0.5m | 相机距地面高度（Go2 前摄像头） |

### OpenGoalNavigationSkillContainer

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `search_timeout` | 120s | 单次搜索超时 |
| `max_no_detect_frames` | 10 | 连续无检测帧数后重新编码文本 |
| `debug_save_dir` | `""` | 非空时保存调试帧到此目录 |
| `static_scale_factor` | 0.75 | RADIO 输入缩放因子 |

## 测试

```bash
# 运行所有 open-goal 测试（不需要模型权重，23 个测试）
uv run pytest dimos/navigation/open_goal/tests/ -v

# 放好 checkpoint 文件后，额外的 5 个模型加载测试会自动激活
# 届时全部 28 个测试都会运行
```

### 测试结构

| 测试类 | 测试数 | 需要模型 | 描述 |
|--------|--------|---------|------|
| `TestWildOSInferenceLoading` | 5 | ✓ checkpoint | 模型加载、text encoding、forward、localize、端到端 pipeline |
| `TestWildOSInferencePure` | 9 | — | `estimate_3d_position`、`render_debug_viz` 纯函数测试 |
| `TestOpenGoalSkill` | 14 | — | Skill 逻辑：坐标转换、停止/搜索、导航、VLM 确认、相机内参 |

## 术语对应

原始 WildOS 论文/NVIDIA RADIO 与 DimOS 集成的命名对照:

| WildOS 原文 | DimOS 集成 |
|------------|-----------|
| VLN (Visual Language Navigation) | Open-Goal（开放目标搜索） |
| `frontier_head.ckpt` | 前沿检测 → 暂未使用, 预留 |
| `trav_head.ckpt` | 可通行区域 → 可视化输出 |
| RADIO backbone | C-RADIOv3-B, HuggingFace 自动下载 |
| SigLIP2 adaptor | 文本-图像对齐 → `localize_object()` |

## 依赖

- `torch`, `timm`, `einops`, `opencv-python`
- vendored: `_vendor/explorfm/`, `_vendor/nvidia_radio/`（来自 NVIDIA 开源 RADIO + WildOS 定制修改）

`_vendor/` 中的代码在 `wildos_inference.py` 加载时自动注册到 `sys.path`，无需手动安装。

## CPU 模式

如果无 GPU 或希望在 CPU 上验证推理管线:

```python
from dimos.navigation.open_goal import WildOSInference

model = WildOSInference(device="cpu", model_precision="FP32")
model.encode_text(["orange flag"])

# 用测试用的合成图像验证
import numpy as np
rgb = np.zeros((480, 640, 3), dtype=np.uint8)
trav, frontiers, ad_spatial = model.forward(rgb)
mask = model.localize_object(ad_spatial, rgb.shape[:2])
pos = model.estimate_3d_position(mask, K, camera_height=0.5)
```

## 引用

- WildOS: nebula2-wildos (opensource project)
- RADIO: NVIDIA Research — [radio-model](https://github.com/NVlabs/RADIO)
- SigLIP2: Google DeepMind — [siglip2](https://arxiv.org/abs/2503.00746)
