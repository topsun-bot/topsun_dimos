---
applyTo: "dimos/**/test_*.py"
---

# dimos 内测试文件 — 覆盖率审查补充

审查 `dimos/**/test_*.py` 时，将其与 PR 描述中的验收标准逐条对照；无法对应的条目写入 Copilot 总结评论的「覆盖缺口」。避免仅 import 不断言；标记恒真断言与空测试体。
