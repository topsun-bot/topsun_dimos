# Gemini ER 分层系统 / G1 单策略蒸馏评分卡

版本：v1.0  
日期：2026-07-15  
配套方案：[Gemini Robotics-ER 1.6 分层多 Teacher 蒸馏与 G1 单策略方案](gemini_er_multiteacher_distillation_plan.md)

## 1. 用法

这份评分卡是阶段门，不是汇报美化表。规则：

- **任何安全硬门失败，综合分再高也不能进入下一阶段**；
- 所有指标按 `quasi_static / low_dynamic / high_dynamic / recovery` 分组，并同时报告最坏组；
- 阈值在复现 TWIST 基线后冻结。下面的相对阈值是 v0 工程门，不是论文结论；
- `待验证` 不得填成 0 或猜测值；证据路径、配置、随机种子必须可追溯。

## 2. 当前状态快照

| 项目 | 状态 | 证据/说明 |
|---|---|---|
| ER 1.6 型号与接口 | 绿 | 官方模型卡/API 文档确认；文本输出的具身推理 VLM |
| ER 输出用于 Student 训练 | 红 | 现行 Gemini API 条款禁止开发竞争模型；未见书面授权 |
| Gemini key | 黄 | 当前进程和常见项目配置未发现可用赋值；但 key 不解除合规红灯 |
| Go2 视觉帧 | 绿 | `data/.lfs/distill/frames_v0/` 有 1432 张图 + `index.jsonl` |
| G1 动作训练数据账本 | 红 | 尚未建立本项目 manifest、物理过滤和固定切分 |
| TWIST 基线复现 | 红 | 上游代码/检查点存在，本仓库尚未复现 |
| 多 Teacher | 红 | 尚未训练 |
| 单 Student | 红 | 尚未训练 |
| `4090 48G GPU 服务器` readiness | 待验证 | 本轮未检查 SSH/CUDA/Isaac Gym/磁盘 |

## 3. 阶段门

| Gate | 目标 | 必须证据 | 通过条件 | 当前 |
|---|---|---|---|---|
| G0 合规 | 明确哪些输出能训练 | 条款快照、数据许可证、公司审批 | ER 训练输出禁用；开放/自有数据清单可审计 | 部分通过 |
| G1 基线 | 复现 TWIST Student | commit、环境锁、启动日志、sim 视频 | Isaac Gym 与 MuJoCo 均可重复运行 | 未开始 |
| G2 数据 | 建立 G1 motion manifest | clip 哈希、来源、许可证、retarget 结果、split | 无泄漏；四组均有 train/held-out/red-team | 未开始 |
| G3 Teacher | T0–T3 各自可靠 | 每组 3+ seeds、失败清单、hard queue | 指标平台期；无明显数据 bug 冒充难例 | 未开始 |
| G4 Student | 合并为单策略 | 同口径 teacher/student rollout | 最坏组、稳定性、约束均过线 | 未开始 |
| G5 sim2sim | 跨物理引擎稳定 | Isaac Gym 与 MuJoCo 对照 | 成功率退化 ≤ 10 个百分点；无硬越界 | 未开始 |
| G6 低风险实机 | 站立/慢走/转向 | 急停、吊架/保护、日志、视频 | 分级试验全部通过；无安全事件 | 未开始 |
| G7 高动态实机 | 跑跳踢拳逐项放行 | 单动作风险评审与测试报告 | 每个动作单独批准，不能用总分代替 | 未开始 |
| G8 高层闭环 | ER/开放 VLM 调用 skills | 任务日志、控制日志、成功判定 | 高层失败可降级；低层不依赖云端维持安全 | 未开始 |

## 4. 指标定义

### 4.1 任务与跟踪

| 指标 | 定义 | v0 通过线 |
|---|---|---|
| `Succ_k` | 组 k 中完整完成且未触发早停的 clip 比例 | Student ≥ 对应 Teacher - 5pp（准静态/低动态）；≥ Teacher - 8pp（高动态/恢复） |
| `WorstGroupSucc` | 四组 `Succ_k` 的最小值 | 不低于各组已冻结门槛 |
| `MPJPE` | root-relative 关节位置误差 | Student ≤ 1.15 × Teacher |
| `RootError` | 根位置/姿态误差 | Student ≤ 1.15 × Teacher |
| `Recovery@5s/10s` | 失稳后在 5/10 秒内恢复并重新跟踪 | Student ≥ Teacher - 8pp |

`pp` 表示百分点。相对线在 G1 完成后冻结；若 Teacher 本身不可靠，不能靠放宽 Student 门槛过关。

### 4.2 物理与安全硬门

以下任一项失败即红灯：

- 关节位置/速度/力矩超过机器人或仿真配置的硬限制；
- 自碰撞、非预期地面穿透、不可解释的外力或“上帝力”；
- 控制超时/网络断开后不能进入安全站立、阻尼或停机；
- 高动态实机测试缺少急停、安全员和物理保护；
- 真实试验出现人员伤害、财产损坏或未受控跌倒趋势。

