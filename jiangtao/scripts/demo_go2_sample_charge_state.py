#!/usr/bin/env python3
"""采样 Go2 充电中的 lowstate / 相关 WebRTC topic, 用于标定充电判定.

凭据从 .env 读取. 狗应已趴在桩上并真正在充电.

  source .venv/bin/activate && set -a && source .env && set +a
  uv run python jiangtao/scripts/demo_go2_sample_charge_state.py --seconds 20
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
import threading
import time
from typing import Any

from dimos.core.global_config import global_config
from dimos.robot.unitree.connection import UnitreeWebRTCConnection

try:
    from unitree_webrtc_connect.constants import RTC_TOPIC
except ImportError:  # pragma: no cover
    RTC_TOPIC = {}


def _event(name: str, **fields: object) -> None:
    print(json.dumps({"event": name, **fields}, ensure_ascii=False, default=str), flush=True)


def _dig(obj: Any, *keys: str) -> Any:
    cur = obj
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


class Sampler:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.lowstate_n = 0
        self.last_lowstate: dict[str, Any] | None = None
        self.bms_samples: list[dict[str, Any]] = []
        self.extra: dict[str, list[Any]] = {
            "self_test": [],
            "service_state": [],
            "multiple_state": [],
            "sport_mod_state": [],
        }

    def on_lowstate(self, msg: object) -> None:
        if not isinstance(msg, dict):
            return
        data = msg.get("data") if isinstance(msg.get("data"), dict) else msg
        bms = data.get("bms_state") if isinstance(data, dict) else None
        sample = {
            "ts": time.time(),
            "power_v": data.get("power_v") if isinstance(data, dict) else None,
            "bms_current": _dig(bms, "current") if isinstance(bms, dict) else None,
            "bms_soc": _dig(bms, "soc") if isinstance(bms, dict) else None,
            "bms_status": _dig(bms, "status") if isinstance(bms, dict) else None,
            "bms_bq_ntc": _dig(bms, "bq_ntc") if isinstance(bms, dict) else None,
            "bms_mos_ntc": _dig(bms, "mos_ntc") if isinstance(bms, dict) else None,
            "bms_cell_vol": _dig(bms, "cell_vol") if isinstance(bms, dict) else None,
            "bms_raw": bms,
            "keys": sorted(data.keys()) if isinstance(data, dict) else [],
        }
        with self._lock:
            self.lowstate_n += 1
            self.last_lowstate = msg if isinstance(msg, dict) else {"data": data}
            self.bms_samples.append(sample)

    def on_named(self, name: str):
        def _cb(msg: object) -> None:
            with self._lock:
                bucket = self.extra.setdefault(name, [])
                if len(bucket) < 30:
                    bucket.append(msg)

        return _cb


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--seconds", type=float, default=20.0)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    serial = os.getenv("UNITREE_SERIAL")
    if not serial:
        raise SystemExit("UNITREE_SERIAL is required")
    cfg = global_config
    sampler = Sampler()
    conn = UnitreeWebRTCConnection(
        ip=None,
        connection_method="remote",
        username=cfg.unitree_username,
        password=cfg.unitree_password,
        serial_number=serial,
        region=cfg.unitree_region or "cn",
    )
    subs = []
    try:
        subs.append(conn.lowstate_stream().subscribe(sampler.on_lowstate))
        # 额外探测可能带充电/服务状态的 topic
        for key, bucket in (
            ("SELF_TEST", "self_test"),
            ("SERVICE_STATE", "service_state"),
            ("MULTIPLE_STATE", "multiple_state"),
            ("SPORT_MOD_STATE", "sport_mod_state"),
            ("LF_SPORT_MOD_STATE", "sport_mod_state"),
        ):
            topic = RTC_TOPIC.get(key)
            if not topic:
                continue
            try:
                subs.append(conn.unitree_sub_stream(topic).subscribe(sampler.on_named(bucket)))
                _event("charge_sample_subscribed", topic_key=key, topic=topic)
            except Exception as exc:
                _event("charge_sample_subscribe_failed", topic_key=key, error=str(exc))

        _event("charge_sample_start", seconds=args.seconds)
        time.sleep(args.seconds)

        with sampler._lock:
            samples = list(sampler.bms_samples)
            extras = {k: list(v) for k, v in sampler.extra.items()}
            n = sampler.lowstate_n

        if not samples:
            _event("charge_sample_failed", reason="no_lowstate", lowstate_n=n)
            return 2

        currents = [s["bms_current"] for s in samples if isinstance(s["bms_current"], int | float)]
        socs = [s["bms_soc"] for s in samples if isinstance(s["bms_soc"], int | float)]
        statuses = [s["bms_status"] for s in samples if s["bms_status"] is not None]
        powers = [s["power_v"] for s in samples if isinstance(s["power_v"], int | float)]

        # 打印若干完整样本
        for idx in (0, len(samples) // 2, len(samples) - 1):
            s = samples[idx]
            _event(
                "charge_sample_bms",
                index=idx,
                power_v=s["power_v"],
                bms_current=s["bms_current"],
                bms_soc=s["bms_soc"],
                bms_status=s["bms_status"],
                bms_bq_ntc=s["bms_bq_ntc"],
                bms_mos_ntc=s["bms_mos_ntc"],
                cell_vol_n=len(s["bms_cell_vol"]) if isinstance(s["bms_cell_vol"], list) else None,
                bms_raw=s["bms_raw"],
            )

        summary = {
            "lowstate_n": n,
            "sample_n": len(samples),
            "current_min": min(currents) if currents else None,
            "current_max": max(currents) if currents else None,
            "current_median": sorted(currents)[len(currents) // 2] if currents else None,
            "soc_min": min(socs) if socs else None,
            "soc_max": max(socs) if socs else None,
            "power_v_min": min(powers) if powers else None,
            "power_v_max": max(powers) if powers else None,
            "bms_status_counts": dict(Counter(map(str, statuses))),
            "lowstate_keys": samples[-1]["keys"],
        }
        _event("charge_sample_summary", **summary)

        for name, msgs in extras.items():
            if not msgs:
                _event("charge_sample_extra_empty", topic_name=name)
                continue
            # 只打最后一条, 避免刷屏
            last = msgs[-1]
            preview = last
            if isinstance(last, dict):
                preview = {k: last[k] for k in list(last)[:20]}
            _event("charge_sample_extra", topic_name=name, count=len(msgs), last_preview=preview)

        return 0
    finally:
        for sub in subs:
            try:
                sub.dispose()
            except Exception:
                pass
        conn.stop()


if __name__ == "__main__":
    raise SystemExit(main())
