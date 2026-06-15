# 使用 GitHub Copilot 评测 PR 测试覆盖率

本文说明如何启用 **Copilot code review**，让其在每个合入 `main` 的 PR 上对照 PR 描述评测测试覆盖率，并将结果写入 PR 评论。

**特点：仅评论、不驳回。** Copilot 不会 Request changes，也不会作为 required check 阻止合并。

## 前置条件

- 组织或仓库已订阅 **GitHub Copilot**（Business / Enterprise 等，以贵司合同为准）。
- 审查者对该仓库有 Copilot 权限。

## 一次性仓库配置

### 1. 启用自定义审查指令

1. 打开仓库 **Settings** → **Code & automation** → **Copilot** → **Code review**。
2. 打开 **Use custom instructions when reviewing pull requests**。

本仓库已提供：

- [`.github/copilot-instructions.md`](/.github/copilot-instructions.md) — 全仓库审查要点与评论模板
- [`.github/instructions/test-coverage-review.instructions.md`](/.github/instructions/test-coverage-review.instructions.md) — `test/**`
- [`.github/instructions/dimos-tests-dir-coverage-review.instructions.md`](/.github/instructions/dimos-tests-dir-coverage-review.instructions.md) — `dimos/**/tests/**`（如 `navigation/nav_stack/tests/`）
- [`.github/instructions/dimos-tests-coverage-review.instructions.md`](/.github/instructions/dimos-tests-coverage-review.instructions.md) — `dimos/**/test_*.py`

> Copilot 使用 **PR 的 base 分支（通常是 `main`）** 上的指令文件。请先合入本说明与指令文件，再对功能 PR 启用自动审查。

### 2. 对 `main` 的 PR 自动请求 Copilot 审查（推荐）

1. **Settings** → **Rules** → **Rulesets** → **New branch ruleset**。
2. **Target branches**：包含 `main`（或 default branch）。
3. **Branch rules**：勾选 **Automatically request Copilot code review**。
4. 可选：
   - **Review new pushes** — 每次 push 重新审查
   - **Review draft pull requests** — 草稿阶段也出结果

参考：[Configuring automatic code review by GitHub Copilot](https://docs.github.com/en/copilot/using-github-copilot/code-review/configuring-automatic-code-review-by-copilot)

### 3. 不要做的配置

- **不要**将 Copilot review 设为 branch protection 的 required approval（Copilot 本身只做 Comment，通常也不应挡合并）。
- **不要**为覆盖率评测单独配置 `OPENAI_API_KEY` workflow（已改用 Copilot 原生审查）。

## 同事开 PR 时

1. PR 描述写清 **验收标准 / Test plan**（与 [PR 模板](/.github/pull_request_template.md) 一致）。
2. 在功能变更之外，补充或修改对应测试。本仓库常见位置（Copilot 会扫描 PR diff 中**全部**匹配项）：
   - `dimos/<module>/tests/` — 例：`dimos/navigation/nav_stack/tests/`
   - `dimos/<module>/test_*.py` — 例：`dimos/perception/test_spatial_memory.py`
   - `test/` — Quality Gate 专用（若团队使用）
3. PR 描述中的 **Test plan** 请列出新增/修改的测试文件路径，便于 Copilot 对照。
4. 等待 Copilot 在 PR 中发表评论（通常数十秒内）；评论中应包含「本 PR 涉及的测试文件」列表；每次 push 若开启 **Review new pushes** 会更新。
5. 手动触发（可选）：

   ```bash
   gh pr edit <PR号> --add-reviewer @copilot
   ```

## 与 `test/cuenca` 分支上 Quality Gate 的关系

| 内容 | 分支 `test/cuenca` | 本方案（Copilot） |
|------|-------------------|-------------------|
| `test/` Quality Gate 框架 | 有（领导暂不合入） | 无 |
| blocking workflow / `origin_test Pass` | 有 | **不需要** |
| 覆盖率评测 | 曾规划 OpenAI/Codex workflow | **Copilot PR 评论** |
| 是否阻塞合并 | 可配置为阻塞 | **否** |

领导暂缓合入的 `test/cuenca` 工作请保持 **Draft PR 或关闭**，不要与本次 Copilot 配置 PR 混在一个 PR 里。

## 本次如何合入 `main`

请使用 **独立小 PR**，只包含 Copilot 相关文件（示例分支名 `chore/copilot-pr-test-coverage`）：

```bash
git fetch origin main
git checkout -b chore/copilot-pr-test-coverage origin/main

# 确认仅包含以下新增/修改（不要带上 test/cuenca 的 workflow 与 test/ 框架）
git status

git add .github/copilot-instructions.md \
        .github/instructions/*.instructions.md \
        docs/development/copilot-pr-test-coverage.md

git commit -m "docs: enable Copilot PR test coverage review comments"
git push -u origin chore/copilot-pr-test-coverage

gh pr create --base main --title "docs: Copilot PR 测试覆盖率评论" --body "..."
```

合并该 PR 后，再在仓库按上文开启 **自动 Copilot review**，后续功能 PR 即可收到覆盖率评论。