软指标：

| 指标 | v0 通过线 |
|---|---|
| 足滑距离/时间 | Student ≤ 1.10 × Teacher |
| Action rate / jerk | Student ≤ 1.15 × Teacher |
| 单位任务能耗 | Student ≤ 1.20 × Teacher |
| 额外接触冲量 | 不高于冻结的安全上限；上限需基于硬件/仿真标定 |

### 4.3 蒸馏过程指标

| 指标 | 作用 | 不能代表什么 |
|---|---|---|
| Action MSE | 看均值动作是否接近 | 不能证明闭环稳定 |
| KL(Student‖Teacher) | 看动作分布是否接近 | 不能证明真实任务成功 |
| Teacher disagreement | 发现边界动作和冲突 | 分歧高不一定是谁错，需物理验证 |
| hard-queue hit rate | 看训练是否覆盖失败模式 | 不能把坏 retarget 当难例 |

### 4.4 鲁棒性矩阵

每个动作组至少覆盖：

| 变量 | v0 测试方式 | 记录 |
|---|---|---|
| 摩擦 | 在冻结范围内分层采样 | 成功率曲线，不只报平均 |
| 质量/质心 | 基座与末端质量扰动 | 最坏组退化 |
| 电机强度 | 缩放执行器能力 | 安全限值与恢复能力 |
| 外力 | 基座/末端随机推扰 | Recovery@5s/10s |
| 观测噪声 | IMU/编码器噪声、丢帧 | 稳定性与退化斜率 |
| 控制延迟 | 固定延迟 + jitter | 超时降级是否生效 |
| 地形 | 平地、低台阶、轻微坡度 | 每类独立通过 |

## 5. 综合评分

仅在安全硬门全部通过后计算：

\[
Score=0.30S_{task}+0.25S_{safety}+0.20S_{fidelity}
+0.15S_{robust}+0.10S_{latency}
\]

- `≥ 85`：绿灯，可申请进入下一阶段；
- `70–84`：黄灯，只能继续仿真/修复；
- `< 70`：红灯，回到数据、Teacher 或 Student 训练；
- 任一 worst-group 未过线：最高只能判黄灯；
- 任一安全硬门失败：直接红灯，不计算总分。

## 6. 环境配额记录

每轮训练记录：

| round | group | valid frames `H_k` | failure EMA `e_k` | `p_k` | env `E_k` | 主要失败 |
|---|---|---:|---:|---:|---:|---|
| R0 | quasi_static | 待验证 | 待验证 | 待验证 | 待验证 | 待验证 |
| R0 | low_dynamic | 待验证 | 待验证 | 待验证 | 待验证 | 待验证 |
| R0 | high_dynamic | 待验证 | 待验证 | 待验证 | 待验证 | 待验证 |
| R0 | recovery | 待验证 | 待验证 | 待验证 | 待验证 | 待验证 |

默认 `α=0.5, β=1.0, p_min=0.10, p_max=0.50`。修改参数必须写原因，不能因某组分数难看而临时减少其评测或采样。

## 7. 实验记录模板

```yaml
experiment_id:
date:
git_commit:
config_hash:
dataset_manifest_hash:
teacher_ids: []
student_id:
simulator:
seeds: []
group_metrics:
  quasi_static: {succ: null, mpjpe: null, foot_slip: null}
  low_dynamic: {succ: null, mpjpe: null, foot_slip: null}
  high_dynamic: {succ: null, mpjpe: null, foot_slip: null}
  recovery: {succ: null, recovery_5s: null, recovery_10s: null}
worst_group:
safety_hard_gates: []
hard_queue_summary:
decision: red|yellow|green
evidence_paths: []
open_questions: []
```

## 8. 停止与回退规则

- 连续 3 个评估周期 worst-group 无改善：停止加算力，先查数据、观测定义和 Teacher 冲突；
- hard queue 中同一来源占比超过 40%：暂停训练，做 retarget/数据质量审计；
- Action/KL 继续下降但任务成功率下降：降低模仿权重，增加 Student rollout PPO；
- sim2sim 退化超过 10pp：禁止上机，补系统辨识和 domain randomization；
- 任何安全硬门失败：回退到上一已通过检查点；
- 无 ER 训练授权：保持 `gen_dataset.py` 暂停，不把“已有 key”当作授权。

## 9. 最终交付清单

- [ ] 数据 manifest、许可证、哈希和固定 split
- [ ] T0–T3 Teacher 配置、权重、held-out 报告
- [ ] 单 Student 权重与部署格式
- [ ] Isaac Gym / MuJoCo sim2sim 对照报告
- [ ] worst-group、hard queue、随机种子和置信区间
- [ ] 低风险实机分级测试报告
- [ ] 高动态动作逐项安全审批
- [ ] ER/开放 VLM 高层 skill 调用接口与降级策略
- [ ] 条款/授权记录与 key 管理说明
