#!/usr/bin/env python3
"""Go2 4G Remote WebRTC 自研 ArUco 回充真机脚本 (不依赖 NX aruco_recharge 二进制).

启动 (需 .env 里 UNITREE_SERIAL 等):
  source .venv/bin/activate && set -a && source .env && set +a
  uv run python jiangtao/scripts/demo_go2_4g_aruco_recharge.py \\
    --execute --allow-liedown --until-charge \\
    --preflight-timeout-s 120 --max-attempts 5 --charge-watch-s 30

默认不加 --execute 只观测码位. --until-charge 会在 fail/丢码后 recover 并重试.

2026-08-05 成功样本 (yaw_sign=1.0, 日志 /tmp/go2_recharge_yaw_sign_fix.log):
  第 1 次 attempt: z 1.43→0.34 m, 趴下前 yaw≈9°, BMS -2172→-1048 mA, charged=true.
  曾用 yaw_sign=-1.0 时 align_yaw 14° 飙到 43° 后全失败 (pulse_yaw11.log).

运动路径: WIRELESS_CONTROLLER 摇杆 (velocity_api=False), 见 connection.move:
  ly=vx, lx=-vy, rx=-angular.z; |ly|≥0.10、|rx|≥0.20 才动 (jiangtao/run.md).
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
import math
import os
import threading
import time
from typing import Any

from dimos.core.global_config import global_config
from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.robot.unitree.connection import UnitreeWebRTCConnection
from dimos.robot.unitree.go2.recharge.charge_verify import (
    ChargeVerifier,
    calibrated_go2_4g_charge_rules,
    soc_rising_charge_hint,
)
from dimos.robot.unitree.go2.recharge.config import RechargeConfig
from dimos.robot.unitree.go2.recharge.controller import ArucoRechargeController
from dimos.robot.unitree.go2.recharge.types import MarkerObservation, RechargeState
from dimos.robot.unitree.go2.recharge.vision import ArucoRechargeVision

# 4G 摇杆硬下限 (2026-08-05 三狗 odom 标定, 见 jiangtao/run.md).
_MIN_FORWARD = 0.10  # |ly| 前进下限
_MIN_YAW = 0.20  # |rx| 转向下限; 持续 30 s 约转一圈 → ω≈0.21 rad/s
_YAW_RATE_RAD_S = 0.21  # 仅用于「偏差角 ↔ 反转时长」换算, 非精确角速度
_FULL_TURN_S = 30.0  # 与 _MIN_YAW 配套: 30 s @ rx=0.20 ≈ 360°
_BACKUP_1M_S = 7.0  # vx=-0.15 约 7 s, 实测约退 1 m


def _undo_duration_s(last_yaw_error_rad: float, yaw_turn_s: float) -> float:
    """按丢码前偏差角等量反转, 且不超过刚才实际转过的量."""
    from_angle = abs(last_yaw_error_rad) / _YAW_RATE_RAD_S
    undo = from_angle if from_angle >= 0.25 else yaw_turn_s
    if yaw_turn_s > 0.0:
        undo = min(undo, yaw_turn_s * 1.15)
    return max(0.3, min(undo, 3.1416 / _YAW_RATE_RAD_S))


def parse_args() -> argparse.Namespace:
    """Parse field-test flags; default is observation-only and no physical lie-down."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--allow-liedown", action="store_true")
    parser.add_argument(
        "--until-charge",
        action="store_true",
        help="Retry with recover-until-tag until liedown + BMS current observed.",
    )
    parser.add_argument("--preflight-timeout-s", type=float, default=90.0)
    parser.add_argument("--max-attempts", type=int, default=30)
    parser.add_argument("--charge-watch-s", type=float, default=20.0)
    return parser.parse_args()


def _event(name: str, **fields: object) -> None:
    """Emit one JSONL event so field logs can be grepped and replayed after a run."""
    print(json.dumps({"event": name, **fields}, ensure_ascii=False), flush=True)


def _age(now: float, received_at: float | None) -> float:
    """Return +inf for streams that have not produced data yet."""
    return float("inf") if received_at is None else max(0.0, now - received_at)


