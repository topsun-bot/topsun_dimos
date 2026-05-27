# test/ AGENTS.md — 本地测试用例生成规范

> **面向对象**：本仓库开发同事 + 本地 Cursor / Copilot 等 AI 助手。  
> **模式**：**每日基于 `main` 扩充测试库**；同事提 PR 时 CI 会跑回归测试（`quality-gate-regression`，不阻塞）+ 描述到测试一致性门禁（`quality-gate-pr-blocking`，是否阻塞取决于 branch protection 配置）。  
> **框架说明**：见 [`README.md`](README.md)。

---

## 工作模式总览

| 时机 | 谁做 | 做什么 |
|------|------|--------|
| **每天（或定期）** | 指定同事 / 轮值 | `git pull origin main` → 按本文件用 AI **扩充 `test/`** → 合入 `main` |
| **同事提 PR** | CI 自动 | 跑 `quality-gate-regression`（不阻塞）+ `quality-gate-pr-blocking`（对 PR 描述与测试一致性做 required check：通过评论 `origin_test Pass`，失败评论驳回理由并让 check 失败） |
| **PR 合并** | — | 回归层失败默认仅提醒；是否阻止合并以 `quality-gate-pr-blocking` 在 branch protection 中是否设为 required check 为准 |

测试用例库在 `main` 上持续积累；PR 只验证「是否破坏已有回归行为」，不在 PR 上自动生成新用例。

---

## 一、硬规则

| 规则 | 说明 |
|------|------|
| **只改 `test/`** | 禁止为通过测试而修改 `dimos/`、`pyproject.toml`、`.github/` |
| **每日合入前本地跑通** | 至少 `test/bin/run_regression.sh`；扩充 unit/integration 时跑全量 `test/` |
| **必须打 marker** | `@pytest.mark.unit` / `integration` / `regression` |
| **禁止低价值测试** | 禁止 `assert True`、`pass`、空测试体 |
| **版权头 + `-> None`** | 与仓库其它文件一致 |

---

## 二、每日流程（在 `main` 上扩充测试）

### 步骤 1 — 同步 main

```bash
git checkout main
git pull origin main
```

### 步骤 2 — 确定今日要补的范围

任选一种（可组合）：

1. **看近期 main 变更**：`git log --oneline -20`、`git diff HEAD~7..HEAD --name-only -- dimos/`
2. **按模块轮询**：navigation、manipulation、`global_config`、blueprints、MCP 等（见第四节映射表）
3. **覆盖率缺口**：`test/regression/` 尚未覆盖的默认配置 / 公开 API

整理给 AI 的输入（不必写 PR）：

- 功能/模块摘要
- 要锁定的**历史行为**（默认值、返回值、蓝图名等）
- 相关 `dimos/` 文件路径

### 步骤 3 — 本地 AI 生成（Cursor）

对话中 `@test/AGENTS.md`，使用下方 **「每日扩充 Prompt」**。

### 步骤 4 — 本地验证

```bash
# 回归层（与 PR 上 CI 一致，必跑）
./test/bin/run_regression.sh -v

# 若今日新增了 unit/integration
PYTHONPATH=. python3 -m pytest test/ -c test/pytest.ini -v
```

### 步骤 5 — 提交到 main

```bash
git add test/
git commit -m "test: expand quality gate regression for <module>"
git push origin main
```

建议：**测试扩充单独 commit/PR 合入 main**，与功能 PR 分离，避免同事功能 PR 被回归失败挡住。

---

## 三、每日扩充 Prompt（复制给本地 AI）

```markdown
请严格遵循 `test/AGENTS.md`。今日任务：在 **main 分支当前代码** 上扩充测试库（非 PR  diff）。

## 范围
- 模块/路径：【例：dimos/core/global_config.py、dimos/navigation/...】
- 目标：【例：为 GlobalConfig 与导航默认参数补充 regression；必要时加 unit】
- 要锁定的历史行为：
  1. 【例：GlobalConfig().replay_db == "go2_short"】
  2. 【例：蓝图 unitree-go2-agentic 仍在 all_blueprints 中】

## 要求
1. 只修改 `test/`。
2. **优先** `test/regression/`（PR CI 只跑这层）。
3. regression 至少 1 个用例使用 `evidence` fixture。
4. 每个测试有正确 `@pytest.mark.*`。
5. 给出验证命令：`./test/bin/run_regression.sh -v`。
```

