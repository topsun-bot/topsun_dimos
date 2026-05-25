# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""End-to-end smoke test for navigation obstacle MVP with fake LiDAR (GL-free path)."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

import pytest

from dimos.experimental.navigation_obstacle_mvp.tests.sim_test_deps import require_lcm_multicast

pytest_plugins = ("dimos.e2e_tests.conftest",)

_DEMO_TIMEOUT_S = 180.0
_FAKE_LIDAR_MARKER = "FAKE OBSTACLE LIDAR enabled"
_FIRST_FRAME_MARKER = "Published first fake obstacle lidar frame"
_MVP_DECISION_MARKER = "Obstacle detected ahead, applying MVP decision"
_GOAL_EXPR = "PoseStamped(position=Vector3(-1.10, 1.55, 0.0))"


def _require_mujoco() -> None:
    try:
        import mujoco  # noqa: F401
    except ImportError:
        pytest.skip("MuJoCo not installed. Run: uv sync --extra sim")


def _dimos_cmd(*extra: str) -> list[str]:
    return [sys.executable, "-m", "dimos.robot.cli.dimos", *extra]


def _dimos_demo_cmd(*run_args: str) -> list[str]:
    return _dimos_cmd(
        "--simulation",
        "mujoco",
        "--no-mujoco-viewer",
        "--mujoco-offscreen-gl",
        "off",
        "--viewer",
        "none",
        "--obstacle-crossing-mvp",
        "--obstacle-crossing-mvp-height-cm",
        "8",
        "run",
        "unitree-go2",
        *run_args,
    )


def _wait_for_dimos_running(timeout_s: float) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        result = subprocess.run(
            _dimos_cmd("status"),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and "Run ID:" in result.stdout:
            return True
        time.sleep(2.0)
    return False


def _read_dimos_log(n: int = 400) -> str:
    result = subprocess.run(
        _dimos_cmd("log", "-n", str(n)),
        capture_output=True,
        text=True,
        check=False,
    )
    text = result.stdout + result.stderr
    if "No log files found" in text:
        return ""
    return text


def _wait_for_log_substrings(substrings: tuple[str, ...], timeout_s: float) -> tuple[bool, str]:
    deadline = time.time() + timeout_s
    last_log = ""
    while time.time() < deadline:
        last_log = _read_dimos_log()
        if last_log and all(marker in last_log for marker in substrings):
            return True, last_log
        time.sleep(2.0)
    return False, last_log or _read_dimos_log()


def _wait_for_global_costmap(timeout_s: float) -> bool:
    import threading

    from dimos.core.transport import LCMTransport
    from dimos.msgs.nav_msgs.OccupancyGrid import OccupancyGrid

    transport = LCMTransport("/global_costmap", OccupancyGrid)
    transport.start()
    seen = threading.Event()

    def _on_msg(_msg: OccupancyGrid) -> None:
        seen.set()

    transport.subscribe(_on_msg)
    try:
        return seen.wait(timeout=timeout_s)
    finally:
        transport.stop()


def _send_goal_via_cli() -> None:
    subprocess.run(
        _dimos_cmd("topic", "send", "/goal_request", _GOAL_EXPR),
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.mark.skipif_in_ci
@pytest.mark.mujoco
def test_demo_smoke_fake_lidar_headless_subprocess() -> None:
    """Headless dimos with offscreen GL off publishes fake lidar (grep subprocess output)."""
    _require_mujoco()
    require_lcm_multicast()

    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = ""

    proc = subprocess.Popen(
        _dimos_demo_cmd(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
        start_new_session=True,
    )

    output_lines: list[str] = []
    deadline = time.time() + _DEMO_TIMEOUT_S
    fake_seen = False
    frame_seen = False

    try:
        assert proc.stdout is not None
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            line = proc.stdout.readline()
            if not line:
                time.sleep(0.05)
                continue
            output_lines.append(line.rstrip())
            if _FAKE_LIDAR_MARKER in line:
                fake_seen = True
            if _FIRST_FRAME_MARKER in line:
                frame_seen = True
            if fake_seen and frame_seen:
                break
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)

    tail = "\n".join(output_lines[-40:])
    assert proc.returncode in (None, 0, -signal.SIGTERM, -15), (
        f"dimos exited early ({proc.returncode}). Output tail:\n{tail}"
    )
    assert fake_seen, f"Expected fake lidar log. Output tail:\n{tail}"
    assert frame_seen, f"Expected first fake lidar frame log. Output tail:\n{tail}"


def run_demo_mvp_smoke(timeout_s: float = _DEMO_TIMEOUT_S) -> int:
    """CLI entry for ``scripts/demo-mvp.sh`` — returns process exit code."""
    _require_mujoco()
    require_lcm_multicast()

    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = ""
    checks: list[tuple[str, bool, str]] = []

    print(f"Running navigation MVP end-to-end demo ({timeout_s:.0f}s timeout)...")
    print("Command:", " ".join(_dimos_demo_cmd("--daemon")))

    subprocess.run(
        _dimos_cmd("stop", "--force"), env=env, check=False, capture_output=True, timeout=30
    )

    start = subprocess.run(
        _dimos_demo_cmd("--daemon"),
        env=env,
        check=False,
        timeout=300,
    )

    if start.returncode != 0:
        print("[demo-mvp] FAILED: could not start dimos daemon", file=sys.stderr)
        return 1

    if not _wait_for_dimos_running(timeout_s=180.0):
        print("[demo-mvp] FAILED: dimos did not reach running state within 180s", file=sys.stderr)
        return 1

    try:
        fake_ok, log_text = _wait_for_log_substrings(
            (_FAKE_LIDAR_MARKER, _FIRST_FRAME_MARKER),
            timeout_s=min(180.0, timeout_s * 0.6),
        )
        checks.append(("fake_lidar_enabled", fake_ok, _FAKE_LIDAR_MARKER))
        if not fake_ok:
            print("[demo-mvp] log tail:\n", log_text[-2000:], file=sys.stderr)
            return 1

        costmap_ok = _wait_for_global_costmap(timeout_s=60.0)
        checks.append(("global_costmap", costmap_ok, "/global_costmap"))
        if not costmap_ok:
            print("[demo-mvp] FAILED: no /global_costmap on LCM within 60s", file=sys.stderr)
            return 1

        print(f"[demo-mvp] Sending goal via dimos topic send: {_GOAL_EXPR}")
        _send_goal_via_cli()

        mvp_ok, log_text = _wait_for_log_substrings((_MVP_DECISION_MARKER,), timeout_s=90.0)
        checks.append(("obstacle_mvp_decision", mvp_ok, _MVP_DECISION_MARKER))

        print("\n=== demo-mvp summary ===")
        for name, ok, marker in checks:
            status = "PASS" if ok else "FAIL"
            print(f"  [{status}] {name}: {marker}")

        matching = [line for line in log_text.splitlines() if _MVP_DECISION_MARKER in line]
        if matching:
            print("\nKey log hits:")
            for line in matching[-3:]:
                print(f"  {line.strip()}")

        if mvp_ok:
            print("\n[demo-mvp] PASS: end-to-end obstacle MVP decision triggered")
            return 0

        print("\n[demo-mvp] log tail:\n", log_text[-3000:], file=sys.stderr)
        print("[demo-mvp] FAIL: obstacle MVP decision not seen in dimos log", file=sys.stderr)
        return 1
    finally:
        subprocess.run(
            _dimos_cmd("stop", "--force"), env=env, check=False, capture_output=True, timeout=30
        )


if __name__ == "__main__":
    raise SystemExit(run_demo_mvp_smoke())
