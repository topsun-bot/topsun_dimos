from pathlib import Path

import pytest

from tools.interruption_checkpoint.checkpoint import (
    complete_task,
    load_checkpoint,
    resume_view,
    save_task_checkpoint,
)


def create_checkpoint(path: Path) -> dict:
    return save_task_checkpoint(
        path,
        task_id="robot-debug",
        goal="定位导航漂移原因",
        last_done="确认里程计时间戳连续",
        next_action="对比第一处轨迹发散前2秒的cmd_vel",
        blockers=["缺少一次真值回放"],
        evidence=["odom检查日志"],
    )


def test_checkpoint_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "task.json"
    created = create_checkpoint(path)

    loaded = load_checkpoint(path)

    assert loaded == created
    assert loaded["status"] == "active"
    assert loaded["next_action"].startswith("对比")


def test_update_preserves_previous_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "task.json"
    create_checkpoint(path)

    updated = save_task_checkpoint(
        path,
        task_id="robot-debug",
        goal="定位导航漂移原因",
        last_done="找到第一处发散时间",
        next_action="检查对应控制命令",
    )

    assert len(updated["history"]) == 1
    assert updated["history"][0]["last_done"] == "确认里程计时间戳连续"


def test_resume_view_contains_only_recovery_fields(tmp_path: Path) -> None:
    path = tmp_path / "task.json"
    checkpoint = create_checkpoint(path)

    view = resume_view(checkpoint)

    assert "目标: 定位导航漂移原因" in view
    assert "下一动作: 对比第一处轨迹发散前2秒的cmd_vel" in view
    assert "阻碍: 缺少一次真值回放" in view


def test_complete_requires_evidence_and_locks_checkpoint(tmp_path: Path) -> None:
    path = tmp_path / "task.json"
    create_checkpoint(path)

    completed = complete_task(path, "回放测试通过")

    assert completed["status"] == "complete"
    assert completed["next_action"] == ""
    with pytest.raises(ValueError, match="已经完成"):
        save_task_checkpoint(
            path,
            task_id="robot-debug",
            goal="定位导航漂移原因",
            last_done="继续修改",
            next_action="再次运行",
        )


def test_required_fields_cannot_be_blank(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="next_action不能为空"):
        save_task_checkpoint(
            tmp_path / "task.json",
            task_id="robot-debug",
            goal="定位导航漂移原因",
            last_done="完成检查",
            next_action="  ",
        )


def test_different_task_cannot_overwrite_checkpoint(tmp_path: Path) -> None:
    path = tmp_path / "task.json"
    create_checkpoint(path)

    with pytest.raises(ValueError, match="不同task_id"):
        save_task_checkpoint(
            path,
            task_id="other-task",
            goal="另一个目标",
            last_done="无",
            next_action="开始",
        )
