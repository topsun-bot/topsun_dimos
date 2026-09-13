# Gemini Robotics-ER 1.6 分层多 Teacher 蒸馏与 G1 单策略方案

版本：v1.0  
日期：2026-07-15  
状态：方案完成；ER 直蒸馏为红灯，TWIST/PHC 合规路线可进入复现门  
默认机器人：Unitree G1（若改为 Go2，需要重新定义动作空间、数据与验收线）

## 0. 一页结论

最终判断不是“把 Gemini Robotics-ER 1.6 直接蒸馏成一个关节控制策略”，而是把系统分成两层：

1. **高层具身推理层**：ER 1.6 只做在线场景理解、对象/空间定位、任务分解、成功判定，输出结构化子目标或调用已有 robot skills。它不进入学生训练集。
2. **低层全身控制层**：用可审计、可复现的开放代码与自有数据训练多个特权 Teacher，再把它们合并到一个可部署 Student。推荐以 TWIST 的 `RL + BC/KL` 为主干，以 PHC 的 hard-negative mining 和渐进式能力扩展为补充。

这样保留了 ER 的高层价值，又避免三个根本错误：

- ER 官方接口是多模态输入、**文本输出**，不是 50 Hz 关节动作 Teacher；
- API 不提供权重、隐藏层或 logits，无法做白盒特征蒸馏；
- 现行 Gemini API 条款禁止用服务开发与其竞争的模型，不能把 ER 输出批量落盘训练 Student。

## 1. 事实、推断、建议

### 1.1 已核实事实

- Google 将 Gemini Robotics-ER 1.6 定义为基于 Gemini 3.0 Flash 的具身推理 VLM，专长是视觉/空间理解、任务规划和成功检测；官方 API 输入为文本、图像、视频、音频，输出为文本。
- 官方说明把 ER 的任务编排描述为“把长任务拆成子任务，并调用已有控制器或行为”，这支持“ER 在上、控制器在下”的分层架构。
- TWIST 的 Teacher 用未来 2 秒参考运动等特权信息通过 PPO 训练；Student 只看本体状态和当前参考帧，目标函数是：

  \[
  \mathcal{L}(\pi_S)=\mathcal{L}_{PPO}(\pi_S)+\lambda D_{KL}(\pi_S\Vert\pi_T)
  \]

  其中 \(\lambda\) 随训练逐渐减小。TWIST 明确报告 `RL+BC` 优于纯 BC 和纯 RL，所以“Student 一定是纯 Action loss”不是该论文的结论。
- PHC/PMCP 先评估全数据集，找出当前策略无法模仿的 hard sequences，再增加新 primitive 学习更难动作；最终由 composer 动态组合 primitives。它是“渐进 primitive + hard mining”，不能简化成互不相关的几个网络最后硬拼。
- TWIST 已公开 Teacher/Student 训练代码、数据、检查点、sim2sim 与 sim2real 脚本，仓库标注 MIT License；上游称单张 RTX 4090 24GB 可在约 1–2 天完成一次训练，实际时间仍取决于数据量和配置。

### 1.2 工程推断

- ER 的二维点、框、文本轨迹不能直接监督 G1 的关节目标，因为两者动作空间、控制频率和物理约束都不同。
- “高动态/低动态/准静态”可以作为第一版语义标签，但不能只靠人工动作名称分类；同一个“踢腿”可能既有慢速展示版，也有高冲击版，应再用动力学特征划分。
- 多 Teacher 的核心价值不是消灭所有梯度冲突，而是先让每个 Teacher 在较窄的物理分布上形成高质量动作，再通过 Student 的共享表示、均衡采样和闭环强化学习解决策略合并后的冲突。

### 1.3 推荐决策

- **推荐路线 A（执行）**：ER 作为在线高层推理器；低层复现 TWIST 单 Teacher 基线，再升级为 4 Teacher + 单 Student。
- **备选路线 B（完全离线）**：高层也改用开放权重 VLM，只用自有/开放标注训练；ER 仅作为独立产品对照，不把输出用于模型开发。
- **否决路线 C**：调用 `gemini-robotics-er-1.6-preview` 批量生成标签，再 SFT/QLoRA 一个相似具身 VLM。当前条款下是合规红灯。