---

## 四、测试分层（每日扩充时怎么选）

| 目录 | 何时写 | PR CI 是否执行 |
|------|--------|----------------|
| `test/regression/` | **优先**：锁定 main 上默认行为、兼容、状态 | ✅ 是（唯一） |
| `test/unit/` | 纯逻辑、配置解析、单函数 | ❌ 否（仅本地/定期全量） |
| `test/integration/` | 蓝图注册、跨模块 import | ❌ 否 |

PR 上同事的功能变更：**不会**触发自动生成测试；若破坏回归，Checks 里 `quality-gate-regression` 会标黄/失败，**不阻塞** `ci` 合并。

### DimOS 映射（扩充 regression 时参考）

| 模块 | regression 关注点 |
|------|-------------------|
| `global_config.py` | 字段默认值 |
| `all_blueprints` | 关键蓝图名仍存在 |
| `navigation` / `manipulation` | 算法默认参数、公开 API 返回值 |
| `agents/mcp` | 工具暴露、schema 结构 |
| `@skill` | 返回类型与文档契约（与 `dimos/` 内测互补） |

---

## 五、代码模板

### 版权头（新文件必填）

与仓库其它 `.py` 相同（Apache 2.0，Dimensional Inc.）。

### 回归测试（PR CI 会跑）

```python
"""回归：<模块> 在 main 上的历史行为。"""

import pytest

from dimos.<package>.<module> import <Target>


@pytest.mark.regression
def test_<字段>_default_unchanged(evidence) -> None:
    obj = <Target>()
    assert obj.<field> == <expected_on_main>
    evidence.add(
        file_path="dimos/<path>/<file>.py",
        location="<symbol>",
        trigger="<how invoked>",
        risk_reason="<why locked>",
        test_basis="test/regression/test_<file>.py",
        verification=f"<field>={obj.<field>}",
    )
```

单元 / 集成模板见以往章节；每日任务 **以 regression 为主**。

---

## 六、同事提 PR 时会发生什么

1. 照常开 PR、跑现有 **`ci`**（`dimos/` pytest、lint 等）→ **决定能否合并**。
2. 额外跑 **`quality-gate-regression`** → 仅 `test/regression/`，`continue-on-error: true`。
3. 结果会 **自动写入 PR 评论**（Conversation 里一条固定评论，每次 push 会更新）：
   - 标题：`Quality Gate 回归测试（不阻塞合并）`
   - 内容：通过/失败数量、失败用例列表、pytest 日志折叠块、Actions 链接
4. 回归失败：**不影响** PR 合并（请勿把该 check 设为 branch required）。
5. **Fork 来的 PR** 可能无法发评论（权限限制），请改看 Checks / Actions 日志。

本地模拟 PR 回归：

```bash
git fetch origin pull/<N>/head:pr-<N>   # 或 checkout 同事分支
git checkout pr-<N>
./test/bin/run_regression.sh -v
```

---

## 七、自检清单（合入 main 前）

- [ ] `./test/bin/run_regression.sh` 通过
- [ ] 新 regression 对 main 上真实默认值/行为（非臆造）
- [ ] 至少 1 个 `evidence`（回归层）
- [ ] 无 `assert True` / 空测试
- [ ] 未改 `dimos/`

---

## 八、禁止模式

```python
# ❌ 浅断言 / 空测试 / 无 marker / 改 dimos/ 凑通过
```

---

## 九、与 `dimos/` 内测试的分工

| 位置 | 维护 | PR 阻塞？ |
|------|------|-----------|
| `dimos/**/test_*.py` | 模块作者 | 是（`ci` / tests job） |
| `test/regression/` | 每日轮值 + 本地 AI | **否**（仅提醒） |
| `test/unit/`、`test/integration/` | 每日轮值 + 本地 AI | 否（PR 不跑） |

---

## 十、参考命令

```bash
# 与 PR CI 一致
./test/bin/run_regression.sh -v

# 全量 test/（每日扩充后可选）
PYTHONPATH=. python3 -m pytest test/ -c test/pytest.ini -v

# 可选：本地 Quality Gate 报告
PYTHONPATH=. python3 test/bin/run_quality_gate.py --skip-tests
```

---

## 十一、一句话总结

**每天 pull `main` → 按本文件扩充 `test/`（尤其 regression）→ 合入 `main`；同事 PR 只跑回归、不挡合并。**