def _twist(vx: float = 0.0, vy: float = 0.0, yaw: float = 0.0) -> Twist:
    """Build a Twist whose fields are interpreted as 4G joystick ratios."""
    return Twist(linear=Vector3(vx, vy, 0.0), angular=Vector3(0.0, 0.0, yaw))


def _extract_bms_current(lowstate: Any) -> float | None:
    """Extract raw ``bms_state.current`` from the WebRTC lowstate dictionary."""
    try:
        data = lowstate.get("data") if isinstance(lowstate, dict) else None
        bms = data.get("bms_state") if isinstance(data, dict) else None
        value = bms.get("current") if isinstance(bms, dict) else None
        return float(value) if isinstance(value, int | float) else None
    except (TypeError, AttributeError, ValueError):
        return None


def _extract_power_v(lowstate: Any) -> float | None:
    """Extract ``power_v`` for diagnostics; charge success does not depend on it."""
    try:
        data = lowstate.get("data") if isinstance(lowstate, dict) else None
        value = data.get("power_v") if isinstance(data, dict) else None
        return float(value) if isinstance(value, int | float) else None
    except (TypeError, AttributeError, ValueError):
        return None


class LatestInputs:
    """Thread-safe latest camera pose, low-state and BMS current."""

    def __init__(self, vision: ArucoRechargeVision) -> None:
        self._vision = vision
        self._lock = threading.Lock()
        self._observation: MarkerObservation | None = None
        self._image_at: float | None = None
        self._lowstate_at: float | None = None
        self._bms_current: float | None = None
        self._power_v: float | None = None
        self._frame_n = 0

    def on_image(self, image: object) -> None:
        """Update latest marker pose from a WebRTC video frame."""
        now = time.monotonic()
        observation = self._vision.observe(image)  # type: ignore[arg-type]
        with self._lock:
            self._image_at = now
            self._frame_n += 1
            # 单帧漏检不清缓存: approach 阶段偶发漏检若清空 obs 会误发零速卡住.
            # 2026-08-05 现场曾因此 z 停在 ~0.8 m 无进展.
            if observation is not None:
                self._observation = replace(observation, observed_at=now)

    def on_lowstate(self, message: object) -> None:
        """Update latest lowstate freshness and scalar BMS fields."""
        with self._lock:
            self._lowstate_at = time.monotonic()
            current = _extract_bms_current(message)
            if current is not None:
                self._bms_current = current
            power = _extract_power_v(message)
            if power is not None:
                self._power_v = power

    @property
    def power_v(self) -> float | None:
        """Latest diagnostic pack voltage, if WebRTC lowstate has exposed it."""
        with self._lock:
            return self._power_v

    def snapshot(
        self, now: float
    ) -> tuple[MarkerObservation | None, float, float, float | None, int]:
        """Return a consistent latest-input snapshot for one controller tick."""
        with self._lock:
            return (
                self._observation,
                _age(now, self._image_at),
                _age(now, self._lowstate_at),
                self._bms_current,
                self._frame_n,
            )


def _stop(conn: UnitreeWebRTCConnection) -> None:
    """Send several zero commands because WirelessController can keep the last stick value."""
    for _ in range(5):
        conn.move(_twist())
        time.sleep(0.05)


def _enable_motion(conn: UnitreeWebRTCConnection) -> None:
    """Put Go2 into a motion-accepting state before sending joystick commands."""
    standup_ok = conn.standup()
    time.sleep(5.0)
    balance_ok = conn.balance_stand()
    time.sleep(2.0)
    joystick_ok = conn.switch_joystick(True)
    _stop(conn)
    _event(
        "recharge_motion_enabled",
        standup=standup_ok,
        balance=balance_ok,
        joystick=joystick_ok,
    )