## 2. 起点、终点、最终目的

### 2.1 当前起点

| 资产/条件 | 当前事实 | 对本方案的意义 |
|---|---|---|
| `tools/er_distill/` | 已有 ER 黑盒标注脚手架 | 保留为历史实验；训练标签生成暂停 |
| `data/.lfs/distill/frames_v0/` | 1432 张 Go2 办公室/走廊帧 + 索引 | 可做高层视觉评测；**不是** G1 动作蒸馏数据 |
| Gemini key | 当前 shell 中 `GEMINI_API_KEY`、`GOOGLE_API_KEY` 均未注入，相关项目 `.env`/shell 配置未发现赋值 | 即使重新提供，也不能越过合规硬门 |
| DimOS | 已有 G1 MuJoCo/agentic sim 入口 | 可作为 Student 集成与技能调用层 |
| 训练基线 | 本仓库尚未落地 TWIST/PHC G1 训练线 | 第一里程碑应是复现，不是直接发起大规模多 Teacher 训练 |
| GPU | `4090 48G GPU 服务器` 是既有候选机器引用 | 本轮未验证 SSH、CUDA、Isaac Gym 和磁盘 readiness，不能把它写成已就绪 |

### 2.2 项目终点

交付一个 G1 单策略 Student `π_S`，满足：

- 一个网络覆盖准静态、低动态、高动态和失稳恢复四个 motion groups；
- 部署只需要机器人本体状态、历史窗口和当前参考目标，不需要未来动作真值；
- 在 Isaac Gym 训练、MuJoCo sim2sim 验证后，按低风险到高风险逐级上机；
- 导出 JIT/ONNX 或项目实际支持的部署格式，由高层 motion server/DimOS skill 调用；
- 每个动作组都有独立成功率、最坏组性能、安全指标和回归样例，不能只报全局均值。

### 2.3 最终目的

最终目的不是“得到一个更小的模型”本身，而是形成以下闭环：

```text
自然语言/视频/任务
        │
        ▼
ER 1.6 在线高层推理（或开放 VLM）
对象/空间/子任务/成功条件
        │  结构化 skill call / motion goal
        ▼
统一 G1 Student（50 Hz 设计目标）
        │  关节位置目标
        ▼
PD/安全控制器（TWIST 实例为 1000 Hz）
        │
        ▼
真实机器人反馈 → 成功判定 / 降级 / 停机
```

ER 是否在线、延迟多少不应影响低层稳定性；网络断开时低层必须能安全站立、降级或停止。

## 3. 数据处理与动作分类

### 3.1 先做可行性过滤，再做聚类

每条人类运动先 retarget 到 G1，再经过物理过滤：

- 关节位置、速度、加速度是否超过机器人限制；
- 足底接触与地面高度是否一致；
- 是否存在自碰撞、穿模、瞬时根位移；
- 质心投影、支撑多边形和接触切换是否合理；
- 涉及物体的动作是否有对应物体几何、质量和接触模型。

未通过过滤的动作不能因为“看起来像人”就进入 Teacher 训练。

### 3.2 双标签体系

第一层是人工语义标签，便于产品和测试理解：

- `quasi_static`：站立、单腿平衡、慢速伸手、姿态展示；
- `low_dynamic`：行走、转向、下蹲、缓慢全身操作；
- `high_dynamic`：奔跑、跳跃、踢腿、拳击、快速转身；
- `recovery`：被推、跌倒、偏离参考、恢复站立并重新跟踪。

第二层是动力学特征，避免动作名称掩盖真实难度：

\[
x_i=[v_{root},\omega_{root},a_{root},\max|\dot q|,\max|\ddot q|,
N_{contact},J_{contact},m_{support},h_{root},ROM]
\]

对特征标准化后可用 K-means 做初始分桶；若离群动作多，优先 HDBSCAN。不要直接对原始关节序列做 K-means，因为时长、相位和坐标系会主导距离。

### 3.3 数据切分

