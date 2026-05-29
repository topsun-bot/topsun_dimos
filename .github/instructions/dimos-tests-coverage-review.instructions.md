---
applyTo: "dimos/**/test_*.py"
---

# dimos 内 test_*.py — 覆盖率审查补充

适用于与业务模块同目录的 `dimos/**/test_*.py`（navigation、perception、manipulation 等模块常见写法）。

审查时将其与 PR 描述中的验收标准逐条对照；无法对应的条目写入 Copilot 总结评论的「覆盖缺口」。避免仅 import 不断言；标记恒真断言与空测试体。在总结评论中列出本 PR 变更的所有此类文件路径。
