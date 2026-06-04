# WildOS Open-Goal 集成开发总结

> 状态: **系统功能测试中** | 日期: 2026-06-01

---

## 一、概述

将 WildOS 开放词汇视觉导航算法（RADIO + SigLIP2）集成到 DimOS 中，使 Unitree Go2 机器人能够根据自然语言目标描述（如 "橙色旗子"）自主搜索并导航到目标物体。

### 核心模块

| 模块 | 路径 | 职责 |
|------|------|------|
| WildOSInference | `dimos/navigation/open_goal/wildos_inference.py` | 模型加载、文本编码、图像推理、3D位置估计 |
| OpenGoalNavigationSkillContainer | `dimos/agents/skills/navigation_open_goal.py` | 搜索启动/停止、逐帧处理、目标确认、导航控制 |
| Blueprint | `dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic_open_goal_search.py` | 可运行的机器人蓝图组合 |

### 模型依赖

- **RADIO backbone** (`c-radio_v3-b_half.pth.tar`): 视觉特征提取 + 可通行性 + 前沿
- **SigLIP2 adaptor** (`google/siglip2-so400m-patch16-naflex`): 文本-图像对齐

**注意**: 已移除 Qwen VL 和 SpatialMemory 依赖。目标确认完全由 WildOS 自身的 binary_mask + 3D 位置估算完成。

---

## 二、集成开发过程

### 阶段 1: 代码归并（前置 session）

- 移除 `_vendor/` 目录和 `sys.path` hack，改为直接相对导入
- 修复蓝图扫描器误扫 `nn.Module` 子类的问题（新增路径排除规则）
- 编写测试用例覆盖模型加载、推理、坐标变换

### 阶段 2: 本地模型加载修复

**问题**: `AutoModel.from_pretrained()` 尝试从 HuggingFace 下载模型，超时失败。

**修复**:
- `siglip2_adaptor.py`: 将 `cache_dir` 修正为 `data/models_wildos/siglip2/`（HF 缓存目录结构），并添加 `local_files_only=True`
- `wildos_inference.py`: 优先使用本地 `.pth.tar` 文件作为 RADIO checkpoint
- `naclip.py`: `NAClipAttention.forward()` 添加 `attn_mask=None` 参数，匹配 timm Attention 签名

### 阶段 3: 推理与可视化联调

**帧率限制**: 默认 5fps 处理（`_open_goal_frame_hz = 5.0`），因为 WildOS 推断无法实时运行

**Rerun 可视化**: 在 open-goal 蓝图中自定义 `vis_module`，通过 `_convert_camera_info` 在 `world/wildos_image` 写入 Pinhole，确保可视化在 Rerun 中可见

### 阶段 4: VLM/SpatialMemory 依赖移除

**原因**: 搜索功能与 DimOS 原有技能兼容性太差，agent 频繁误调用其他 skill（wait、navigate_with_text 等），导致搜索任务失败。

**移除内容**:
- `_vl_model`（Qwen VL）及所有 VLM 相关代码：`_ensure_vlm_client`、`_draw_vlm_bbox`、`_confirmed_bbox`
- `_spatial_memory`（SpatialMemory）及所有空间记忆相关代码：`tag_location` skill、`navigate_with_text` skill
- `_object_tracking` 相关代码：`_navigate_to_object`、`_get_bbox_for_current_frame`

**新的终止确认逻辑** (`_confirm_target_close`):
- 完全基于 WildOS 自身的 binary_mask 覆盖率和 3D 位置估算
- mask 覆盖率 ≥ 0.5% 图像像素
- 3D 深度 cz 在 0.1m–2.5m 范围内
- 需要连续 3 帧满足条件才确认 `_target_confirmed = True`
- 距离 < 1.0m 且 `_target_confirmed` 时终止搜索

### 阶段 5: Agent 行为优化

- `wait` skill 的 `@skill` 装饰器临时禁用（agent 会在 search_target 后误调用）
- `WavefrontFrontierExplorer` 在蓝图中禁用（不需要该功能）
- 系统提示词强化：明确指示 search_target 后不做任何操作，等待 tool_update

---

## 三、设计说明

### 3.1 数据流