- 以 motion clip / subject / source session 为单位切分，禁止把同一动作的相邻帧拆进 train 与 test；
- 固定 `train / validation / held-out / red-team` 四套；
- `held-out` 包含每个动作组未见过的动作与速度；
- `red-team` 包含低摩擦、质量偏差、延迟、观测噪声、外力冲击和恢复场景。

## 4. 多 Teacher 设计

推荐不是 3 个，而是 4 个 Teacher：

| Teacher | 数据域 | 特权输入 | 主要优化目标 | 失败风险 |
|---|---|---|---|---|
| T0 准静态 | 平衡、姿态、慢速 reach | 未来参考帧、完整仿真状态、接触真值 | 姿态精度、支撑裕度、低抖动 | 过度保守 |
| T1 低动态 | 走、转、蹲、慢操作 | 同上 | 速度跟踪、足底不滑、能耗 | 泛化到快速动作差 |
| T2 高动态 | 跑、跳、踢、拳、快速转向 | 同上 | 冲量、腾空/落地、全身协调 | 冲击、跌倒、关节超限 |
| T3 恢复 | 推扰、跌倒、偏离参考 | 完整状态、失败类型、目标姿态 | 5/10 秒恢复成功率、重回跟踪 | 与正常动作相互干扰 |

Teacher 训练采用 PPO + 模仿奖励。每个 Teacher 训练到平台期后，在自己的 held-out 集上跑全量评估；失败动作进入 hard queue。若同一动作连续多次失败且数据/retarget 无误，才扩展网络容量或新增 primitive，避免把坏数据误判为“难动作”。

两种合并方式：

1. **先 composer、后 Student**：按 PHC 思路冻结已学 primitives，训练 composer 形成复合 Teacher，再蒸馏一次。优点是 Student 只面对一个 Teacher 接口；缺点是 composer 的错误会被继承。
2. **直接多 Teacher 蒸馏**：每个环境携带 `teacher_id`，Student 接收统一观测，监督目标来自对应 Teacher。优点是误差来源清晰；缺点是边界动作的 Teacher 冲突更明显。

第一版推荐方式 2，评测清楚后再决定是否加 composer。

## 5. 三阶段训练方法

### 阶段 I：训练多个特权 Teacher

Teacher \(k\) 的策略为：

\[
\pi_{T_k}(a_t\mid o_t^{priv}, r_{t:t+H},k)
\]

奖励至少包含姿态、关节、根速度、关键点、接触、足滑、动作变化率、能耗和越界惩罚。不同 Teacher 可以改变权重，但指标定义必须一致，避免合并时“成功”的含义不同。

退出条件：每个 Teacher 在 held-out 集达到自己的成功门槛，且最坏 10% clip 不再因明显 retarget/仿真错误失败。

### 阶段 II：蒸馏到一个可部署 Student

Student 只看可部署观测：

\[
\pi_S(a_t\mid o_t^{deploy},h_{t-L:t},r_t)
\]

推荐目标函数：

\[
\mathcal{L}=
\mathcal{L}_{PPO}
+\lambda_{KL}\sum_k p_kD_{KL}(\pi_S\Vert\pi_{T_k})
+\lambda_A\mathbb{E}\|\mu_S-\mu_{T_k}\|_2^2
+\lambda_{safe}\mathcal{L}_{constraint}
\]

- Teacher/Student 都输出高斯动作分布时，以 KL 为主；
- 若部署策略是确定性动作，可保留 Action MSE；
- `λ_KL`/`λ_A` 前高后低，让 Student 先学 Teacher，再用 RL 修正自己的状态分布；
- 不能只离线回放 Teacher 状态。Student 必须在仿真中自己 rollout，否则分布偏移会在上机时放大。

### 阶段 III：闭环微调与 hard mining

每个评估周期执行：

1. 在固定 held-out + 随机 domain randomization 中 rollout Student；
2. 记录跌倒、足滑、超限、跟踪发散、Teacher 分歧和最坏组性能；
3. 先排除坏数据/仿真 bug，再把有效失败放入 hard queue；
4. 对 hard queue 提高采样率，并用对应 Teacher + PPO 继续训练；
5. 只有所有安全硬门通过，才进入下一风险级别的 sim2real。