def _wait_tag(
    inputs: LatestInputs,
    *,
    timeout_s: float,
    prefer_z_min: float | None = None,
    prefer_z_max: float | None = None,
) -> MarkerObservation | None:
    """Wait for a fresh tag pose and fresh lowstate before allowing motion."""
    deadline = time.monotonic() + timeout_s
    last_diag = 0.0
    while time.monotonic() < deadline:
        now = time.monotonic()
        obs, image_age, lowstate_age, _, frames = inputs.snapshot(now)
        if now - last_diag > 5.0:
            _event(
                "recharge_waiting_tag",
                frames=frames,
                has_tag=obs is not None,
                z_m=None if obs is None else round(obs.z_m, 4),
                yaw_rad=None if obs is None else round(obs.yaw_rad, 4),
                image_age_s=None if image_age == float("inf") else round(image_age, 2),
            )
            last_diag = now
        if (
            obs is not None
            and image_age <= 0.35
            and lowstate_age <= 0.5
            and (prefer_z_min is None or obs.z_m >= prefer_z_min)
            and (prefer_z_max is None or obs.z_m <= prefer_z_max)
        ):
            return obs
        time.sleep(0.05)
    return None


def _recover_tag(
    conn: UnitreeWebRTCConnection,
    inputs: LatestInputs,
    *,
    home_z_m: float,
    last_yaw_cmd: float = 0.0,
    last_yaw_error_rad: float = 0.0,
    yaw_turn_s: float = 0.0,
    skip_undo: bool = False,
    timeout_s: float = 90.0,
) -> MarkerObservation | None:
    """丢码恢复 (三层, 2026-08-05 定稿):

    1. undo: 按丢码前 bearing 角估算时长, 沿 last_yaw_cmd 反方向转; 见码即停.
       若控制器 _tick_marker_lost 已做过等量反转, skip_undo=True 跳过本层.
    2. full_360: 同方向 rx=±0.20 转满 30 s (≈一圈).
    3. backup_1m + post_backup_full_360: 退约 1 m 后再转一圈.

    禁止用 recover 开始前的缓存 obs (observed_at 必须 ≥ recover_started_at).
    不再用 home_z_m 一路倒车追码 (旧逻辑会把狗倒离桩).
    """
    del home_z_m  # 不再用作一路倒车目标
    del timeout_s
    _stop(conn)
    undo_sign = -1.0 if last_yaw_cmd >= 0.0 else 1.0
    undo_s = 0.0 if skip_undo else _undo_duration_s(last_yaw_error_rad, yaw_turn_s)
    recover_started_at = time.monotonic()
    _event(
        "recharge_recover_start",
        last_yaw_cmd=round(last_yaw_cmd, 3),
        last_yaw_error_rad=round(last_yaw_error_rad, 4),
        yaw_turn_s=round(yaw_turn_s, 3),
        undo_yaw_sign=undo_sign,
        undo_s=round(undo_s, 3),
        skip_undo=skip_undo,
        full_turn_s=_FULL_TURN_S,
        plan="undo_same_angle_then_full_360",
    )

    def _fresh_tag() -> MarkerObservation | None:
        """必须是恢复开始后的新检测, 不能用丢码前缓存."""
        now = time.monotonic()
        obs, image_age, _, _, _ = inputs.snapshot(now)
        if obs is None:
            return None
        if obs.observed_at < recover_started_at:
            return None
        if now - obs.observed_at > 0.40:
            return None
        if image_age > 0.50:
            return None
        return obs

    def _spin_until_tag(
        *, yaw_sign: float, duration_s: float, via: str
    ) -> MarkerObservation | None:
        deadline = time.monotonic() + duration_s
        while time.monotonic() < deadline:
            obs = _fresh_tag()
            if obs is not None:
                _stop(conn)
                _event(
                    "recharge_recover_ok",
                    via=via,
                    z_m=round(obs.z_m, 4),
                    yaw_rad=round(obs.yaw_rad, 4),
                    elapsed_s=round(time.monotonic() - recover_started_at, 2),
                )
                return obs
            conn.move(_twist(yaw=yaw_sign * _MIN_YAW))
            time.sleep(0.1)
        _stop(conn)
        return None

    # 第一层: 按刚才偏差角等量反转 (例如偏了 18° 就大约反转 18° 的时长)
    if undo_s > 0.0:
        _event("recharge_recover_phase", phase="undo_same_angle", duration_s=round(undo_s, 3))
        found = _spin_until_tag(yaw_sign=undo_sign, duration_s=undo_s, via="undo_same_angle")
        if found is not None:
            return found

    # 第二层: 原地转满一整圈
    _event("recharge_recover_phase", phase="full_360", duration_s=_FULL_TURN_S)
    found = _spin_until_tag(yaw_sign=undo_sign, duration_s=_FULL_TURN_S, via="full_360")
    if found is not None:
        return found

    # 第三层: 仍没有 → 退约 1 m 一次, 再转一整圈
    _event("recharge_recover_backup_1m")
    backup_deadline = time.monotonic() + _BACKUP_1M_S
    while time.monotonic() < backup_deadline:
        obs = _fresh_tag()
        if obs is not None:
            _stop(conn)
            _event(
                "recharge_recover_ok",
                via="during_backup",
                z_m=round(obs.z_m, 4),
                yaw_rad=round(obs.yaw_rad, 4),
            )
            return obs
        conn.move(_twist(vx=-0.15))
        time.sleep(0.1)
    _stop(conn)

    _event("recharge_recover_phase", phase="post_backup_full_360", duration_s=_FULL_TURN_S)
    found = _spin_until_tag(yaw_sign=undo_sign, duration_s=_FULL_TURN_S, via="post_backup_full_360")
    if found is not None:
        return found

    _event("recharge_recover_failed")
    return None


