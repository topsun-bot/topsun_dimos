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

"""Go2 4G Remote: 站立动作 + 视频 + 状态验收 (共享 ICE 补丁).

关键修复
--------
aiortc/aioice 默认给 audio/video/datachannel 三条 m-line 生成不同的
ice-ufrag. 经 Unitree TURN 时 DTLS 经常失败. 本脚本在 import aiortc
之前把 aioice.Connection.__init__ 打成共享 ufrag/pwd (思路来自 z4rtc).

本机实测结果 (Clash 可保持运行)
------------------------------
- 建连 ~1.2s, robot_network_mode=4G
- StandUp / BalanceStand / StandDown 返回码均为 0
- 视频 ~14 Hz, 640x360
- lowstate ~20 Hz, odom ~18.8 Hz
- 点云需 disableTrafficSaving + ULIDAR_SWITCH=on; 后续探针约 8.7 Hz /
  2 万点, DimOS unitree-go2 4G 端到端已通. 详见测试指南.

用法
----
  python jiangtao/test/4G-WebRTC-test/demo_go2_4g_remote_nav_acceptance.py --duration 40
  python jiangtao/test/4G-WebRTC-test/demo_go2_4g_remote_nav_acceptance.py --duration 40 --no-motion
"""

from __future__ import annotations

# ---- 必须在 import aiortc / unitree_webrtc_connect 之前 ----
import aioice
import aioice.utils

_SHARED_UFRAG = aioice.utils.random_string(4)
_SHARED_PWD = aioice.utils.random_string(22)
_ORIG_ICE_INIT = aioice.Connection.__init__


def _shared_ice_init(self, *args, **kwargs):
    kwargs["local_username"] = _SHARED_UFRAG
    kwargs["local_password"] = _SHARED_PWD
    return _ORIG_ICE_INIT(self, *args, **kwargs)


aioice.Connection.__init__ = _shared_ice_init
# -----------------------------------------------------------

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
import re
import socket
import statistics
import sys
import time
from typing import Any

from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamError
from unitree_webrtc_connect.constants import RTC_TOPIC, SPORT_CMD
from unitree_webrtc_connect.unitree_auth import send_sdp_to_remote_peer
from unitree_webrtc_connect.unitree_cloud import UnitreeCloud
from unitree_webrtc_connect.util import fetch_public_key, fetch_turn_server_info, print_status
from unitree_webrtc_connect.webrtc_audio import WebRTCAudioChannel
from unitree_webrtc_connect.webrtc_datachannel import WebRTCDataChannel
from unitree_webrtc_connect.webrtc_driver import UnitreeWebRTCConnection, WebRTCConnectionMethod
from unitree_webrtc_connect.webrtc_video import WebRTCVideoChannel


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Go2 4G nav acceptance: motion + video + state")
    p.add_argument("--region", choices=("cn", "global"), default="cn")
    p.add_argument("--duration", type=float, default=40.0)
    p.add_argument("--no-motion", action="store_true")
    return p.parse_args()


def summarize_times(times: list[float], elapsed: float) -> dict[str, Any]:
    gaps = [(b - a) * 1000 for a, b in pairwise(times)]
    return {
        "frames": len(times),
        "rate_hz": round(len(times) / elapsed, 2) if elapsed else 0.0,
        "gap_p50_ms": round(statistics.median(gaps), 1) if gaps else None,
        "gap_max_ms": round(max(gaps), 1) if gaps else None,
        "gaps_over_1s": sum(g > 1000 for g in gaps),
    }


def response_code(response: Any) -> int | None:
    try:
        return response["data"]["header"]["status"]["code"]
    except (KeyError, TypeError):
        return None


