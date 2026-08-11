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

"""Tests for BMS charge confirmation that keep unknown field polarity safe."""

from __future__ import annotations

from dimos.robot.unitree.go2.recharge.charge_verify import (
    ChargeCurrentRule,
    ChargeVerifier,
    calibrated_go2_4g_charge_rules,
    soc_rising_charge_hint,
)


def _lowstate(current: int) -> dict:
    return {"data": {"bms_state": {"current": current}}}


def test_verifier_never_claims_charge_without_calibrated_threshold() -> None:
    verifier = ChargeVerifier(ChargeCurrentRule())

    result = verifier.observe(_lowstate(-2500), 0.0)

    assert result is None


def test_verifier_requires_full_stable_duration_before_success() -> None:
    verifier = ChargeVerifier(
        ChargeCurrentRule(threshold=-1000, direction="below", stable_duration_s=10.0)
    )

    assert verifier.observe(_lowstate(-1500), 0.0) is False
    assert verifier.observe(_lowstate(-1500), 9.9) is False
    assert verifier.observe(_lowstate(-1500), 10.0) is True


def test_verifier_resets_after_non_charging_sample() -> None:
    verifier = ChargeVerifier(
        ChargeCurrentRule(threshold=-1000, direction="below", stable_duration_s=10.0)
    )

    verifier.observe(_lowstate(-1500), 0.0)
    assert verifier.observe(_lowstate(0), 9.0) is False
    assert verifier.observe(_lowstate(-1500), 10.0) is False
    assert verifier.observe(_lowstate(-1500), 20.0) is True


def test_calibrated_band_rejects_standing_draw_and_accepts_dock_charge() -> None:
    verifier = ChargeVerifier(calibrated_go2_4g_charge_rules())
    # 站立未充实采 ≈ -2172, 两带之外
    assert verifier.observe(_lowstate(-2172), 0.0) is False
    # 趴桩充电 (负电流样本) ≈ -1030
    assert verifier.observe(_lowstate(-1030), 0.0) is False
    assert verifier.observe(_lowstate(-1030), 4.0) is True


def test_calibrated_band_accepts_positive_dock_charge_current() -> None:
    verifier = ChargeVerifier(calibrated_go2_4g_charge_rules())
    # 晚场对齐趴桩: +8030 mA 量级
    assert verifier.observe(_lowstate(8050), 0.0) is False
    assert verifier.observe(_lowstate(8050), 4.0) is True
    assert verifier.observe(_lowstate(8081), 4.0) is True
    # 带外
    assert verifier.observe(_lowstate(6000), 0.0) is False


def test_soc_rising_hint() -> None:
    assert soc_rising_charge_hint([65, 65, 66, 66, 67], min_rise=1.0, min_samples=5) is True
    assert soc_rising_charge_hint([71, 71, 71, 71, 71], min_rise=1.0, min_samples=5) is False
    assert soc_rising_charge_hint([71, 71], min_samples=5) is None
