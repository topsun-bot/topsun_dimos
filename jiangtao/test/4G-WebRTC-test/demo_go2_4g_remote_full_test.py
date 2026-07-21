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

# ruff: noqa: RUF001
"""Go2 4G Remote WebRTC end-to-end acceptance test.

Credentials are requested interactively and are never written to disk. The
test subscribes to state, odometry, LiDAR, and video before optionally issuing
the low-risk StandUp -> BalanceStand -> StandDown command sequence.
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

from aiortc.mediastreams import MediaStreamError
from unitree_webrtc_connect.constants import RTC_TOPIC, SPORT_CMD
from unitree_webrtc_connect.unitree_cloud import UnitreeCloud
from unitree_webrtc_connect.webrtc_driver import (
    UnitreeWebRTCConnection,
    WebRTCConnectionMethod,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Test Go2 control, state, point cloud, and video over 4G Remote WebRTC."
    )
    parser.add_argument(
        "--region",
        choices=("cn", "global"),
        default="cn",
        help="Unitree account region (default: cn)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=60.0,
        help="Data stability sampling time in seconds (default: 60)",
    )
    parser.add_argument(
        "--no-motion",
        action="store_true",
        help="Only test the connection and data; do not send physical commands",
    )
    return parser.parse_args()


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = round((len(ordered) - 1) * fraction)
    return ordered[index]


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
    conn: UnitreeWebRTCConnection,
    topic: str,
    options: dict[str, Any],
    timeout: float = 10.0,
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


async def run_test(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    print("\n账号、密码和 SN 只保存在本进程内，不会写入报告。")
    account = input("Unitree 账号（中国账号可输入手机号）: ").strip()
    password = getpass.getpass("Unitree 密码（输入时不可见）: ")
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
    subscribed_topics: list[str] = []
    lidar_enabled = False
    video_enabled = False
    motion_started = False

    try:
        report["turn_dns"] = sorted(
            {item[4][0] for item in socket.getaddrinfo("turn.unitree.com", 5349)}
        )
        if any(ip.startswith("198.18.") or ip.startswith("198.19.") for ip in report["turn_dns"]):
            print("警告：TURN 域名解析到了 198.18/15，通常表示 Clash/TUN fake-ip。")

        print("\n[1/6] 登录 Unitree 云并检查设备绑定……")
        login_started = time.monotonic()
        cloud = UnitreeCloud(region=args.region, device_type="Go2")
        token = cloud.login_email(account, password)
        report["cloud_login"] = bool(token)
        report["cloud_login_s"] = round(time.monotonic() - login_started, 3)
        devices = cloud.list_devices()
        report["serial_bound"] = any(device.sn == serial for device in devices)
        if not report["serial_bound"]:
            raise RuntimeError(f"这个 SN 未绑定到当前 {args.region} 区域账号")
        print(f"云登录成功，目标设备已绑定（账号共绑定 {len(devices)} 台设备）。")

        print("\n[2/6] 建立 Remote WebRTC（云信令 + TURN）……")
        conn = UnitreeWebRTCConnection(
            WebRTCConnectionMethod.Remote,
            serialNumber=serial,
            region=args.region,
            device_type="Go2",
        )
        conn.token = token
        connected_started = time.monotonic()
        await conn.connect()
        report["connect_s"] = round(time.monotonic() - connected_started, 3)
        report["connected"] = True
        report["peer_state"] = conn.pc.connectionState
        report["ice_state"] = conn.pc.iceConnectionState
        report["datachannel_state"] = conn.datachannel.channel.readyState
        await wait_until(
            lambda: bool(conn.datachannel.rtc_inner_req.network_status.network_status),
            timeout=5.0,
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
            f"WebRTC 已连接：{report['connect_s']} s，"
            f"机器人网络模式={report['robot_network_mode']}。"
        )

        print("\n[3/6] 开启并订阅 lowstate、odom、点云和视频……")
        topic_times: dict[str, list[float]] = {
            "lowstate": [],
            "odom": [],
            "sport_state": [],
            "lidar": [],
            "video": [],
        }
        lidar_point_counts: list[int] = []
        video_resolutions: set[str] = set()

        def simple_callback(name: str) -> Callable[[dict[str, Any]], None]:
            def callback(_message: dict[str, Any]) -> None:
                topic_times[name].append(time.monotonic())

            return callback

        def lidar_callback(message: dict[str, Any]) -> None:
            topic_times["lidar"].append(time.monotonic())
            try:
                points = message["data"]["data"]["points"]
                lidar_point_counts.append(len(points))
            except (KeyError, TypeError):
                lidar_point_counts.append(0)

        async def video_callback(track: Any) -> None:
            try:
                while True:
                    frame = await track.recv()
                    topic_times["video"].append(time.monotonic())
                    video_resolutions.add(f"{frame.width}x{frame.height}")
            except MediaStreamError:
                return

        conn.datachannel.set_decoder(decoder_type="native")
        await conn.datachannel.disableTrafficSaving(True)
        conn.datachannel.pub_sub.publish_without_callback(RTC_TOPIC["ULIDAR_SWITCH"], "on")
        lidar_enabled = True

        subscriptions = (
            (RTC_TOPIC["LOW_STATE"], simple_callback("lowstate")),
            (RTC_TOPIC["ROBOTODOM"], simple_callback("odom")),
            (RTC_TOPIC["SPORT_MOD_STATE"], simple_callback("sport_state")),
            (RTC_TOPIC["ULIDAR_ARRAY"], lidar_callback),
        )
        for topic, callback in subscriptions:
            conn.datachannel.pub_sub.subscribe(topic, callback)
            subscribed_topics.append(topic)

        conn.video.add_track_callback(video_callback)
        conn.video.switchVideoChannel(True)
        video_enabled = True

        state_ready = await wait_until(
            lambda: len(topic_times["lowstate"]) >= 2 and len(topic_times["odom"]) >= 2,
            timeout=20.0,
        )
        if not state_ready:
            raise RuntimeError("20 秒内未同时收到 lowstate 和 odom；为安全起见不执行动作")

        print("状态与里程计安全门已通过。")

        if not args.no_motion:
            print("\n[4/6] 准备执行 StandUp → BalanceStand → StandDown。")
            print("请保证 Go2 四周无人员、宠物、台阶和障碍物，并有人现场看护。")
            confirmation = (
                await asyncio.to_thread(input, "确认安全后输入 YES（其他输入将跳过动作）: ")
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
                await asyncio.sleep(5)
                report["control"]["sequence_sent"] = True
                report["control"]["visual_confirmation"] = (
                    (
                        await asyncio.to_thread(
                            input, "现场是否确认已完成站立、平衡站立、趴下？输入 yes/no: "
                        )
                    )
                    .strip()
                    .lower()
                )
            else:
                report["control"]["sequence_sent"] = False
                report["control"]["skip_reason"] = "operator did not type YES"
        else:
            report["control"]["sequence_sent"] = False
            report["control"]["skip_reason"] = "--no-motion"

        print(f"\n[5/6] 连续采样 {args.duration:.0f} 秒；期间不要启动手机 App……")
        for times in topic_times.values():
            times.clear()
        lidar_point_counts.clear()
        video_resolutions.clear()
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
        report["data"]["video"]["resolutions"] = sorted(video_resolutions)

        report["data_acceptance"] = {
            "lowstate": report["data"]["lowstate"]["frames"] > 0,
            "odom": report["data"]["odom"]["frames"] > 0,
            "lidar": report["data"]["lidar"]["frames"] > 0
            and report["data"]["lidar"]["points_max"] > 0,
            "video": report["data"]["video"]["frames"] > 0,
        }
        report["full_data_path_passed"] = all(report["data_acceptance"].values())
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
            report["connection_acceptance"] and report["full_data_path_passed"] and control_passed
        )
        print("\n[6/6] 测试完成，正在安全关闭数据和连接……")
        return 0 if report["overall_passed"] else 2, report
    except Exception as exc:
        error_message = f"{type(exc).__name__}: {exc}"
        for secret in (account, password, serial):
            error_message = error_message.replace(secret, "<redacted>")
        report["errors"].append(error_message)
        logging.error("测试失败: %s", error_message)
        return 1, report
    finally:
        if conn is not None:
            channel_open = getattr(getattr(conn, "datachannel", None), "channel", None)
            channel_open = getattr(channel_open, "readyState", "") == "open"
            if channel_open and motion_started:
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
            if channel_open:
                if video_enabled:
                    conn.video.switchVideoChannel(False)
                for topic in subscribed_topics:
                    conn.datachannel.pub_sub.unsubscribe(topic)
                if lidar_enabled:
                    conn.datachannel.pub_sub.publish_without_callback(
                        RTC_TOPIC["ULIDAR_SWITCH"], "off"
                    )
            try:
                await conn.disconnect()
            except Exception as exc:
                report["errors"].append(f"disconnect failed: {exc}")


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
    print(f"\n报告已保存：{output_path}")
    print("报告不含账号、密码、SN 或 AES Key。")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