```
用户/Agent 说 "搜索橙色旗子"
    ↓ MCP
McpClient → search_target("橙色旗子")
    ↓
WildOSInference.encode_text(["橙色旗子"])  → SigLIP2 文本特征 (L2归一化)
    ↓ 每帧 5fps (stream callback 驱动)
color_image → _on_color_image → ThreadPoolExecutor.submit(_process_open_goal_frame)
    ↓ (异步线程池, 不阻塞 callback 线程)
_process_open_goal_frame
    ↓
WildOSInference.forward(rgb)
    → RADIO backbone: traversability (H,W) + frontiers (H,W) + ad_spatial (D,h,w)
    ↓
localize_object(ad_spatial)
    → 余弦相似度 sim = text_feats · ad_spatial
    → 二值掩码 (sim > 0.09)
    ↓ (掩码像素 < 10 → 目标丢失, 连续10帧则重新编码文本)
estimate_3d_position(binary_mask, K, camera_height=0.5)
    → 取掩码最低点 (u, v) — 最接近地面的像素
    → 反投影射线: ray = ((u-cx)/fx, (v-cy)/fy, 1)
    → 地面交点: depth = camera_height / ray.y
    → 相机光学坐标系 3D 点: pos = depth * ray
    ↓
camera_to_goal_pose(pos_3d)
    → 光学坐标系 → base_link: bx=0.3+cz, by=-cx, bz=-cy
    → base_link → map: odom_quat.rotate(base_offset) + odom_pos
    → PoseStamped(position, orientation, frame_id="map")
    ↓
_navigation.set_goal(pose) → 导航栈规划路径
    ↓ 循环直到 dist < 1.0m 且 _target_confirmed (连续3帧确认)
_stop_open_goal_search() → 取消导航 + 重置状态
tool_update → agent 收到指令 → 调用 speak / execute_sport_command("Sit")
```

#### goal_pose 坐标变换细节

1. **3D 位置估计** (`estimate_3d_position`): 取掩码中 y 坐标最大（最接近地面）的像素 `(u, v)`，通过相机内参 `K` 反投影为射线 `ray = ((u-cx)/fx, (v-cy)/fy, 1)`，与地面平面 `y = camera_height` 求交，得到相机光学坐标系下的 3D 点。

2. **光学 → base_link** (`_camera_to_goal_pose`): 相机安装在 base_link 前方 0.3m 处。光学坐标系 (x=右, y=下, z=前) 转 base_link (x=前, y=左, z=上): `bx = 0.3 + cz`, `by = -cx`, `bz = -cy`。

3. **base_link → map**: 使用里程计 (`odom`) 的位置和姿态四元数，将 base_link 偏移旋转到 map 坐标系并加上当前位置: `goal_world = odom_quat.rotate(base_offset) + odom_pos`。

### 3.2 搜索终止条件

终止判定完全由 `_target_confirmed` 标志位决定，需同时满足：

| 条件 | 参数 | 说明 |
|------|------|------|
| 3D 距离 | `< 1.0m` | 地面平面投影估算距离 |
| Mask 覆盖率 | `≥ 0.5%` | binary_mask 非零像素占图像比例 |
| 深度范围 | `0.1m < cz < 2.5m` | 目标在合理深度范围内 |
| 稳定性 | 连续 3 帧 | 连续满足以上条件才确认 |

终止后通过 `tool_update` 通知 Agent 执行到达动作（speak + execute_sport_command("Sit")）。

### 3.3 技能接口

```python
@skill
def search_target(self, target_name: str) -> str:
    """搜索开放词汇目标物体，估计3D位置并导航过去。
    到达目标后自动坐下并语音播报。"""

@skill
def stop_search(self) -> str:
    """停止当前搜索。"""

@skill
def stop_navigation(self) -> str:
    """立即停止机器人移动。"""
```

### 3.4 关键配置

| 参数 | 默认值 | 位置 | 说明 |
|------|--------|------|------|
| `_open_goal_frame_hz` | 5.0 | `__init__` | 推理帧率限制 |
| `_max_no_detect_frames` | 10 | `__init__` | 目标丢失重试阈值 |
| `model_precision` | FP16 | `WildOSInference()` | 模型精度 |
| `device` | cuda(默认) | `WildOSInference()` | 推理设备 |

