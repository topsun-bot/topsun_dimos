# Copilot PR 审查 — 测试覆盖率（不阻塞合并）

本仓库使用 **GitHub Copilot code review** 对照 PR 描述评估测试覆盖率。Copilot 仅发表评论，**不会** Request changes，也**不会**阻止 PR 合并。

## 审查任务

对每个 Pull Request，请完成以下评测并**在 PR 评论中输出结构化结果**（建议置顶一条总结评论）：

1. **阅读 PR 描述**：提取功能摘要、验收标准（Acceptance Criteria / Test plan / 测试计划）。
2. **收集本 PR 中所有与测试相关的变更文件**（以 PR diff 为准，不要只盯 `test/`）：
   - `test/**` — Quality Gate 测试库（若存在）
   - `dimos/**/tests/**` — 模块内测试目录（例：`dimos/navigation/nav_stack/tests/`、`dimos/core/tests/`）
   - `dimos/**/test_*.py` — 与模块同目录的 pytest 文件（例：`dimos/navigation/.../test_local_planner_rosbag.py`）
   - `dimos/**/*_test.py` — 以 `_test.py` 结尾的测试文件
   - 上述路径下的 `conftest.py`、`tests/conftest.py` 等测试支撑文件（若在本 PR 有改动）
3. **对照变更的功能代码**：`dimos/` 下非测试的生产代码（排除 `tests/`、`test_*.py`、`*_test.py`）。
4. **语义覆盖率评分**（0–100，非 pytest-cov 行覆盖率）：
   - 90–100：验收标准均有对应测试且断言具体
   - 70–89：主路径覆盖，少量边界缺失
   - 50–69：部分功能有测试，关键缺口明显
   - 0–49：测试与 PR 描述严重脱节或仅有占位测试

## 评论输出格式（请尽量遵循）

```markdown
### Copilot 测试覆盖率评测

**评分：** <0-100>/100

**摘要：** <一两句话>

**本 PR 涉及的测试文件：**
- `<path>` — <一句话说明测什么>
- ...

**功能点覆盖：**
- [✓/✗] <功能点 1> — <测试依据或缺口说明>
- [✓/✗] <功能点 2> — ...

**覆盖缺口：**
- ...

**补测建议：**
- <建议放在哪条路径，例如 dimos/navigation/nav_stack/tests/ 或 dimos/perception/.../test_*.py>

> 本评测仅供参考，不阻塞合并。合并仍以 `ci` 等 required checks 为准。
```

## 判定规则

- PR 仅改文档/配置、无 `dimos/` 功能变更：说明「无功能代码变更，跳过覆盖率评测」即可。
- 有 `dimos/` 功能变更但 PR diff 中**没有任何**测试相关文件（见上文路径规则）：在缺口中明确指出，并建议放在与模块邻近的 `tests/` 或 `test_*.py`（与团队现有模块一致，如 navigation / perception）。
- 发现 `assert True/False`、空测试体、仅 `pass`：在缺口中标注为低价值测试。
- 不要要求修改 `dimos/` 业务代码来「凑覆盖率」；只评价测试与描述是否匹配。
- 评测时以 **PR 全部测试相关 diff** 为范围；禁止仅审查 `test/` 而忽略 `dimos/**/tests/**` 等路径。

## 语言

- 团队以中文为主时，评论可用中文；保留上述标题结构便于扫读。