def _run_controller(
    conn: UnitreeWebRTCConnection,
    inputs: LatestInputs,
    *,
    allow_liedown: bool,
    home_z_m: float,
) -> tuple[RechargeState, bool, float, float, float, bool]:
    """跑一轮对接.

    返回 (终态, 可恢复, last_yaw_cmd, last_yaw_error_rad, yaw_turn_s, undo_already_done).
    """
    # demo 直连 WebRTC: 4G 链路偶发丢帧, 放宽 image_max_age 避免误杀.
    # marker_lost_abort 略长, 给控制器内 undo 反转留时间.
    config = RechargeConfig(image_max_age_s=0.80, marker_lost_abort_s=4.0)
    controller = ArucoRechargeController(config)
    controller.start(time.monotonic())
    previous_state = controller.state
    next_tick = time.monotonic()
    lost_recoverable = False
    last_progress_z: float | None = None
    last_progress_at = time.monotonic()
    last_heartbeat = 0.0
    last_yaw_cmd = 0.0
    last_yaw_error_rad = 0.0
    try:
        while controller.active:
            now = time.monotonic()
            observation, image_age_s, lowstate_age_s, _, _ = inputs.snapshot(now)
            if (
                observation is not None
                and now - observation.observed_at > controller.config.image_max_age_s
            ):
                observation = None
            command = controller.tick(
                now,
                observation,
                image_age_s=image_age_s,
                lowstate_age_s=lowstate_age_s,
                charge_confirmed=None,
            )
            if command.yaw_rad_s != 0.0:
                # Keep the last physical yaw command for recovery. If the controller
                # later loses the tag, recovery rotates the opposite way first.
                last_yaw_cmd = command.yaw_rad_s
            if observation is not None:
                last_yaw_error_rad = observation.yaw_rad
            conn.move(_twist(command.forward_mps, command.lateral_mps, command.yaw_rad_s))
            if observation is not None:
                # Progress is measured by z decreasing. A state can move sticks but
                # still stall if the marker distance does not shrink.
                if last_progress_z is None or observation.z_m < last_progress_z - 0.04:
                    last_progress_z = observation.z_m
                    last_progress_at = now
            if now - last_heartbeat >= 2.0:
                _event(
                    "recharge_heartbeat",
                    state=controller.state.value,
                    z_m=None if observation is None else round(observation.z_m, 4),
                    yaw_rad=None if observation is None else round(observation.yaw_rad, 4),
                    cmd_vx=round(command.forward_mps, 3),
                    cmd_yaw=round(command.yaw_rad_s, 3),
                    cmd_vy=round(command.lateral_mps, 3),
                )
                last_heartbeat = now
            # approach / align 卡住: 近距多给时间; 远距或侧向无进展则恢复.
            stall_limit_s = (
                25.0 if (last_progress_z is not None and last_progress_z < 0.80) else 15.0
            )
            if controller.state == RechargeState.ALIGN_LATERAL:
                stall_limit_s = 8.0
            if (
                controller.state
                in {
                    RechargeState.APPROACH,
                    RechargeState.ALIGN_LATERAL,
                    RechargeState.ALIGN_YAW,
                }
                and now - last_progress_at > stall_limit_s
            ):
                _event(
                    "recharge_stall_detected",
                    state=controller.state.value,
                    z_m=last_progress_z,
                    stall_limit_s=stall_limit_s,
                )
                lost_recoverable = True
                controller.cancel(now)
                break
            if controller.state != previous_state:
                cfg = controller.config
                if controller.state == RechargeState.SETTLE and observation is not None:
                    yaw = observation.yaw_rad
                    _event(
                        "recharge_settle_pose",
                        z_m=round(observation.z_m, 4),
                        x_m=round(observation.x_m, 4),
                        yaw_rad=round(yaw, 4),
                        yaw_deg=round(math.degrees(yaw), 2),
                        align_yaw_exit_rad=cfg.align_yaw_exit_rad,
                        settle_yaw_rad=cfg.settle_yaw_rad,
                        yaw_over_align_exit=abs(yaw) > cfg.align_yaw_exit_rad,
                        yaw_over_settle=abs(yaw) > cfg.settle_yaw_rad,
                    )
                if controller.state == RechargeState.LIE_DOWN and observation is not None:
                    yaw = observation.yaw_rad
                    forward_err = observation.z_m - cfg.target_camera_marker_distance_m
                    _event(
                        "recharge_pre_liedown_pose",
                        z_m=round(observation.z_m, 4),
                        x_m=round(observation.x_m, 4),
                        forward_error_m=round(forward_err, 4),
                        lateral_m=round(observation.x_m, 4),
                        yaw_rad=round(yaw, 4),
                        yaw_deg=round(math.degrees(yaw), 2),
                        align_yaw_exit_deg=round(math.degrees(cfg.align_yaw_exit_rad), 2),
                        settle_yaw_deg=round(math.degrees(cfg.settle_yaw_rad), 2),
                        # 仅记录: settle 阶段不再强制 yaw 微调, 近场直接趴下.
                        # 成功样本趴下前 yaw≈9° 仍可充上 (带 A -1048 mA).
                        suggest_yaw_correction=abs(yaw) > cfg.align_yaw_exit_rad,
                        note="near_field_no_yaw_turn_before_liedown",
                    )
                _event(
                    "recharge_state_transition",
                    from_state=previous_state.value,
                    to_state=controller.state.value,
                    x_m=None if observation is None else round(observation.x_m, 4),
                    z_m=None if observation is None else round(observation.z_m, 4),
                    yaw_rad=None if observation is None else round(observation.yaw_rad, 4),
                )
                previous_state = controller.state
                last_progress_at = now
            if controller.failure is not None:
                code = controller.failure.code.value
                # Only visual/search failures are recoverable. Lie-down and charge
                # verification failures need a different handling path.
                lost_recoverable = code in {
                    "marker_lost",
                    "marker_not_found",
                    "image_stale",
                    "state_timeout",
                    "cancelled",
                }
                _event(
                    "recharge_failure",
                    code=code,
                    failed_state=controller.failure.state.value,
                    detail=controller.failure.message,
                    recoverable=lost_recoverable,
                    forward_error_m=(
                        None
                        if controller.failure.dock_error is None
                        else round(controller.failure.dock_error.forward_m, 4)
                    ),
                    lateral_error_m=(
                        None
                        if controller.failure.dock_error is None
                        else round(controller.failure.dock_error.lateral_m, 4)
                    ),
                    yaw_error_rad=(
                        None
                        if controller.failure.dock_error is None
                        else round(controller.failure.dock_error.yaw_rad, 4)
                    ),
                )
            if command.request_liedown:
                # The pure controller only requests lie-down. This script decides
                # whether the physical RPC is allowed for the current field run.
                _, _, _, pre_liedown_current, _ = inputs.snapshot(now)
                _event(
                    "recharge_pre_liedown_bms",
                    z_m=None if observation is None else round(observation.z_m, 4),
                    bms_current=pre_liedown_current,
                )
                controller.complete_visual_dock(now)
                if allow_liedown:
                    liedown_ok = conn.liedown()
                    _event(
                        "recharge_visual_docked",
                        liedown_sent=True,
                        liedown_request_completed=liedown_ok,
                        home_z_m=round(home_z_m, 3),
                        pre_liedown_bms_current=pre_liedown_current,
                    )
                else:
                    _event("recharge_visual_docked", liedown_sent=False)
            next_tick += 0.1
            time.sleep(max(0.0, next_tick - time.monotonic()))
    finally:
        _stop(conn)
    if last_yaw_error_rad == 0.0:
        last_yaw_error_rad = controller._last_seen_yaw_rad
    yaw_turn_s = controller._align_yaw_run_s
    undo_already_done = controller.undo_yaw_s > 0.0
    return (
        controller.state,
        lost_recoverable,
        last_yaw_cmd if last_yaw_cmd != 0.0 else controller.last_yaw_cmd,
        last_yaw_error_rad,
        yaw_turn_s,
        undo_already_done,
    )