### 3.5 Rerun 可视化

Rerun 布局面板（从左到右）:
1. **Camera** (2D): `world/color_image` — Go2 相机原始画面
2. **WildOS Debug** (2D): `world/wildos_image` — 红色掩码叠加 + 绿色可通行性 + 黄色十字准星
3. **3D**: `world` — 点云、地图、导航路径

`wildos_image` 独立 2D 面板，通过 blueprint 中自定义 `_convert_camera_info` 添加 Pinhole。

---

## 四、当前状态

### 当前进展

核心功能已集成完毕：WildOS 模型本地加载、推理管线、3D 位置估计、搜索技能 MCP 集成、Rerun 可视化、帧率限制。目标确认完全基于 WildOS 自身的 binary mask + 3D 位置，不再依赖外部 VLM。到达目标后自动坐下并语音播报。

### 设计决策

- **无 VLM 依赖**: 移除 Qwen VL，完全信任 WildOS 的 binary mask 和 3D 位置估算。
- **无 SpatialMemory 依赖**: 移除空间记忆模块，简化搜索流程。
- **终止逻辑**: 距离 < 1.0m + mask 覆盖率 ≥ 0.5% + 深度合理 + 连续 3 帧稳定确认。
- **到达动作**: 终止后通过 `tool_update` 引导 Agent 调用 `speak` + `execute_sport_command("Sit")`。
- **搜索状态管理**: 搜索在 `ThreadPoolExecutor(max_workers=1)` 异步线程中运行，`_on_color_image` 回调仅提交任务到线程池后立即返回，不阻塞 stream callback 线程。

### 已知情况

- 目标丢失时 WildOS 推断与导航状态可能不一致（推断先停）
- GPU 内存管理：连续运行多个模型测试时可能 CUDA OOM
- Agent 在 search_target 执行期间仍可能尝试调用其他 skill（通过系统提示词约束）

### 运行方式

```bash
# 本地回放测试
dimos --replay run unitree-go2-agentic-open-goal-search --daemon

# 真实机器人
dimos run unitree-go2-agentic-open-goal-search --robot-ip 192.168.123.161

# 发送搜索指令
dimos agent-send "搜索橙色旗子"

# 查看运行状态
dimos status
dimos log -f
```

### 测试

```bash
# 全部测试（需要 GPU + 模型文件）
uv run pytest dimos/navigation/open_goal/tests/test_wildos_inference.py -v --timeout=300

# 跳过模型加载测试（仅逻辑测试）
uv run pytest dimos/navigation/open_goal/tests/test_wildos_inference.py -v \
    -k "not TestWildOSInferenceLoading"

# 更新蓝图注册表
uv run pytest dimos/robot/test_all_blueprints_generation.py
```

---

## 五、相关文件清单

| 文件 | 变更类型 |
|------|----------|
| `dimos/navigation/open_goal/wildos_inference.py` | 修改（移除 sys.path、本地 pth.tar 加载） |
| `dimos/navigation/open_goal/explorfm/explorfm_model.py` | 修改（相对导入） |
| `dimos/navigation/open_goal/nvidia_radio/radio/siglip2_adaptor.py` | 修改（本地加载 + cache_dir 修正） |
| `dimos/navigation/open_goal/nvidia_radio/radio/naclip.py` | 修改（attn_mask 兼容） |
| `dimos/navigation/open_goal/nvidia_radio/hf_hub.py` | 修改（相对导入） |
| `dimos/agents/skills/navigation_open_goal.py` | 重写（移除 VLM/SpatialMemory，WildOS 原生确认） |
| `dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic_open_goal_search.py` | 修改（蓝图组合 + wildos_image Pinhole） |
| `dimos/robot/unitree/unitree_skill_container.py` | 修改（禁用 wait skill） |
| `dimos/agents/system_prompt.py` | 修改（Open-Goal 搜索行为指导） |
| `dimos/robot/test_all_blueprints_generation.py` | 修改（路径排除规则） |
| `dimos/navigation/open_goal/tests/test_wildos_inference.py` | 新建（测试覆盖） |
