# 历史 PRD：Gemini Robotics-ER 1.6 → Gemma 4 具身推理蒸馏（已暂停）

状态：**暂停直蒸馏**（2026-07-15 纠偏；M1 不得继续生成训练标签）
日期：2026-07-02
位置：工具链在 `tools/er_distill/`，数据在 `data/.lfs/distill/`

> 2026-07-15 状态纠偏：现行 [Gemini API Additional Terms of Service](https://ai.google.dev/gemini-api/terms)
> 明确禁止使用服务开发与该服务竞争的模型。付费 API 的数据保护条款不解除这一限制。
> 因此，本文件以下内容仅保留为历史技术预研记录；未经 Google 书面授权和公司法务确认，
> 不得运行 `gen_dataset.py` 将 ER 输出用于学生模型训练、筛选或偏好优化。
>
> 当前推荐路线见：
>
> - [Gemini ER 分层多 Teacher 蒸馏与 G1 单策略方案](gemini_er_multiteacher_distillation_plan.md)
> - [蒸馏阶段门、指标与评分卡](gemini_er_distillation_scorecard.md)

## 1. 目标

把 Google 闭源机器人模型 **Gemini Robotics-ER 1.6** 的具身推理能力（指点、检测框、
轨迹规划、空间问答），通过黑盒蒸馏迁移到开源多模态模型 **Gemma 4** 上，得到一个
可在本地（M4 Max，128GB）跑起来、部署在局域网上、给 Unitree Go2 实时提供视觉推理
的学生模型，并量化对比学生 vs 教师在真实机器人相机画面上的效果差距。

## 2. 事实核查（决定技术路线的关键结论）

| 问题 | 结论 | 依据 |
|------|------|------|
| ER 1.6 多大？ | **参数量不公开**。基于 Gemini 3.0 Flash，闭源，仅 API 访问（`gemini-robotics-er-1.6-preview`，2026-04-14 发布） | DeepMind 模型卡 |
| "蒸馏成 72B（或原模型一半）"可行吗？ | 前提不成立：原模型大小未知，且拿不到权重，**"一半"无从算起**。学生模型大小应由部署硬件和开源可得性决定 | — |
| 用户指定的 "gemma4-27b" 存在吗？ | 不存在。Gemma 4 全家：E2B / E4B / 12B / **26B-A4B**（MoE，总参 25.2B、激活 3.8B）/ 31B（稠密）。最接近的是 26B-A4B | HF / Google 模型卡 |
| 学生选型 | **gemma-4-26B-A4B-it**：多模态（图像输入）、Apache 2.0、激活仅 3.8B → 推理速度接近 4B 模型，4bit 量化 ~15GB，本机可跑可部署 | HF 模型卡 |
| 白盒蒸馏（logits/特征对齐）可行吗？ | **不可行**。教师闭源，API 不暴露 logits/中间层 | — |
| 无数据蒸馏（data-free KD）可行吗？ | **不可行**。data-free KD 需要教师白盒（用梯度/BN 统计反向合成输入）。API 教师唯一可行路线是黑盒数据蒸馏 | KD 综述（陶大程 2024 等） |
| 需要采数据吗？ | **需要图像，不需要人工标注**。图像已有（46GB Go2 真值库），标签由教师 API 生成 | — |

⚠️ 合规提示：Gemini API 服务条款含"不得用输出开发与其竞争的模型"类条款。本项目
作为内部技术预研使用；若涉及对外发布/商用，需先做合规评估。

## 3. 蒸馏方式：黑盒序列级蒸馏（SFT on teacher outputs）

对闭源 API 教师，唯一可行且是业界标准的做法：

```
真实图像（Go2 相机帧 + 开源数据集）
        │
        ▼
教师 API 打标（points / boxes / traj / spatial QA，JSON，0-1000 归一化坐标）
        │
        ▼
蒸馏语料 teacher_raw.jsonl → SFT 格式（同一 prompt，教师输出当 ground truth）
        │
        ▼
LoRA/QLoRA 微调 Gemma 4 学生模型（序列级 KD：交叉熵拟合教师输出）
        │
        ▼
一致性评测（Chamfer / IoU / json_valid）→ 不达标的任务追加数据迭代
        │
        ▼
局域网部署（M4 Max 推理服务器）→ Go2 实机相机流实时对比
```

可选增强（第二轮迭代再上）：拒绝采样（学生多次采样，教师/规则挑最优做偏好对）、
对困难帧加密采样、加入开源具身数据集（Open X-Embodiment、DROID、AgiBot World）
的帧扩充场景多样性。

## 4. 数据方案

- **图像来源 v0（已完成）**：从本地 6 个 Go2 真值库（46GB）按 1 秒间隔抽帧，
  得 **1432 帧**（1280×720，办公室/走廊场景）→ `data/.lfs/distill/frames_v0/`。
- **标签来源**：教师 API 对每帧跑 4 类任务（points/boxes/traj/spatial），
  v0 语料约 1432×4 ≈ **5700 条**。preview 模型限额低，按 0.5 QPS 跑约 3-4 小时。
- **v1 扩充（效果不够时）**：继续遥控采集新场景（现有 truth_capture 工作流）+
  开源数据集抽帧，目标 5 万-20 万条。
- **划分**：按场景切 train/held-out（如 hongkong_office 整场景留作测试），避免相邻帧泄漏。

## 5. 训练方案（两级）

| 级别 | 模型 | 硬件 | 方法 | 用途 |
|------|------|------|------|------|
| 试点 | gemma-4-12B-it（稠密，工具链支持最稳） | 本机 M4 Max（MLX LoRA） | mlx-vlm/mlx-lm LoRA，视觉塔冻结 | 一周内验证蒸馏收益曲线 |
| 正式 | gemma-4-26B-A4B-it | 云端 1×H100（或本机 MLX，若 MoE 微调支持到位） | QLoRA，transformers+peft | 最终交付模型 |

产出物：LoRA 权重 + 合并后的 4bit MLX 量化模型（部署用）。

## 6. 评测

1. **结构服从度**：json_valid 比例（学生能否稳定输出合法坐标 JSON）。
2. **与教师一致性**（held-out 场景）：points/traj 用对称 Chamfer 距离（0-1000 空间），
   boxes 用贪心匹配 mean IoU + recall@0.5。蒸馏前先跑零样本基线，蒸馏后同口径对比。
3. **实机对比**：Go2 现场相机流，学生 vs 教师同帧出结果、叠加可视化，人工过目 + 上述指标。
4. 验收线（v0）：json_valid ≥ 95%，points Chamfer 较零样本基线下降 ≥ 50%，
   boxes recall@0.5 ≥ 60%。

## 7. 部署

- 推理服务器：M4 Max 上 `mlx_vlm.server`（OpenAI 兼容接口），局域网监听。
  26B-A4B 激活 3.8B，4bit 在 M 系列上预计 40+ tok/s，单帧标注 < 3 秒。
- 机器人侧：复用 dimos 现有 WebRTC 管线（`UnitreeWebRTCConnection`，默认 IP
  192.168.123.161）取相机帧 → 调局域网学生服务 → 坐标结果叠加显示。
  Go2 本体不跑模型（Jetson 带不动 26B），模型跑在 Mac 上、机器人只出图像流。

## 8. 里程碑

- [x] **M0 立项+基建**（本次会话完成）：事实核查、工具链 4 个脚本、1432 帧图像池、
  学生模型环境（mlx-vlm）就绪
- [ ] **M1 教师语料 v0**：拿到 GEMINI_API_KEY 后跑 `gen_dataset.py`，~5700 条（约半天）
- [ ] **M2 零样本基线**：`run_student.py` 跑未微调 Gemma 4，`eval_agreement.py` 出基线
- [ ] **M3 试点蒸馏**：12B LoRA 本机训练，同口径评测，验证收益
- [ ] **M4 正式蒸馏**：26B-A4B QLoRA（云端或本机），达验收线
- [ ] **M5 局域网部署**：mlx server + Go2 实时对比脚本
- [ ] **M6 实机验证**：机器人开机、Mac 接入 192.168.123.x 网段，现场跑对比、出报告

## 9. 阻塞项（需要用户提供）

1. **GEMINI_API_KEY**（https://aistudio.google.com/apikey，免费档即可起步）→ 解锁 M1。
2. **机器人开机 + Mac 接入机器人网段**（当前 Mac 在 10.10.x，ping 不通 192.168.123.161）→ M6 需要。
3. **云 GPU 预算决定**（仅当本机 MoE 微调走不通时需要，约 1×H100 数小时）→ M4 可能需要。

## 10. 风险

- ER 1.6 是 preview：限额低、可能改版/下线 → 语料落盘保存，教师输出即资产。
- MLX 对 Gemma 4 MoE 微调支持不确定 → 试点用 12B 稠密版兜底，正式版可走云端。
- 教师条款合规（见 §2 提示）。
- 蒸馏只学"输出行为"，学不到教师内部推理 → 靠数据规模和任务覆盖弥补，预期达到
  教师 70-85% 的一致性，不是 100%。
