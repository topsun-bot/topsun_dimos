---
applyTo: "dimos/**/tests/**"
---

# dimos 模块 tests/ 目录 — 覆盖率审查补充

适用于 `dimos/**/tests/**`（例如 `dimos/navigation/nav_stack/tests/`、`dimos/core/tests/`、`dimos/perception/**/tests/`）。

1. 将本 PR 在该目录下新增/修改的每个测试用例与 PR 描述中的验收条目逐条映射。
2. 检查 rosbag / 集成类测试是否仍断言可观测结果（不仅是「能跑完」）。
3. 无法映射的验收项写入 Copilot 总结评论的「覆盖缺口」，并注明建议文件名或目录。
4. 在总结评论的「本 PR 涉及的测试文件」中列出本目录下所有变更的测试路径。