def _watch_charge(inputs: LatestInputs, *, timeout_s: float) -> bool:
    """趴下后确认是否真正在充电 (双电流带 + SOC 缓升辅助日志)."""
    verifier = ChargeVerifier(calibrated_go2_4g_charge_rules())
    _, _, _, baseline, _ = inputs.snapshot(time.monotonic())
    _event(
        "recharge_charge_watch_start",
        baseline_current=baseline,
        timeout_s=timeout_s,
        stable_s=4.0,
        rule="band_A: -1500..-500 mA OR band_B: 7500..8500 mA for 4s",
    )
    deadline = time.monotonic() + timeout_s
    samples: list[float] = []
    soc_samples: list[float] = []
    while time.monotonic() < deadline:
        now = time.monotonic()
        _, _, _, current, _ = inputs.snapshot(now)
        if isinstance(current, int | float):
            samples.append(float(current))
        # LatestInputs 不暴露 SOC; 从 lowstate 已在 on_lowstate 里更新 current 即可
        result = verifier.observe_current(
            float(current) if isinstance(current, int | float) else None, now
        )
        if result is True:
            _event(
                "recharge_charge_confirmed",
                current=current,
                baseline=baseline,
                band="negative" if isinstance(current, int | float) and current < 0 else "positive",
            )
            return True
        time.sleep(0.5)
    soc_hint = soc_rising_charge_hint(soc_samples) if soc_samples else None
    _event(
        "recharge_charge_watch_timeout",
        last_current=samples[-1] if samples else None,
        baseline_current=baseline,
        samples=len(samples),
        min_current=min(samples) if samples else None,
        max_current=max(samples) if samples else None,
        soc_rising_hint=soc_hint,
        charged=False,
    )
    return False


