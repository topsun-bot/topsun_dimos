<!--
SPDX-License-Identifier: Apache-2.0
Adapted from anthropics/oncall-kit (Apache-2.0)
https://github.com/anthropics/oncall-kit
https://claude.com/blog/ai-ci-cd-on-call
-->

# CI on-call for topsun-bot

Grok Bot（CICD 工程师）协助的 **GitHub Actions** 首响手册。本目录是本地 playbook：政策、能力图、事故日志与分诊参考。

值班代理 **只读**：调查、分类、提案。人决定、合并、推 cache、部署。没有 Slack Claude Tag；红 CI 由人贴进 Issue/聊天，代理按本树工作。既有例程（main CI 失败监听、工作日晨报等）只做只读播报，不替人合码。

## Upstream

- Kit: [anthropics/oncall-kit](https://github.com/anthropics/oncall-kit) (Apache-2.0)
- Write-up: [Claude on call for CI/CD](https://claude.com/blog/ai-ci-cd-on-call)（流程参考；本仓绑定 Grok Bot + `gh`，不装 Claude Tag）

本目录纯文档，不改 `.github/workflows/*`。

## Files

| File | Role |
|---|---|
| [ONCALL.md](./ONCALL.md) | 站立政策：申报、page vs morning-log、路由 |
| [STACK.md](./STACK.md) | 能力图（code / logs / pager / deploys） |
| [TRIAGE.md](./TRIAGE.md) | 压缩分诊流程 |
| [lessons.md](./lessons.md) | 只追加事故日志 |
| [references/test-failures.md](./references/test-failures.md) | 产品 / 测试类 |
| [references/cache-publish.md](./references/cache-publish.md) | Cachix / GHCR 发布类 |
| [references/runner-infra.md](./references/runner-infra.md) | Runner / 容量类 |

## How an agent should use this on the next red CI

1. 从磁盘读 `ONCALL.md`、`STACK.md`、`lessons.md`（文件优先于记忆）。
2. 用 `TRIAGE.md` 分类并加载对应 `references/*.md`；无匹配则从时间线入手。
3. 只在 `lessons.md` 搜该类 `#tag`。
4. 对照 `STACK.md` 绑定查失败 run 日志；每条断言带链接。
5. 发诊断：现象 / 根因+信心 / 爆炸半径 / **拟议**修复 / 已排除；不执行。
6. 仅当 `ONCALL.md` 路由树点名 owner 时才点名。
7. 人落地修复后追加 `lessons.md`；手册薄则开文档 PR。**永不合并、永不推 Cachix/GHCR、永不部署。**