async def wait_until(pred: Callable[[], bool], timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        await asyncio.sleep(0.2)
    return pred()


async def connect_full_av(
    serial: str, token: str, region: str
) -> tuple[UnitreeWebRTCConnection, list[Any]]:
    public_key = fetch_public_key(region=region, device_type="Go2")
    turn = fetch_turn_server_info(serial, token, public_key, region=region, device_type="Go2")
    cfg = RTCConfiguration(
        iceServers=[
            RTCIceServer(urls=[turn["realm"]], username=turn["user"], credential=turn["passwd"])
        ]
    )
    pc = RTCPeerConnection(cfg)
    conn = UnitreeWebRTCConnection(
        WebRTCConnectionMethod.Remote, serialNumber=serial, region=region, device_type="Go2"
    )
    conn.token = token
    conn.public_key = public_key
    conn.pc = pc
    conn.datachannel = WebRTCDataChannel(conn, pc)
    conn.audio = WebRTCAudioChannel(pc, conn.datachannel)
    conn.video = WebRTCVideoChannel(pc, conn.datachannel)

    pending: list[Any] = []

    @pc.on("track")
    def on_track(track: Any) -> None:
        pending.append(track)
        print(f"  queued track {track.kind}")

    print_status("WebRTC", "started (shared-ICE full A/V)")
    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    for _ in range(100):
        if pc.iceGatheringState == "complete":
            break
        await asyncio.sleep(0.05)

    sdp = pc.localDescription.sdp
    ufrags = set(re.findall(r"a=ice-ufrag:(\S+)", sdp))
    print(f"  shared ICE ufrags={ufrags}")
    if len(ufrags) != 1:
        raise RuntimeError(f"shared ICE patch failed: {ufrags}")

    payload = {
        "id": "",
        "turnserver": turn,
        "sdp": sdp,
        "type": pc.localDescription.type,
        "token": token,
    }
    answer_json = send_sdp_to_remote_peer(
        serial, json.dumps(payload), token, public_key, region=region, device_type="Go2"
    )
    peer_answer = json.loads(answer_json)
    if peer_answer.get("sdp") == "reject":
        raise RuntimeError("RobotBusyError")
    await pc.setRemoteDescription(
        RTCSessionDescription(sdp=peer_answer["sdp"], type=peer_answer["type"])
    )

    deadline = time.monotonic() + 25.0
    while time.monotonic() < deadline:
        if conn.datachannel.data_channel_opened and pc.connectionState == "connected":
            print_status("WebRTC", f"connected ({pc.connectionState}/{pc.iceConnectionState})")
            return conn, pending
        await asyncio.sleep(0.2)
    raise TimeoutError(
        f"timeout peer={pc.connectionState} ice={pc.iceConnectionState} "
        f"ch={conn.datachannel.channel.readyState}"
    )


async def run_test(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    print("\n账号/密码/SN 只在本进程内, 不写报告.")
    print(f"[patch] shared ICE ufrag={_SHARED_UFRAG}")
    account = input("Unitree 账号: ").strip()
    password = getpass.getpass("Unitree 密码: ")
    serial = input("Go2 SN: ").strip()
    if not account or not password or not serial:
        raise ValueError("账号、密码、SN 不能为空")

    report: dict[str, Any] = {
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "unitree_webrtc_connect": version("unitree-webrtc-connect"),
        "aiortc": version("aiortc"),
        "patch": "shared aioice ICE credentials across m-lines",
        "clash_compatible": True,
        "region": args.region,
        "duration_requested_s": args.duration,
        "turn_dns": sorted({i[4][0] for i in socket.getaddrinfo("turn.unitree.com", 5349)}),
        "control": {},
        "data": {},
        "errors": [],
        "note_lidar": (
            "Go2 built-in voxel_map(_compressed) is not published over 4G Remote "
            "DataChannel (0 binary frames observed). Use Mid360 or WiFi LocalSTA "
            "for navigation point clouds. lidar_state still proves radar is spinning."
        ),
    }
    conn: UnitreeWebRTCConnection | None = None
    motion_started = False
    subscribed: list[str] = []

    try:
        print("\n[1/5] 云登录……")
        cloud = UnitreeCloud(region=args.region, device_type="Go2")
        token = cloud.login_email(account, password)
        report["cloud_login"] = bool(token)
        report["serial_bound"] = any(d.sn == serial for d in cloud.list_devices())
        if not report["serial_bound"]:
            raise RuntimeError("SN 未绑定到当前账号")

        print("\n[2/5] 共享 ICE 完整 A/V 建连……")
        t0 = time.monotonic()
        conn, pending = await connect_full_av(serial, token, args.region)
        report["connect_s"] = round(time.monotonic() - t0, 3)
        report["connected"] = True
        report["peer_state"] = conn.pc.connectionState
        report["ice_state"] = conn.pc.iceConnectionState
        report["datachannel_state"] = conn.datachannel.channel.readyState

        video_times: list[float] = []
        resolutions: set[str] = set()

        async def eat_video(track: Any) -> None:
            try:
                while True:
                    frame = await track.recv()
                    video_times.append(time.monotonic())
                    resolutions.add(f"{frame.width}x{frame.height}")
            except MediaStreamError:
                return

        async def eat_audio(track: Any) -> None:
            try:
                while True:
                    await track.recv()
            except MediaStreamError:
                return

        bg_tasks: list[asyncio.Task[Any]] = []
        for track in pending:
            if track.kind == "video":
                bg_tasks.append(asyncio.create_task(eat_video(track)))
            elif track.kind == "audio":
                bg_tasks.append(asyncio.create_task(eat_audio(track)))

        print("\n[3/5] 开视频 + 订阅状态/点云……")
        conn.video.switchVideoChannel(True)
        topic_times: dict[str, list[float]] = {
            "lowstate": [],
            "odom": [],
            "lidar": [],
            "lidar_state": [],
        }
        lidar_pts: list[int] = []

        def simple(name: str) -> Callable[[dict[str, Any]], None]:
            def cb(_m: dict[str, Any]) -> None:
                topic_times[name].append(time.monotonic())

            return cb

        def lidar_cb(message: dict[str, Any]) -> None:
            topic_times["lidar"].append(time.monotonic())
            try:
                lidar_pts.append(len(message["data"]["data"]["points"]))
            except (KeyError, TypeError):
                lidar_pts.append(0)

        conn.datachannel.set_decoder("native")
        await conn.datachannel.disableTrafficSaving(True)
        conn.datachannel.pub_sub.publish_without_callback(RTC_TOPIC["ULIDAR_SWITCH"], "on")
        for topic, cb in (
            (RTC_TOPIC["LOW_STATE"], simple("lowstate")),
            (RTC_TOPIC["ROBOTODOM"], simple("odom")),
            (RTC_TOPIC["ULIDAR_ARRAY"], lidar_cb),
            (RTC_TOPIC["ULIDAR_STATE"], simple("lidar_state")),
        ):
            conn.datachannel.pub_sub.subscribe(topic, cb)
            subscribed.append(topic)

        if not await wait_until(
            lambda: len(topic_times["lowstate"]) >= 2 and len(topic_times["odom"]) >= 2,
            20.0,
        ):
            raise RuntimeError("状态安全门失败")
        print("状态门通过")

        await wait_until(
            lambda: bool(conn.datachannel.rtc_inner_req.network_status.network_status),
            8.0,
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

        if not args.no_motion:
            print("\n[4/5] StandUp → BalanceStand → StandDown")
            print("请保证四周空旷并有人看护.")
            conf = (await asyncio.to_thread(input, "确认安全后输入 YES: ")).strip()
            if conf == "YES":
                motion_started = True

                async def req(api: int) -> Any:
                    return await asyncio.wait_for(
                        conn.datachannel.pub_sub.publish_request_new(
                            RTC_TOPIC["SPORT_MOD"], {"api_id": api}
                        ),
                        timeout=10,
                    )

                r1 = await req(SPORT_CMD["StandUp"])
                report["control"]["stand_up_code"] = response_code(r1)
                print("  StandUp", report["control"]["stand_up_code"])
                await asyncio.sleep(5)
                r2 = await req(SPORT_CMD["BalanceStand"])
                report["control"]["balance_stand_code"] = response_code(r2)
                print("  BalanceStand", report["control"]["balance_stand_code"])
                await asyncio.sleep(3)
                r3 = await req(SPORT_CMD["StandDown"])
                report["control"]["stand_down_code"] = response_code(r3)
                print("  StandDown", report["control"]["stand_down_code"])
                motion_started = False
                report["control"]["sequence_sent"] = True
                report["control"]["visual_confirmation"] = (
                    (await asyncio.to_thread(input, "现场是否看到站立/平衡/趴下? yes/no: "))
                    .strip()
                    .lower()
                )
            else:
                report["control"]["sequence_sent"] = False
                report["control"]["skip_reason"] = "not YES"
        else:
            report["control"]["sequence_sent"] = False
            report["control"]["skip_reason"] = "--no-motion"

        print(f"\n[5/5] 采样 {args.duration:.0f}s……")
        for t in topic_times.values():
            t.clear()
        lidar_pts.clear()
        video_times.clear()
        resolutions.clear()
        t1 = time.monotonic()
        await asyncio.sleep(max(args.duration, 1.0))
        elapsed = time.monotonic() - t1
        report["duration_actual_s"] = round(elapsed, 3)
        report["data"] = {k: summarize_times(v, elapsed) for k, v in topic_times.items()}
        report["data"]["lidar"]["points_max"] = max(lidar_pts, default=0)
        report["data"]["lidar"]["points_mean"] = (
            round(statistics.mean(lidar_pts), 1) if lidar_pts else 0
        )
        report["data"]["video"] = summarize_times(video_times, elapsed)
        report["data"]["video"]["resolutions"] = sorted(resolutions)

        report["data_acceptance"] = {
            "lowstate": report["data"]["lowstate"]["frames"] > 0,
            "odom": report["data"]["odom"]["frames"] > 0,
            "video": report["data"]["video"]["frames"] > 0,
            "lidar_state": report["data"]["lidar_state"]["frames"] > 0,
            "lidar_points": report["data"]["lidar"]["frames"] > 0
            and report["data"]["lidar"]["points_max"] > 0,
        }
        if args.no_motion:
            report["control_acceptance"] = "not_tested"
            control_ok = True
        else:
            report["control_acceptance"] = (
                report["control"].get("sequence_sent") is True
                and report["control"].get("stand_up_code") == 0
                and report["control"].get("balance_stand_code") == 0
                and report["control"].get("stand_down_code") == 0
                and report["control"].get("visual_confirmation") == "yes"
            )
            control_ok = report["control_acceptance"] is True

        # 导航就绪: 视频必须有; 内置点云在 4G 上预期失败, 单独标记
        report["video_ok"] = report["data_acceptance"]["video"] is True
        report["builtin_lidar_ok"] = report["data_acceptance"]["lidar_points"] is True
        report["motion_ok"] = control_ok
        report["overall_passed"] = (
            report["connection_acceptance"]
            and report["data_acceptance"]["lowstate"]
            and report["data_acceptance"]["odom"]
            and report["video_ok"]
            and control_ok
        )
        return 0 if report["overall_passed"] else 2, report
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        for s in (account, password, serial):
            msg = msg.replace(s, "<redacted>")
        report["errors"].append(msg)
        logging.error("%s", msg)
        return 1, report
    finally:
        if conn is not None and conn.pc is not None:
            ch = getattr(getattr(conn, "datachannel", None), "channel", None)
            open_ok = getattr(ch, "readyState", "") == "open"
            if open_ok and motion_started:
                try:
                    await asyncio.wait_for(
                        conn.datachannel.pub_sub.publish_request_new(
                            RTC_TOPIC["SPORT_MOD"], {"api_id": SPORT_CMD["StandDown"]}
                        ),
                        timeout=5,
                    )
                except Exception as exc:
                    report["errors"].append(f"emergency StandDown: {exc}")
            if open_ok:
                try:
                    conn.video.switchVideoChannel(False)
                except Exception:
                    pass
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
                report["errors"].append(f"disconnect: {exc}")
            conn.pc = None


def main() -> int:
    args = parse_args()
    if args.duration <= 0:
        print("--duration 必须 > 0", file=sys.stderr)
        return 2
    code, report = asyncio.run(run_test(args))
    report["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S %z")
    out = Path.cwd() / f"go2_4g_nav_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n========== 结果 ==========")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n已保存: {out}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