def main() -> int:
    """Run observation, one docking attempt, or retry-until-charge on a real 4G Go2."""
    args = parse_args()
    serial = os.getenv("UNITREE_SERIAL")
    if not serial:
        raise SystemExit("UNITREE_SERIAL is required")
    config = global_config
    vision = ArucoRechargeVision(RechargeConfig())
    inputs = LatestInputs(vision)
    conn = UnitreeWebRTCConnection(
        ip=None,
        connection_method="remote",
        username=config.unitree_username,
        password=config.unitree_password,
        serial_number=serial,
        region=config.unitree_region or "cn",
    )
    image_subscription = None
    lowstate_subscription = None
    try:
        image_subscription = conn.video_stream().subscribe(inputs.on_image)
        lowstate_subscription = conn.lowstate_stream().subscribe(inputs.on_lowstate)

        tag = _wait_tag(inputs, timeout_s=min(20.0, args.preflight_timeout_s))
        if tag is None:
            # If the dog starts seated or the tag is outside the current camera view,
            # enable motion and try the explicit visual recovery routine once.
            _enable_motion(conn)
            tag = _recover_tag(conn, inputs, home_z_m=1.2, last_yaw_cmd=0.0)
        if tag is None:
            _event("recharge_preflight_failed", reason="missing_fresh_tag_or_lowstate")
            return 2

        home_z_m = max(0.9, min(tag.z_m, 2.0))
        _event(
            "recharge_preflight_passed",
            x_m=round(tag.x_m, 4),
            y_m=round(tag.y_m, 4),
            z_m=round(tag.z_m, 4),
            yaw_rad=round(tag.yaw_rad, 4),
            home_z_m=round(home_z_m, 3),
            reprojection_error_px=round(tag.reprojection_error_px, 4),
        )
        if not args.execute:
            _event("recharge_observation_only_complete")
            return 0

        _enable_motion(conn)
        # standup 后可能丢码, 再找回一次
        tag = _wait_tag(inputs, timeout_s=10.0)
        last_yaw_cmd = 0.0
        last_yaw_error_rad = 0.0
        yaw_turn_s = 0.0
        if tag is None:
            tag = _recover_tag(conn, inputs, home_z_m=home_z_m, last_yaw_cmd=last_yaw_cmd)
        if tag is None:
            _event("recharge_failed_no_tag_after_stand")
            return 2

        attempts = args.max_attempts if args.until_charge else 1
        for attempt in range(1, attempts + 1):
            # Each attempt starts a fresh pure controller but reuses the live WebRTC
            # subscriptions and the recovery hints from the prior attempt.
            _event("recharge_attempt", attempt=attempt, max_attempts=attempts)
            (
                final_state,
                recoverable,
                last_yaw_cmd,
                last_yaw_error_rad,
                yaw_turn_s,
                undo_already_done,
            ) = _run_controller(conn, inputs, allow_liedown=args.allow_liedown, home_z_m=home_z_m)
            _event("recharge_attempt_finished", attempt=attempt, final_state=final_state.value)

            if final_state == RechargeState.VISUAL_DOCKED:
                if args.allow_liedown:
                    charged = _watch_charge(inputs, timeout_s=args.charge_watch_s)
                    if charged or not args.until_charge:
                        _event(
                            "recharge_finished",
                            final_state=final_state.value,
                            charged=charged,
                            attempt=attempt,
                        )
                        return 0 if charged or not args.until_charge else 4
                    # 趴下了但电流未确认: 起立重试
                    _event("recharge_retry_after_unconfirmed_charge")
                    _enable_motion(conn)
                    tag = _recover_tag(
                        conn,
                        inputs,
                        home_z_m=home_z_m,
                        last_yaw_cmd=last_yaw_cmd,
                        last_yaw_error_rad=last_yaw_error_rad,
                        yaw_turn_s=yaw_turn_s,
                        skip_undo=undo_already_done,
                    )
                    if tag is None:
                        return 2
                    continue
                _event("recharge_finished", final_state=final_state.value, charged=False)
                return 0

            if not args.until_charge:
                _event("recharge_finished", final_state=final_state.value)
                return 3

            _event("recharge_recover_and_retry", recoverable=recoverable, attempt=attempt)
            tag = _recover_tag(
                conn,
                inputs,
                home_z_m=home_z_m,
                last_yaw_cmd=last_yaw_cmd,
                last_yaw_error_rad=last_yaw_error_rad,
                yaw_turn_s=yaw_turn_s,
                skip_undo=undo_already_done,
            )
            if tag is None:
                _event("recharge_abort_cannot_reacquire")
                return 2

        _event("recharge_finished", final_state="max_attempts")
        return 3
    finally:
        _stop(conn)
        if image_subscription is not None:
            image_subscription.dispose()
        if lowstate_subscription is not None:
            lowstate_subscription.dispose()
        conn.stop()


if __name__ == "__main__":
    raise SystemExit(main())
