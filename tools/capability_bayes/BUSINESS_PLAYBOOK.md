# 从 AI 能力到收入与权限

目标不是向公司证明“我学会了 AI”，而是证明：在明确约束下，使用 AI 后的交付改善了一个公司关心的指标，并且改善能够归因到你的工作。

## 价值公式

```text
可实现收益 = 指标改善量 × 单位价值 × 每期数量 × 观察期数
业务后验均值 = alpha / (alpha + beta)
期望价值     = 业务后验均值 × 可实现收益 - 实施成本 - (1-业务后验均值) × 失败损失
```

当命令提供 `--tracker` 时，成功概率强制使用账本中 `value` 阶段的 Beta 后验均值。JSON 中的 `success_probability` 只是在完全没有历史账本时使用的初始先验，不能覆盖已有证据。

只有以下条件同时成立才执行试点：

1. 期望价值大于 0。
2. 有明确的业务负责人或验收人。
3. 试点前固定基线、目标和验收规则。
4. 有办法把指标变化归因到本次工作，而不是市场、团队扩张或其他同时发生的变化。
5. 权限申请是可回滚、有限范围且与当前证据等级匹配的。

## 权限阶梯

| 等级 | 已有证据 | 下一次只申请什么 |
| --- | --- | --- |
| L0 | 尚无真实价值成功 | 两周沙箱、只读数据、验收人时间 |
| L1 | 至少一次真实验收成功 | 有限真实数据、受控用户、生产试点 |
| L2 | 至少三次真实验收成功 | 有边界的生产所有权和小额预算 |
| L3 | 业务价值阶段后验置信度超过 0.9 | 正式职责、预算、职级或薪酬调整 |
| L4 | 收入/权限阶段也毕业 | 维持授权，并定期重新校准概率 |

不要从 L0 直接要求 L3。组织授予权限，本质上是在承担风险；最有效的路径是用一系列可回滚的小实验降低它对你的风险判断。

## 第一个公司实验

优先选择同时满足以下条件的问题：

- 每周发生，解决后可以反复产生价值。
- 当前有可测量的时间、成本、缺陷或收入基线。
- 两周内能形成结果。
- 失败不会影响生产安全或客户权益。
- 有一个具体负责人愿意验收。

不要选择“学习 AI”“研究新技术”作为业务指标。选择“故障定位时间从 120 分钟降至 30 分钟”“每周人工报告时间减少 6 小时”这类可验收指标。

## 使用工具

创建模板：

```bash
uv run python tools/capability_bayes/business_case.py template \
  business_case.json \
  --name "缩短机器人故障定位时间"
```

填入数据后，结合当前贝叶斯账本评估：

```bash
uv run python tools/capability_bayes/business_case.py evaluate \
  business_case.json \
  --tracker tools/capability_bayes/current_state.json
```

仓库中提供了一个可以直接运行的示例：

```bash
uv run python tools/capability_bayes/business_case.py evaluate \
  tools/capability_bayes/business_case.example.json \
  --tracker tools/capability_bayes/current_state.json
```

`RUN` 表示业务假设在当前输入下值得做受控试点，不表示最终一定成功。结果完成后，仍然需要把成功或失败写入 `value` 阶段。
