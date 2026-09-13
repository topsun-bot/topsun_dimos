from pathlib import Path

from tools.skill_lint.lint_skills import lint_paths, lint_source


def codes(source: str) -> list[str]:
    return [finding.code for finding in lint_source(source)]


def test_valid_skill_has_no_findings() -> None:
    source = '''
class Skills:
    @skill
    def move(self, distance: float) -> str:
        """Move by a distance."""
        return str(distance)
'''
    assert lint_source(source) == []


def test_skillresult_return_type_is_allowed() -> None:
    source = '''
class Skills:
    @skill
    def pick(self, name: str) -> SkillResult[PickError]:
        """Pick an object."""
        return SkillResult.ok(name)
'''
    assert lint_source(source) == []


def test_missing_docstring_is_warning() -> None:
    findings = lint_source("@skill\ndef move(x: float) -> str:\n    return str(x)\n")
    assert [(finding.code, finding.severity) for finding in findings] == [("SK001", "warning")]


def test_missing_parameter_and_return_annotations_are_errors() -> None:
    source = '''
@skill
def move(x):
    """Move."""
    return x
'''
    assert codes(source) == ["SK002", "SK003"]


def test_rpc_and_skill_must_not_be_stacked() -> None:
    source = '''
@rpc
@skill
def reset() -> str:
    """Reset."""
    return "ok"
'''
    assert codes(source) == ["SK004"]


def test_non_skill_function_is_ignored() -> None:
    source = "def helper(x):\n    return x\n"
    assert lint_source(source) == []


def test_test_files_are_excluded_by_default(tmp_path: Path) -> None:
    (tmp_path / "test_example.py").write_text(
        "@skill\ndef bad(x):\n    return x\n", encoding="utf-8"
    )
    assert lint_paths([tmp_path]) == []
    assert len(lint_paths([tmp_path], include_tests=True)) == 3