## 6. 环境数量与采样数学

“按动作条数比例分环境”只在两个前提同时成立时正确：目标部署分布与数据条数一致，且每条动作时长/难度相近。更稳妥的基础量是有效控制帧数：

\[
H_k=\sum_{i\in k} duration_i\times f_{control}
\]

推荐动态采样：

\[
p_k=\frac{H_k^{\alpha}(\epsilon+e_k)^{\beta}}
{\sum_jH_j^{\alpha}(\epsilon+e_j)^{\beta}},\quad
E_k=round(E\cdot p_k)
\]

- `e_k`：该组 Student 失败率的指数滑动平均；
- `α=0.5`：对数据量开平方，防止大类淹没小类；
- `β=1.0`：失败越多，采样越多；
- 每组设置最小 10% 和最大 50% 配额，恢复组不得被采样器饿死。

实现时先算未截断的 `p_k`，再施加最小/最大配额并重新归一化，保证所有环境数之和仍为 `E`。

你的示例中，动作条数为 10/20/30、总环境 6000：

- 先假设每条动作等时且三组失败率相等，此时有效控制帧比例可近似为动作条数比例；
- 纯比例分配：`1000 / 2000 / 3000`，优化的是按 clip 计的 micro-average；
- 推荐初始开方分配：约 `1447 / 2046 / 2507`，更接近每类均衡；
- 训练稳定后再让 `e_k` 动态修正，不应永远固定比例。

## 7. 五类验证

### 7.1 物理学验证

- 关节位置/速度/力矩不越硬件限值；
- 接触冲量、足滑、腾空与落地顺序合理；
- 根姿态、质心和支撑裕度不会因为追动作而失稳；
- 控制链断网、超时、观测异常时进入安全站立/阻尼/停机；
- 高动态动作在没有吊架、急停和安全员前只允许仿真。

### 7.2 数学验证

- 同时报 `macro-average`、按部署频率加权的 `micro-average`、最坏动作组和最坏 10% clips；
- Student 对 Teacher 的 Action/KL 相似度只是过程指标，不能替代任务成功率；
- 所有结论至少报告随机种子、均值和置信区间，不用单次最好结果做结论。

### 7.3 生物学/形态验证

- 人体 MoCap 的关节范围、脚底结构、质量分布与 G1 不同，必须 retarget 后再判定可行；
- 人类“自然”不等于机器人“安全/节能”；
- 拳击、踢腿等动作必须区分表演式空挥与接触式动作，后者需要物体接触模型和力限制。

### 7.4 逻辑学验证

- “ER 会规划轨迹”不能推出“ER 会输出可执行关节控制”；
- “Student 像 Teacher”不能推出“Student 在真实世界成功”；
- “整体平均上升”不能推出“所有动作都变好”；
- “更多 Teacher”不能推出“冲突自动消失”，仍需观测统一、采样平衡和闭环训练。

### 7.5 心理与评测偏差验证

- 评审员盲测视频，隐藏模型名称和训练轮次，降低期待偏差；
- 预先登记通过线，避免看完结果后改指标；
- 对 ER 或 Teacher 的输出保持自动化怀疑：可视化好看不等于物理正确；
- 失败视频必须保留，禁止只展示成功 demo。

## 8. 阶段门与验收迹象

完整指标见 [蒸馏阶段门、指标与评分卡](gemini_er_distillation_scorecard.md)。核心“绿灯迹象”是：

- 复现官方 TWIST Student 检查点，在 Isaac Gym 与 MuJoCo 都能稳定跑；
- 多 Teacher 各自 held-out 性能稳定，hard queue 中坏数据占比已清理；
- 统一 Student 的每组成功率接近对应 Teacher，最坏组没有被整体平均掩盖；
- sim2sim 性能下降可控，延迟抖动和观测噪声测试通过；
- 实机从站立/慢走开始逐级放行，零安全越界，随时可急停和回退。

