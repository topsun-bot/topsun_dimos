#!/usr/bin/env python3
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

"""List Orin input devices, scan for mic signal, optional Whisper check.

Usage on Orin::

    bash ~/topsun_dimos/scripts/orin_test_mic.sh              # default input
    bash ~/topsun_dimos/scripts/orin_test_mic.sh scan         # pulse/default only (fast)
    bash ~/topsun_dimos/scripts/orin_test_mic.sh scan-ape     # also try tegra APE (slow, may hang)
    bash ~/topsun_dimos/scripts/orin_test_mic.sh 27           # pulse default
"""

from __future__ import annotations

import sys
import threading

import numpy as np
import sounddevice as sd  # type: ignore[import-untyped]

_VAD_THRESHOLD = 0.008
_MIN_SIGNAL_RMS = 0.003


def _rms(audio: np.ndarray) -> float:
    if audio.size == 0:
        return 0.0
    flat = audio.astype(np.float32, copy=False).reshape(-1)
    return float(np.sqrt(np.mean(flat * flat)))


def _list_input_devices() -> list[tuple[int, dict]]:
    devices: list[tuple[int, dict]] = []
    for idx, dev in enumerate(sd.query_devices()):
        info = dict(dev)
        if int(info.get("max_input_channels", 0) or 0) > 0:
            devices.append((idx, info))
    return devices


def _print_input_devices() -> None:
    print("=== 可用输入设备 (max_input_channels > 0) ===")
    for idx, info in _list_input_devices():
        name = info.get("name", "?")
        ch = info.get("max_input_channels", "?")
        sr = info.get("default_samplerate", "?")
        mark = " *" if idx == sd.default.device[0] else ""
        print(f"  [{idx:2d}] {name}  (in={ch}, sr={sr}){mark}")
    print("  (* = sounddevice 默认输入)")
    print("  设备 0-3 是 HDMI 输出，不能录音。")
    print("  tegra APE [4-23] 在部分 Orin 上会卡住，优先用 scan (pulse/default)。")
    print()


def _resolve_device(arg: str | None) -> tuple[int | None, dict]:
    if arg is None:
        info = dict(sd.query_devices(kind="input"))
        default_idx = sd.default.device[0]
        return int(default_idx) if default_idx is not None else None, info

    lowered = arg.lower()
    if lowered in ("scan", "scan-ape"):
        raise ValueError(lowered)

    idx = int(arg)
    info = dict(sd.query_devices(idx))
    if int(info.get("max_input_channels", 0) or 0) <= 0:
        raise ValueError(
            f"设备 {idx} ({info.get('name', '?')}) 没有输入通道。"
            " HDMI 0-3 是纯输出。请试: orin_test_mic.sh scan 或 index 27"
        )
    return idx, info


def _record_unsafe(device: int | None, seconds: float) -> tuple[np.ndarray, int, float]:
    info = (
        sd.query_devices(device, "input")
        if device is not None
        else sd.query_devices(kind="input")
    )
    sample_rate = int(info.get("default_samplerate", 16000) or 16000)
    audio = sd.rec(
        int(seconds * sample_rate),
        samplerate=sample_rate,
        channels=1,
        dtype="float32",
        device=device,
    )
    sd.wait()
    return audio, sample_rate, _rms(audio)


def _record(
    device: int | None, seconds: float, *, timeout_s: float | None = None
) -> tuple[np.ndarray, int, float]:
    if timeout_s is None:
        timeout_s = seconds + 4.0

    result_box: dict[str, object] = {}
    error_box: dict[str, BaseException] = {}

    def _worker() -> None:
        try:
            audio, sample_rate, peak = _record_unsafe(device, seconds)
            result_box["value"] = (audio, sample_rate, peak)
        except BaseException as exc:
            error_box["exc"] = exc

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout_s)
    if thread.is_alive():
        try:
            sd.stop()
        except Exception:
            pass
        thread.join(1.0)
        label = device if device is not None else "default"
        raise TimeoutError(f"设备 {label} 录音超时 ({timeout_s:.0f}s)，已跳过")

    if "exc" in error_box:
        raise error_box["exc"] from None
    value = result_box.get("value")
    if value is None:
        raise RuntimeError("录音失败")
    return value  # type: ignore[return-value]


def _is_ape_device(name: str) -> bool:
    return "APE" in name


def _scan_indices(include_ape: bool) -> list[int]:
    inputs = _list_input_devices()
    by_idx = {idx: str(d.get("name", "")) for idx, d in inputs}

    ordered: list[int] = []
    for preferred in (27, 25):
        if preferred in by_idx:
            ordered.append(preferred)

    default_idx = sd.default.device[0]
    if default_idx is not None and int(default_idx) not in ordered:
        ordered.append(int(default_idx))

    for idx, name in sorted(by_idx.items()):
        if idx in ordered:
            continue
        if "pulse" in name.lower() or "default" in name.lower():
            ordered.append(idx)

    if include_ape:
        for idx, name in sorted(by_idx.items()):
            if idx in ordered:
                continue
            if _is_ape_device(name):
                ordered.append(idx)

    return ordered


