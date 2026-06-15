---
applyTo: "test/**"
---

# test/ 目录 — 覆盖率审查补充指令

仅针对仓库根下 `test/**`（Quality Gate 测试库）。**不要**把本规则当作全仓库唯一测试范围；`dimos/**/tests/**` 与 `dimos/**/test_*.py` 由其它 instructions 与 `copilot-instructions.md` 覆盖。

审查本 PR 中 `test/` 下变更的测试文件时，额外执行：

1. 将每个 `test_*` 函数与 PR 描述中的验收条目逐条映射；无法映射的验收项记入「覆盖缺口」。
2. 检查断言是否验证真实行为（避免恒真断言、空函数体、仅 `pass`）。
3. 若测试只 import 模块而未断言输出/状态/默认值，视为未覆盖。
4. 在 Copilot 总结评论中给出 0–100 语义覆盖率评分与补测建议（不阻塞合并）。