“红灯迹象”包括：仅 Action loss 很低但 rollout 频繁跌倒、某一组环境占比过高、验证集相邻帧泄漏、MuJoCo 明显退化、网络断开后策略继续输出危险动作、用 ER 输出训练却没有书面授权。

## 9. 里程碑

| 里程碑 | 工作 | 退出证据 |
|---|---|---|
| M0 合规与基线 | 冻结 ER 直蒸馏；复现 TWIST 官方 Student | sim 与检查点可重复启动；版本/许可证记录完整 |
| M1 数据账本 | G1 retarget、物理过滤、四组标签、固定切分 | `manifest` 可追溯；无 train/test 泄漏 |
| M2 多 Teacher | T0–T3 分别训练，周期性 hard mining | 每组 held-out 指标与失败清单 |
| M3 单 Student | 多 Teacher RL+BC/KL；动态环境配额 | macro、worst-group、稳定性通过 |
| M4 sim2sim | Isaac Gym → MuJoCo；扰动/延迟/噪声 | 退化不超过评分卡门槛 |
| M5 低风险实机 | 站立、平衡、慢走、转向 | 分级试验记录；无安全硬门失败 |
| M6 高层集成 | ER/开放 VLM → skill/motion goal → Student | 任务成功与控制成功分开记分 |
| M7 高动态放行 | 跑、跳、踢、拳逐项审批 | 吊架、急停、安全员、边界测试全部具备 |

不在本轮承诺固定天数。上游 TWIST 给出的单卡训练时间是参考，不是对本项目数据、驱动和服务器环境的实测。

## 10. key 与合规处理

- 本轮只检查“是否存在”，没有读取或回显任何 key；当前进程和常见相关配置中未发现 `GEMINI_API_KEY` / `GOOGLE_API_KEY` 可用赋值。
- 即使用户曾在旧对话提供，密钥也不应作为长期记忆保存；需要时应通过项目 secret manager 或当前 shell 临时注入。
- 在取得 Google 书面授权和公司法务批准前，不运行 `tools/er_distill/gen_dataset.py` 生成训练语料。
- 付费服务解决的是 Google 是否用你的输入/输出训练其产品，不代表你获得用输出开发竞争模型的权利。
- 合规的 ER 用法是面向当前机器人应用做在线推理、任务编排和成功判定；低层 Student 从 TWIST/PHC、自有仿真真值和开放数据学习。

## 11. 主要风险与 Plan B

| 风险 | 触发迹象 | Plan B |
|---|---|---|
| ER 条款阻断 | 无书面授权 | 保持在线 API 层；训练数据全部换开放/自有来源 |
| 单 Student 容量不足 | worst-group 长期落后 Teacher | 增大网络、加入 skill embedding，或保留 composer + primitives |
| 高动态拖累低动态 | 低动态回归、足滑上升 | 回放低动态安全缓冲区；限制动态采样上限；分阶段解冻 |
| sim2real 失效 | MuJoCo/实机明显退化 | 加强 domain randomization、系统辨识、延迟/噪声建模 |
| 数据伪难样本 | hard queue 高度集中于同一 retarget 错误 | 数据审计优先于扩容网络 |
| 实机安全不足 | 无吊架/急停/安全员 | 高动态保持 sim-only，不以 demo 压过安全门 |

## 12. 主要来源

- [Gemini Robotics-ER 1.6 Model Card](https://deepmind.google/models/model-cards/gemini-robotics-er-1-6/)
- [Gemini Robotics-ER 1.6 API / Robotics overview](https://ai.google.dev/gemini-api/docs/robotics-overview)
- [Gemini API Additional Terms of Service，2026-03-23 生效](https://ai.google.dev/gemini-api/terms)
- [TWIST: Teleoperated Whole-Body Imitation System](https://arxiv.org/abs/2505.02833)
- [TWIST 官方代码、数据与训练说明](https://github.com/YanjieZe/TWIST)
- [PHC: Perpetual Humanoid Control for Real-time Simulated Avatars](https://openaccess.thecvf.com/content/ICCV2023/papers/Luo_Perpetual_Humanoid_Control_for_Real-time_Simulated_Avatars_ICCV_2023_paper.pdf)
