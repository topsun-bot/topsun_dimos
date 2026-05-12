# /ship — 一句话触发本地开发与提交流水线

> 用法：在 Cursor chat 里输入 `/ship <一句话需求>`，例如：  
> `/ship 修正 scripts/verify.sh 里 pytest 参数说明`

把下面步骤当作**固定剧本**执行。**任一步失败先停下汇报**，不要为走完流程而造假通过。

---

## 步骤（严格按序）

### 1. 准备

- 确认当前目录为**本仓库根目录**（含 `pyproject.toml`、`dimos/`、`scripts/verify.sh`）。
- 读 `AGENTS.md` 与 `.cursor/rules/00-workflow.mdc`。
- `git status`：
  - 在集成分支上且工作区干净 → 可进入步骤 2。
  - 有无关未提交改动 → **不要** `git add -A`；只处理本次任务文件，或先请用户 stash / 处理并行工作（见 `AGENTS.md` 坑 #12）。

### 2. 更新基线并拉功能分支

本 fork 约定集成分支为 **`feat/dingyi`**（若团队改为以 `dev` 为准，以 `AGENTS.md` / 用户说明为准）。

```bash
git fetch origin
git checkout feat/dingyi
git pull --ff-only origin feat/dingyi
git checkout -b <type>/<topic>
```

命名示例：`feat/verify-doc-tweak`、`fix/mypy-regression`、`chore/ci-cache`。

### 3. 最小变更计划

- 用 todo 拆成 3～5 步；只动**必要文件**。
- 若影响面过大（跨多子系统、5+ 文件且无用户授权），**停下问是否拆 PR**。

### 4. 实现

- 按 todo 逐项完成并标记进度。
- 改 Python：保持类型与现有风格；勿引入未使用的依赖。
- 中间可跑针对性 `uv run pytest <path>`，**不能**跳过最终 `verify.sh`（除非用户明确只要子集并承担风险）。

### 5. 本地验证（强制）

```bash
bash scripts/verify.sh
```

- 通过 → 步骤 6。
- 失败 → 读报错 → 最小修复 → 重跑；**禁止**删测试/改 `verify.sh` 放水。
- 若失败明显为历史/环境问题且非本次引入 → **停下问用户**是否纳入本 PR。

### 6. 整理变更

- `git diff --stat` 汇总。
- `git add <仅本次相关文件>`（**禁止**不加分辨的 `git add -A`）。
- 准备 1～3 行 commit 说明（为什么改）。

### 7. Commit

格式：

```text
<type>: <一句话摘要>

<可选：1～3 行细节>
```

**禁止**对已 push 的 commit 滥用 `--amend`；**禁止** `--no-verify`（若以后加 git hooks）。

### 8. Push

```bash
git push -u origin <branch>
```

- 失败（权限/网络）→ 汇报，**禁止** force push 解决。

### 9. 创建 PR

- **`--base`**：默认 **`feat/dingyi`**；若团队要求进 `dev`，改为 `dev` 并遵守 `AGENTS.md` Git Workflow。
- 使用 `gh pr create`，title 与 body 符合模板。

PR body 须包含（与 `.github/pull_request_template.md` 一致）：

- **Summary**
- **Test plan**（含 `bash scripts/verify.sh` 及结果摘要）
- **Risk**
- **Related**（可写 N/A）

若 `gh pr create` 遇 GraphQL 延迟，可改用 `gh api repos/<owner>/<repo>/pulls`（REST）创建（见 onboarding 文档坑 #2）。

### 10. Auto-merge（若仓库已开启）

```bash
gh pr merge --auto --squash <pr-number-or-url>
```

若报错未开启 auto-merge → 在步骤 11 列出人工项。

### 11. 收尾汇报（固定格式）

```markdown
## 完成情况

### 改动
- <文件>: <一句话>

### 验证
- bash scripts/verify.sh: PASS / FAIL（关键一行输出）

### PR
- <URL>

### 还需要人类做
- [ ] GitHub：Allow auto-merge / branch protection / required checks（若未配）
- [ ] Codex：仓库审查设置（若启用）
- [ ] 硬件/仿真回归（若 PR 涉及运动与安全）
```

---

## 失败 / 中止

- 任一步失败：**先汇报状态**，勿静默重试或 force push。
- 用户中止意图：保留分支，由用户善后。
- **永远不要**：跳过 verify、删别人改动、对共享分支 force push、直接 commit 到 `feat/dingyi` / `main` / `dev`（除非策略明确允许）。
