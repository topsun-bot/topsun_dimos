#!/usr/bin/env python3
"""Statically check DimOS @skill declarations without importing robot modules."""

from __future__ import annotations

import argparse
import ast
from collections.abc import Iterable
from dataclasses import asdict, dataclass
import json
from pathlib import Path


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    function: str
    code: str
    severity: str
    message: str


def decorator_name(decorator: ast.expr) -> str | None:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def lint_source(source: str, path: str = "<memory>") -> list[Finding]:
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as error:
        return [
            Finding(
                path=path,
                line=error.lineno or 1,
                function="<module>",
                code="SK000",
                severity="error",
                message=f"Python语法错误: {error.msg}",
            )
        ]

    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decorators = {
            name for decorator in node.decorator_list if (name := decorator_name(decorator))
        }
        if "skill" not in decorators:
            continue

        if ast.get_docstring(node) is None:
            findings.append(
                Finding(
                    path,
                    node.lineno,
                    node.name,
                    "SK001",
                    "warning",
                    "缺少静态docstring; 动态赋值也应人工确认MCP描述。",
                )
            )

        parameters = [
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        ]
        for parameter in parameters:
            if parameter.arg not in {"self", "cls"} and parameter.annotation is None:
                findings.append(
                    Finding(
                        path,
                        parameter.lineno,
                        node.name,
                        "SK002",
                        "error",
                        f"参数{parameter.arg!r}缺少类型注解。",
                    )
                )
        for variadic_parameter, prefix in (
            (node.args.vararg, "*"),
            (node.args.kwarg, "**"),
        ):
            if variadic_parameter is not None and variadic_parameter.annotation is None:
                findings.append(
                    Finding(
                        path,
                        variadic_parameter.lineno,
                        node.name,
                        "SK002",
                        "error",
                        f"参数{prefix + variadic_parameter.arg!r}缺少类型注解。",
                    )
                )

        if node.returns is None:
            findings.append(
                Finding(
                    path,
                    node.lineno,
                    node.name,
                    "SK003",
                    "error",
                    "缺少返回类型注解。",
                )
            )

        if "rpc" in decorators:
            findings.append(
                Finding(
                    path,
                    node.lineno,
                    node.name,
                    "SK004",
                    "error",
                    "@skill已经包含@rpc, 不应重复叠加。",
                )
            )
    return findings


def python_files(paths: Iterable[Path], *, include_tests: bool) -> list[Path]:
    files: set[Path] = set()
    for path in paths:
        candidates = [path] if path.is_file() else path.rglob("*.py")
        for candidate in candidates:
            if candidate.suffix != ".py":
                continue
            if not include_tests and (
                candidate.name.startswith("test_") or "tests" in candidate.parts
            ):
                continue
            files.add(candidate)
    return sorted(files)


def lint_paths(paths: Iterable[Path], *, include_tests: bool = False) -> list[Finding]:
    findings: list[Finding] = []
    for path in python_files(paths, include_tests=include_tests):
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            findings.append(
                Finding(
                    str(path),
                    1,
                    "<module>",
                    "SK000",
                    "error",
                    "文件不是UTF-8编码。",
                )
            )
            continue
        findings.extend(lint_source(source, str(path)))
    return findings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path, default=[Path("dimos")])
    parser.add_argument("--include-tests", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    findings = lint_paths(args.paths, include_tests=args.include_tests)
    if args.json:
        print(json.dumps([asdict(finding) for finding in findings], ensure_ascii=False, indent=2))
    else:
        for finding in findings:
            print(
                f"{finding.path}:{finding.line}: "
                f"{finding.severity} {finding.code} "
                f"{finding.function}: {finding.message}"
            )
        errors = sum(finding.severity == "error" for finding in findings)
        warnings = sum(finding.severity == "warning" for finding in findings)
        print(f"skill-lint: {errors} error(s), {warnings} warning(s)")
    return 1 if any(finding.severity == "error" for finding in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
