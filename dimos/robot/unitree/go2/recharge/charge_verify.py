# Copyright 2026 Dimensional Inc.
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

"""趴桩后 BMS 电流确认 (4G WebRTC rt/lf/lowstate).

2026-08-05 同狗实测 (demo_go2_4g_aruco_recharge.py):
  站立未充: current ≈ -2172 ~ -2264 mA (两带电外, 不会误判)
  趴桩充电带 A: -1500 ~ -500 mA (成功样本 -1048 mA, 4 s 稳定)
  趴桩充电带 B: +7500 ~ +8500 mA (对齐样本 +8030 mA, SOC 65~71%)
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class ChargeCurrentRule:
    """A site-calibrated BMS current band; ``None`` threshold means unverified.

    两种写法 (二选一):
    1. ``threshold`` + ``direction`` + 可选 ``current_floor`` (旧负电流带)
    2. ``band_min`` + ``band_max`` 闭区间 (新正电流带, 见 2026-08-05 三狗实采)
    """

    threshold: float | None = None
    direction: Literal["below", "above"] = "below"
    stable_duration_s: float = 4.0
    # 充电带下沿 (含): current >= current_floor. 过负=站立耗电, 不是稳充.
    current_floor: float | None = None
    # 闭区间充电带 (含端点). 与 threshold 模式互斥.
    band_min: float | None = None
    band_max: float | None = None


def calibrated_go2_4g_charge_rule() -> ChargeCurrentRule:
    """兼容旧调用: 返回默认标定策略中的第一条规则."""
    return calibrated_go2_4g_charge_rules()[0]


def calibrated_go2_4g_charge_rules() -> tuple[ChargeCurrentRule, ...]:
    """三狗 4G WebRTC ``rt/lf/lowstate`` 现场标定 (2026-08-05, 2026-08-05 晚补充).

    带 A — 负电流 (早期趴桩样本):
      current ≈ -1030 mA, 即 -1500 <= current <= -500, 连续 4s.

    带 B — 正电流 (同狗晚场趴桩对齐样本, SOC 65~71% 均在充):
      current ≈ +8030 mA (7933~8121), 即 7500 <= current <= 8500, 连续 4s.

    站立未充: current ≈ -2172 mA (落在带 A 外、带 B 外).

    ``power_v`` / ``bms_status`` 在 WebRTC 上区分度差, 不作为主判据.
    SOC 缓升可作辅助 (1% 可能要数分钟), 见 ``soc_rising_charge_hint()``.
    """
    return (
        ChargeCurrentRule(
            threshold=-500.0,
            direction="below",
            stable_duration_s=4.0,
            current_floor=-1500.0,
        ),
        ChargeCurrentRule(
            band_min=7500.0,
            band_max=8500.0,
            stable_duration_s=4.0,
        ),
    )


def soc_rising_charge_hint(
    soc_samples: Sequence[int | float],
    *,
    min_rise: float = 1.0,
    min_samples: int = 8,
) -> bool | None:
    """辅助: SOC 在采样窗口内单调上升 >= min_rise 百分点.

    电流带为主判据; SOC 太慢, 仅作 hint (True/False/None 样本不足).
    """
    if len(soc_samples) < min_samples:
        return None
    start = float(soc_samples[0])
    end = float(soc_samples[-1])
    if end - start >= min_rise and end >= start:
        return True
    if end <= start:
        return False
    return None


class ChargeVerifier:
    """Require one calibrated current condition continuously before reporting charge success."""

    def __init__(self, rules: ChargeCurrentRule | Sequence[ChargeCurrentRule]) -> None:
        if isinstance(rules, ChargeCurrentRule):
            self.rules: tuple[ChargeCurrentRule, ...] = (rules,)
        else:
            self.rules = tuple(rules)
        self._stable_duration_s = max(rule.stable_duration_s for rule in self.rules)
        self._condition_started_at: float | None = None
        self.last_current: float | None = None

    def observe(self, lowstate: Mapping[str, Any] | None, now: float) -> bool | None:
        """Return True on stable charging, False on a calibrated non-charge, None when unverified."""
        return self.observe_current(_extract_bms_current(lowstate), now)

    def observe_current(self, current: float | None, now: float) -> bool | None:
        """Evaluate an already-extracted current sample from an RPC or low-state stream."""
        # No calibrated rule means "unknown", never "charging". This avoids claiming
        # success from a BMS field whose polarity/unit has not been checked on this Go2.
        if not self.rules or all(
            rule.threshold is None and (rule.band_min is None or rule.band_max is None)
            for rule in self.rules
        ):
            return None
        self.last_current = current
        if current is None:
            # Missing lowstate breaks continuity, so the stable window restarts.
            self._condition_started_at = None
            return None
        if not self._matches_any(current):
            self._condition_started_at = None
            return False
        if self._condition_started_at is None:
            self._condition_started_at = now
        if now - self._condition_started_at >= self._stable_duration_s:
            return True
        return False

    def _matches_any(self, current: float) -> bool:
        """True when one of the calibrated charging-current bands matches."""
        return any(self._matches_rule(current, rule) for rule in self.rules)

    def _matches_rule(self, current: float, rule: ChargeCurrentRule) -> bool:
        """Evaluate one rule, supporting either closed band or threshold mode."""
        if rule.band_min is not None and rule.band_max is not None:
            return rule.band_min <= current <= rule.band_max
        if rule.threshold is None:
            return False
        if rule.direction == "below":
            if current > rule.threshold:
                return False
        elif current < rule.threshold:
            return False
        if rule.current_floor is not None and current < rule.current_floor:
            return False
        return True


def _extract_bms_current(lowstate: Mapping[str, Any] | None) -> float | None:
    """Extract Unitree's raw BMS current without assuming its unit or polarity."""
    try:
        data = lowstate.get("data") if lowstate is not None else None
        bms_state = data.get("bms_state") if isinstance(data, Mapping) else None
        value = bms_state.get("current") if isinstance(bms_state, Mapping) else None
        return float(value) if isinstance(value, int | float) else None
    except (KeyError, TypeError):
        return None