def _scan_inputs(*, include_ape: bool) -> int | None:
    indices = _scan_indices(include_ape)
    if not indices:
        print("未找到任何输入设备。")
        return None

    scan_seconds = 1.0 if include_ape else 2.0
    timeout_s = 2.5 if include_ape else 6.0
    mode = "pulse/default + tegra APE" if include_ape else "pulse/default"
    print(f"扫描模式: {mode}，{len(indices)} 个设备，每个约 {scan_seconds}s …")
    print("请对着麦克风大声说话 …\n")

    results: list[tuple[int, str, float]] = []
    for idx in indices:
        name = str(dict(sd.query_devices(idx)).get("name", "?"))
        print(f"  测试 [{idx:2d}] {name} ...", flush=True)
        try:
            _, _, peak = _record(idx, scan_seconds, timeout_s=timeout_s)
            results.append((idx, name, peak))
            print(f"           RMS={peak:.6f}")
        except TimeoutError as exc:
            print(f"           超时跳过 ({exc})")
        except Exception as exc:
            print(f"           失败跳过 ({exc})")

    results.sort(key=lambda x: x[2], reverse=True)
    print()
    if not results or results[0][2] < _MIN_SIGNAL_RMS:
        print("未找到有效麦克风信号 (ALSA/pulse)。")
        print("G1 机身麦不走 ALSA，请试:")
        print("  bash ~/topsun_dimos/scripts/orin_test_body_mic.sh eth0")
        if not include_ape:
            print("或: bash ~/topsun_dimos/scripts/orin_test_mic.sh scan-ape")
        print("建议接 USB 麦克风后重试 scan，或用 agent-send 文字输入。")
        return None

    best_idx, best_name, best_rms = results[0]
    print(f"推荐: index={best_idx}  RMS={best_rms:.6f}  ({best_name})")
    print(f"  export DIMOS_MIC_DEVICE_INDEX={best_idx}")
    return best_idx


def _whisper_check(audio: np.ndarray, sample_rate: int) -> None:
    try:
        from faster_whisper import WhisperModel  # type: ignore[import-untyped]

        print("\nWhisper tiny 识别中…")
        model = WhisperModel("tiny", device="cpu", compute_type="int8")
        segments, _ = model.transcribe(audio.reshape(-1), language="zh")
        text = "".join(s.text for s in segments).strip()
        print(f"识别结果: {text!r}")
        if not text:
            print("Whisper 未识别出文字，请更清晰、更慢地说，句末停顿 1 秒。")
    except Exception as exc:
        print(f"(跳过 Whisper 测试: {exc})")


def main() -> None:
    _print_input_devices()

    arg = sys.argv[1] if len(sys.argv) > 1 else None
    if arg is not None and arg.lower() == "scan":
        best = _scan_inputs(include_ape=False)
        if best is None:
            sys.exit(1)
        print(f"\n对最佳设备再录 5 秒做 Whisper 验证 (index={best}) …")
        audio, sample_rate, peak = _record(best, 5.0)
        print(f"RMS={peak:.6f}")
        _whisper_check(audio, sample_rate)
        return

    if arg is not None and arg.lower() == "scan-ape":
        best = _scan_inputs(include_ape=True)
        if best is None:
            sys.exit(1)
        print(f"\n对最佳设备再录 5 秒做 Whisper 验证 (index={best}) …")
        audio, sample_rate, peak = _record(best, 5.0)
        print(f"RMS={peak:.6f}")
        _whisper_check(audio, sample_rate)
        return

    try:
        device, info = _resolve_device(arg)
    except ValueError as exc:
        if str(exc) in ("scan", "scan-ape"):
            raise
        print(f"错误: {exc}")
        print("提示: bash ~/topsun_dimos/scripts/orin_test_mic.sh scan")
        sys.exit(1)

    label = f"index={device}" if device is not None else "default"
    print(f"使用设备 {label}: {info.get('name', '?')}")
    print("\n请对着麦克风大声说 5 秒 (例如: 你好, 语音测试) ...")
    try:
        audio, sample_rate, peak = _record(device, 5.0)
    except TimeoutError as exc:
        print(f"录音超时: {exc}")
        print("tegra APE 设备常会卡住，请试: orin_test_mic.sh scan 或 index 27")
        sys.exit(1)

    print(f"录音完成: 5s @ {sample_rate}Hz, RMS={peak:.6f}")

    if peak < _MIN_SIGNAL_RMS:
        print(
            "几乎没收到声音。\n"
            "  - 运行: bash ~/topsun_dimos/scripts/orin_test_mic.sh scan\n"
            "  - 或接 USB 麦后重试 index 27 (pulse)"
        )
        sys.exit(1)
    if peak < _VAD_THRESHOLD:
        print(f"声音偏弱 (RMS < {_VAD_THRESHOLD})，VadVoiceInput 可能触发不了。")
    else:
        print(f"麦克风有信号，VAD 阈值 {_VAD_THRESHOLD} 应能触发。")
        if device is not None:
            print(f"  export DIMOS_MIC_DEVICE_INDEX={device}")

    _whisper_check(audio, sample_rate)
    print('\n临时用文字绕过麦克风: bash ~/topsun_dimos/scripts/orin_dimos.sh agent-send "你好"')


if __name__ == "__main__":
    main()
