# AI 能力内化贝叶斯追踪器

这个工具不测量 IQ，也不把“看懂一次”当成永久能力。它持续回答一个更窄、可验证的问题：

> 面对未见过的同类任务，在延迟、无 AI 和压力条件下，真实成功率达到目标线的后验置信度是否超过 0.9？

## 数学模型

每个阶段独立维护一个 Beta-Binomial 模型。练习数据与认证数据严格分离：

```text
先验              theta ~ Beta(1, 1)
认证盲测 s 成功 f 失败 theta | D ~ Beta(1+s, 1+f)
毕业置信度         C = P(theta >= 目标成功率 | D)
阶段毕业           C > 0.9 且有效样本数达到下限
联合概率保守下界   L = max(0, 1 - sum(1-C_k))
总任务停止         所有阶段毕业，且 L > 0.9
```

`L` 使用并集界，不要求六个阶段相互独立。报告也会显示 `prod(C_k)` 作为“阶段独立”假设下的参考值，但它不能单独触发停止。六项分别刚超过 `0.9` 时，总任务不会提前停止。

阶段不能相互替代：

| 阶段 | 目标成功率 | 最少有效样本 | 有效证据要求 |
| --- | ---: | ---: | --- |
| AI 甄优 | 90% | 21 | 权威来源或事实基准 + 可执行测试或反例检查 |
| 延迟回忆 | 80% | 10 | AI 结果已核验、无辅助、延迟至少 24 小时 |
| 陌生迁移 | 80% | 10 | AI 结果已核验、无辅助、未见变式 |
| 压力提取 | 70% | 8 | 无辅助、未见变式、限时或模拟追问 |
| 业务价值 | 60% | 5 | 有前后指标，并被真实使用者验收 |
| 收入或权限 | 50% | 5 | 薪酬、资源或权限申请有可核实结果 |

AI 甄优首先估计：随机抽取一个经该流程筛选的 AI 结果，最终被事实基准和测试判定正确且适用的真实概率。AI 结果的“已核验”要求与权威来源或事实基准核对，并且至少经过可执行测试或反例检查。

人脑、业务价值和收入/权限认证必须在知道结果前预注册测试编号、到期时间、题目 SHA-256 和评分规则。没有预注册、提前作答、重复编号、普通练习和其他无效记录都会保留，但不会抬高概率。到期仍未提交的认证自动按一次失败进入后验，防止只报告成功结果；稍后提交后，自动失败会被真实结果替换，不会重复计算。AI 甄优是对已经生成的产物做事实和执行验证，不要求延迟预注册。

每条认证还必须指定 `evidence_group`。同一工具、同一知识点或同一业务实验的连续改版属于同一组，组内无论产生多少次成功都只算一个有效样本；只有跨知识点、跨任务或跨真实场景的证据才能增加独立样本数。

记录一条通过甄优的 AI 输出：

```bash
uv run python tools/capability_bayes/tracker.py add capability.json \
  --stage ai_quality \
  --result pass \
  --certification \
  --test-id ai-output-001 \
  --evidence-group bayes-model \
  --evidence "官方文档链接、测试日志或审查记录" \
  --source-checked \
  --executable-checked
```

## 开始使用

```bash
uv run python tools/capability_bayes/tracker.py init \
  capability.json \
  --capability "利用AI独立完成并交付高价值工程任务"
```

你目前的“看一次 AI 结果，有些内容能记住”可以如实记录为练习，但它不会被误算成认证证据：

```bash
uv run python tools/capability_bayes/tracker.py add capability.json \
  --stage recall \
  --result pass \
  --evidence "主观上能够复述部分AI答案" \
  --source-checked \
  --counterexample-checked
```

先把题目写入文件并计算 SHA-256；预注册只保存哈希，不需要提前公开题目内容：

```bash
uv run python tools/capability_bayes/tracker.py hash-prompt recall-001.txt

uv run python tools/capability_bayes/tracker.py prepare capability.json \
  --stage recall \
  --test-id recall-001 \
  --due-at "2026-07-13T19:50:00-07:00" \
  --prompt-hash "<上一步输出的SHA-256>" \
  --evidence-group bayes-model-recall \
  --rubric "定义、原理、反例、应用各1分，至少3分通过"
```

到期后进行闭卷测试，再登记结果：

```bash
uv run python tools/capability_bayes/tracker.py add capability.json \
  --stage recall \
  --result pass \
  --certification \
  --test-id recall-001 \
  --evidence-group bayes-model-recall \
  --prompt-file recall-001.txt \
  --evidence "录屏/2026-07-13-recall.mp4" \
  --source-checked \
  --executable-checked \
  --unassisted \
  --delay-hours 24
```

查看当前后验置信度和系统指定的下一步：

```bash
uv run python tools/capability_bayes/tracker.py report capability.json
```

本仓库已经按用户当前自述初始化了持续账本：

```bash
uv run python tools/capability_bayes/tracker.py report \
  tools/capability_bayes/current_state.json
```

## 每日闭环

1. 只选择一个与工作价值有关的能力单元。
2. 最多用三个可靠来源甄别 AI 结果，执行测试或寻找反例。
3. 关闭 AI，进行复述、重建或编码。
4. 在第 1、3、7、14 天练习；最终认证题必须提前预注册并使用唯一编号。
5. 正常状态稳定后，再加入限时和模拟追问。
6. 每周把能力用于一个有基线、有指标、有人验收的真实任务。
7. 用已验收结果申请下一档任务范围、资源、职位或薪酬。

追踪器输出 `CONTINUE` 时，不表示失败，只表示当前证据不足。重复练习不会被伪装成独立证据；只有六个阶段都满足认证门禁，而且联合概率保守下界超过 `0.9` 时才输出 `STOP`。
