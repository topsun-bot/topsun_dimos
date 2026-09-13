# DimOS Skill Lint

静态检查 `@skill` 声明，不导入机器人模块，也不会连接硬件。

检查项：

| 代码 | 级别 | 含义 |
| --- | --- | --- |
| `SK000` | error | Python 语法或文件编码错误 |
| `SK001` | warning | 缺少静态 docstring；可能存在动态赋值，需要人工确认 |
| `SK002` | error | 暴露给工具调用的参数缺少类型注解 |
| `SK003` | error | 缺少返回类型注解 |
| `SK004` | error | 同时叠加 `@rpc` 和 `@skill`；`@skill` 已经包含 `@rpc` |

当前 DimOS 支持 `str`、`SkillResult` 以及部分图像、布尔值和生成器返回类型，因此检查器不会使用过期的“只能返回 str”规则。

```bash
uv run python tools/skill_lint/lint_skills.py dimos
uv run python tools/skill_lint/lint_skills.py dimos --json
```

默认排除 `test_*.py` 和 `tests/`，避免测试中的最小示例干扰生产报告。存在 error 时退出码为 1；只有 warning 时退出码为 0。

