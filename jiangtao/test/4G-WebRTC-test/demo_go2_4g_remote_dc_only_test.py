#!/usr/bin/env python3
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

"""Go2 4G Remote WebRTC 稳定连接验收 (Clash 可保持运行).

根因 (本机已验证):
  官方 unitree-webrtc-connect 在 createOffer 时会同时加入
  audio + video + datachannel 三条 m-line. aiortc 默认
  bundlePolicy=balanced 会给每条 m-line 不同的 ice-ufrag.
  经 Unitree TURN 时经常出现 ICE completed 但 DTLS 失败
  (peer 一直 connecting, DataChannel 超时).

修复:
  初始 SDP 只谈 DataChannel (不 addTransceiver audio/video).
  lowstate / odom / 控制 / lidar_state 都走 DataChannel, 足够做
  控制与状态验收. Clash 系统代理可保持, 无需退出.

说明:
  - 本脚本只做 DataChannel 控制/状态基线, 没有按完整流程开启并订阅点云.
    后续实测已确认 4G 可推送 voxel_map_compressed; 需要先执行
    disableTrafficSaving(True) + ULIDAR_SWITCH=on, 再订阅压缩点云 topic.
  - 前置相机需要 video transceiver, 与上述 DTLS bug 冲突;
    后续可再单独做视频方案.

用法:
  python jiangtao/plan/demo_go2_4g_remote_dc_only_test.py --duration 60
  python jiangtao/plan/demo_go2_4g_remote_dc_only_test.py --duration 60 --no-motion
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable
import getpass
from importlib.metadata import version
from itertools import pairwise
import json
import logging
from pathlib import Path
import platform
import socket
import statistics
import sys
import time
from typing import Any

from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
from unitree_webrtc_connect.constants import RTC_TOPIC, SPORT_CMD
from unitree_webrtc_connect.unitree_auth import send_sdp_to_remote_peer
from unitree_webrtc_connect.unitree_cloud import UnitreeCloud
from unitree_webrtc_connect.util import fetch_public_key, fetch_turn_server_info, print_status
from unitree_webrtc_connect.webrtc_datachannel import WebRTCDataChannel
from unitree_webrtc_connect.webrtc_driver import UnitreeWebRTCConnection, WebRTCConnectionMethod


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Go2 4G Remote: DataChannel-only stable connect + state/lidar_state test"
    )
    parser.add_argument("--region", choices=("cn", "global"), default="cn")
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--no-motion", action="store_true")
    return parser.parse_args()


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * fraction)]


def summarize_times(times: list[float], elapsed: float) -> dict[str, Any]:
    gaps_ms = [(right - left) * 1000 for left, right in pairwise(times)]
    return {
        "frames": len(times),
        "rate_hz": round(len(times) / elapsed, 2) if elapsed > 0 else 0.0,
        "gap_p50_ms": round(statistics.median(gaps_ms), 1) if gaps_ms else None,
        "gap_p95_ms": round(percentile(gaps_ms, 0.95), 1) if gaps_ms else None,
        "gap_max_ms": round(max(gaps_ms), 1) if gaps_ms else None,
        "gaps_over_1s": sum(gap > 1000 for gap in gaps_ms),
    }


def response_code(response: Any) -> int | None:
    try:
        return response["data"]["header"]["status"]["code"]
    except (KeyError, TypeError):
        return None


async def request(
    conn: UnitreeWebRTCConnection, topic: str, options: dict[str, Any], timeout: float = 10.0
) -> Any:
    return await asyncio.wait_for(
        conn.datachannel.pub_sub.publish_request_new(topic, options), timeout=timeout
    )


async def wait_until(predicate: Callable[[], bool], timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.2)
    return predicate()


async def connect_datachannel_only(
    serial: str,
    token: str,
    region: str,
) -> tuple[UnitreeWebRTCConnection, dict[str, Any]]:
    """只谈 DataChannel 的 Remote 建连. 这是本机 Clash 环境下的稳定路径."""
    public_key = fetch_public_key(region=region, device_type="Go2")
    turn = fetch_turn_server_info(serial, token, public_key, region=region, device_type="Go2")

    # 仅 Unitree TURN, 不加 Google STUN
    cfg = RTCConfiguration(
        iceServers=[
            RTCIceServer(
                urls=[turn["realm"]],
                username=turn["user"],
                credential=turn["passwd"],
            )
        ]
    )
    pc = RTCPeerConnection(cfg)
    conn = UnitreeWebRTCConnection(
        WebRTCConnectionMethod.Remote,
        serialNumber=serial,
        region=region,
        device_type="Go2",
    )
    conn.token = token
    conn.public_key = public_key
    conn.pc = pc
    # 关键: 不创建 WebRTCAudioChannel / WebRTCVideoChannel (它们会 addTransceiver)
    conn.datachannel = WebRTCDataChannel(conn, pc)

    @pc.on("iceconnectionstatechange")
    def _on_ice() -> None:
        print_status("ICE", pc.iceConnectionState)

    @pc.on("connectionstatechange")
    def _on_peer() -> None:
        print_status("Peer", pc.connectionState)
        if pc.connectionState == "connected":
            conn.isConnected = True

    print_status("WebRTC connection", "started (datachannel-only)")
    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    for _ in range(100):
        if pc.iceGatheringState == "complete":
            break
        await asyncio.sleep(0.05)

    m_lines = [line for line in pc.localDescription.sdp.splitlines() if line.startswith("m=")]
    print(f"local offer m-lines: {m_lines}")

    payload = {
        "id": "",
        "turnserver": turn,
        "sdp": pc.localDescription.sdp,
        "type": pc.localDescription.type,
        "token": token,
    }
    answer_json = send_sdp_to_remote_peer(
        serial, json.dumps(payload), token, public_key, region=region, device_type="Go2"
    )
    if not answer_json:
        raise RuntimeError("No SDP answer from robot")
    peer_answer = json.loads(answer_json)
    if peer_answer.get("sdp") == "reject":
        raise RuntimeError("RobotBusyError: robot rejected SDP (another client holds the slot)")

    await pc.setRemoteDescription(
        RTCSessionDescription(sdp=peer_answer["sdp"], type=peer_answer["type"])
    )

    deadline = time.monotonic() + 25.0
    while time.monotonic() < deadline:
        if conn.datachannel.data_channel_opened and pc.connectionState == "connected":
            print_status("WebRTC connection", "connected")
            return conn, turn
        await asyncio.sleep(0.2)

    raise TimeoutError(
        f"DataChannel/DTLS timeout: peer={pc.connectionState} "
        f"ice={pc.iceConnectionState} ch={conn.datachannel.channel.readyState}"
    )


async def run_test(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    print("\n账号、密码和 SN 只保存在本进程内, 不会写入报告.")
    account = input("Unitree 账号 (中国账号可输入手机号): ").strip()
    password = getpass.getpass("Unitree 密码 (输入时不可见): ")
    serial = input("Go2 SN: ").strip()
    if not account or not password or not serial:
        raise ValueError("账号、密码和 SN 均不能为空")

    report: dict[str, Any] = {
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "unitree_webrtc_connect": version("unitree-webrtc-connect"),
        "aiortc": version("aiortc"),
        "region": args.region,
        "duration_requested_s": args.duration,
        "offer_mode": "datachannel_only",
        "clash_compatible": True,
        "fix_note": (
            "Initial SDP offers only DataChannel to avoid aiortc multi-m-line "
            "DTLS failure over Unitree TURN. Clash may keep running."
        ),
        "turn_dns": [],
        "cloud_login": False,
        "serial_bound": False,
        "connected": False,
        "robot_network_mode": "unknown",
        "control": {},
        "data": {},
        "errors": [],
    }
    conn: UnitreeWebRTCConnection | None = None
    subscribed: list[str] = []
    motion_started = False

    try:
        report["turn_dns"] = sorted(
            {item[4][0] for item in socket.getaddrinfo("turn.unitree.com", 5349)}
        )
        if any(ip.startswith(("198.18.", "198.19.")) for ip in report["turn_dns"]):
            print("警告: TURN 解析到 198.18/15 fake-ip, 请检查 Clash TUN/DNS.")

        print("\n[1/5] 登录 Unitree 云并检查设备绑定……")
        t0 = time.monotonic()
        cloud = UnitreeCloud(region=args.region, device_type="Go2")
        token = cloud.login_email(account, password)
        report["cloud_login"] = bool(token)
        report["cloud_login_s"] = round(time.monotonic() - t0, 3)
        devices = cloud.list_devices()
        report["serial_bound"] = any(device.sn == serial for device in devices)
        if not report["serial_bound"]:
            raise RuntimeError(f"这个 SN 未绑定到当前 {args.region} 区域账号")
        print(f"云登录成功, 账号共绑定 {len(devices)} 台设备.")

        print("\n[2/5] 建立 Remote WebRTC (DataChannel-only + TURN)……")
        t1 = time.monotonic()
        conn, _turn = await connect_datachannel_only(serial, token, args.region)
        report["connect_s"] = round(time.monotonic() - t1, 3)
        report["connected"] = True
        report["peer_state"] = conn.pc.connectionState
        report["ice_state"] = conn.pc.iceConnectionState
        report["datachannel_state"] = conn.datachannel.channel.readyState
        await wait_until(
            lambda: bool(conn.datachannel.rtc_inner_req.network_status.network_status),
            timeout=8.0,
        )
        report["robot_network_mode"] = (
            conn.datachannel.rtc_inner_req.network_status.network_status or "unknown"
        )
        report["connection_acceptance"] = (
            report["peer_state"] == "connected"
            and report["ice_state"] in {"completed", "connected"}
            and report["datachannel_state"] == "open"
            and report["robot_network_mode"] == "4G"
        )
        print(
            f"已连接: {report['connect_s']} s, "
            f"网络模式={report['robot_network_mode']}, "
            f"acceptance={report['connection_acceptance']}"
        )

        print("\n[3/5] 订阅 lowstate / odom / lidar_state / 压缩点云……")
        topic_times: dict[str, list[float]] = {
            "lowstate": [],
            "odom": [],
            "sport_state": [],
            "lidar_state": [],
            "lidar": [],
        }
        lidar_point_counts: list[int] = []
        lidar_state_cloud_size: list[int] = []

        def simple(name: str) -> Callable[[dict[str, Any]], None]:
            def callback(_message: dict[str, Any]) -> None:
                topic_times[name].append(time.monotonic())

            return callback

        def lidar_cb(message: dict[str, Any]) -> None:
            topic_times["lidar"].append(time.monotonic())
            try:
                lidar_point_counts.append(len(message["data"]["data"]["points"]))
            except (KeyError, TypeError):
                lidar_point_counts.append(0)

        def lidar_state_cb(message: dict[str, Any]) -> None:
            topic_times["lidar_state"].append(time.monotonic())
            try:
                lidar_state_cloud_size.append(int(message["data"]["cloud_size"]))
            except (KeyError, TypeError, ValueError):
                pass

        conn.datachannel.set_decoder(decoder_type="native")
        await conn.datachannel.disableTrafficSaving(True)
        conn.datachannel.pub_sub.publish_without_callback(RTC_TOPIC["ULIDAR_SWITCH"], "on")

        for topic, callback in (
            (RTC_TOPIC["LOW_STATE"], simple("lowstate")),
            (RTC_TOPIC["ROBOTODOM"], simple("odom")),
            (RTC_TOPIC["SPORT_MOD_STATE"], simple("sport_state")),
            (RTC_TOPIC["ULIDAR_STATE"], lidar_state_cb),
            (RTC_TOPIC["ULIDAR_ARRAY"], lidar_cb),
        ):
            conn.datachannel.pub_sub.subscribe(topic, callback)
            subscribed.append(topic)

        state_ready = await wait_until(
            lambda: len(topic_times["lowstate"]) >= 2 and len(topic_times["odom"]) >= 2,
            timeout=20.0,
        )
        if not state_ready:
            raise RuntimeError("20 秒内未同时收到 lowstate 和 odom")
        print("状态与里程计安全门已通过.")

        if not args.no_motion:
            print("\n[4/5] 准备 StandUp → BalanceStand → StandDown.")
            confirmation = (
                await asyncio.to_thread(input, "确认安全后输入 YES (其他输入跳过动作): ")
            ).strip()
            if confirmation == "YES":
                motion_started = True
                stand_up = await request(
                    conn, RTC_TOPIC["SPORT_MOD"], {"api_id": SPORT_CMD["StandUp"]}
                )
                report["control"]["stand_up_code"] = response_code(stand_up)
                await asyncio.sleep(5)
                balance = await request(
                    conn, RTC_TOPIC["SPORT_MOD"], {"api_id": SPORT_CMD["BalanceStand"]}
                )
                report["control"]["balance_stand_code"] = response_code(balance)
                await asyncio.sleep(3)
                stand_down = await request(
                    conn, RTC_TOPIC["SPORT_MOD"], {"api_id": SPORT_CMD["StandDown"]}
                )
                report["control"]["stand_down_code"] = response_code(stand_down)
                motion_started = False
                await asyncio.sleep(3)
                report["control"]["sequence_sent"] = True
                report["control"]["visual_confirmation"] = (
                    (await asyncio.to_thread(input, "现场是否确认站立/平衡/趴下? 输入 yes/no: "))
                    .strip()
                    .lower()
                )
            else:
                report["control"]["sequence_sent"] = False
                report["control"]["skip_reason"] = "operator did not type YES"
        else:
            report["control"]["sequence_sent"] = False
            report["control"]["skip_reason"] = "--no-motion"

        print(f"\n[5/5] 连续采样 {args.duration:.0f} 秒……")
        for times in topic_times.values():
            times.clear()
        lidar_point_counts.clear()
        lidar_state_cloud_size.clear()
        sample_started = time.monotonic()
        await asyncio.sleep(max(args.duration, 1.0))
        elapsed = time.monotonic() - sample_started
        report["duration_actual_s"] = round(elapsed, 3)
        report["data"] = {
            name: summarize_times(times, elapsed) for name, times in topic_times.items()
        }
        report["data"]["lidar"]["points_mean"] = (
            round(statistics.mean(lidar_point_counts), 1) if lidar_point_counts else 0
        )
        report["data"]["lidar"]["points_max"] = max(lidar_point_counts, default=0)
        report["data"]["lidar_state"]["cloud_size_mean"] = (
            round(statistics.mean(lidar_state_cloud_size), 1) if lidar_state_cloud_size else 0
        )
        report["data"]["lidar_state"]["cloud_size_max"] = max(lidar_state_cloud_size, default=0)

        report["data_acceptance"] = {
            "lowstate": report["data"]["lowstate"]["frames"] > 0,
            "odom": report["data"]["odom"]["frames"] > 0,
            "lidar_state": report["data"]["lidar_state"]["frames"] > 0,
            "lidar_points": report["data"]["lidar"]["frames"] > 0
            and report["data"]["lidar"]["points_max"] > 0,
            "video": "not_in_datachannel_only_offer",
        }
        # 控制+状态主路径: lowstate + odom; lidar_state 证明雷达在转
        report["core_data_path_passed"] = (
            report["data_acceptance"]["lowstate"]
            and report["data_acceptance"]["odom"]
            and report["data_acceptance"]["lidar_state"]
        )
        if args.no_motion:
            report["control_acceptance"] = "not_tested"
            control_passed = True
        else:
            report["control_acceptance"] = (
                report["control"].get("sequence_sent") is True
                and report["control"].get("stand_up_code") == 0
                and report["control"].get("balance_stand_code") == 0
                and report["control"].get("stand_down_code") == 0
                and report["control"].get("visual_confirmation") == "yes"
            )
            control_passed = report["control_acceptance"] is True

        report["overall_passed"] = (
            report["connection_acceptance"] and report["core_data_path_passed"] and control_passed
        )
        print("测试完成, 正在断开……")
        return 0 if report["overall_passed"] else 2, report
    except Exception as exc:
        error_message = f"{type(exc).__name__}: {exc}"
        for secret in (account, password, serial):
            error_message = error_message.replace(secret, "<redacted>")
        report["errors"].append(error_message)
        logging.error("测试失败: %s", error_message)
        return 1, report
    finally:
        if conn is not None and conn.pc is not None:
            channel = getattr(getattr(conn, "datachannel", None), "channel", None)
            open_ok = getattr(channel, "readyState", "") == "open"
            if open_ok and motion_started:
                try:
                    await request(
                        conn,
                        RTC_TOPIC["SPORT_MOD"],
                        {"api_id": SPORT_CMD["StandDown"]},
                        timeout=5.0,
                    )
                    report["control"]["emergency_stand_down_sent"] = True
                except Exception as exc:
                    report["errors"].append(f"final StandDown failed: {exc}")
            if open_ok:
                for topic in subscribed:
                    try:
                        conn.datachannel.pub_sub.unsubscribe(topic)
                    except Exception:
                        pass
                try:
                    conn.datachannel.pub_sub.publish_without_callback(
                        RTC_TOPIC["ULIDAR_SWITCH"], "off"
                    )
                except Exception:
                    pass
            try:
                await conn.pc.close()
            except Exception as exc:
                report["errors"].append(f"disconnect failed: {exc}")
            conn.pc = None
            print_status("WebRTC connection", "disconnected")


def main() -> int:
    args = parse_args()
    if args.duration <= 0:
        print("--duration 必须大于 0", file=sys.stderr)
        return 2
    exit_code, report = asyncio.run(run_test(args))
    report["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S %z")
    output_path = Path.cwd() / f"go2_4g_test_{time.strftime('%Y%m%d_%H%M%S')}.json"
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n========== 测试结果 ==========")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n报告已保存: {output_path}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
