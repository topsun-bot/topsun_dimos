#!/usr/bin/env python3
"""独立 orbit_object 测试：直接通过 LCM 订阅 odom/costmap，发布 cmd_vel。
用法：先在另一个终端运行 dimos run unitree-go2，然后运行本脚本。
"""

import math
import time

from dimos_lcm.std_msgs import Bool
import numpy as np

from dimos.agents.skills.orbit_object import (
    LapTracker,
    estimate_object_center,
    extract_edge,
)
from dimos.core.transport import LCMTransport
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.msgs.nav_msgs.OccupancyGrid import OccupancyGrid


def _angle_diff(a: float, b: float) -> float:
    d = a - b
    return (d + math.pi) % (2 * math.pi) - math.pi


KP_DISTANCE = 0.5
KP_YAW = 0.15
MAX_ANGULAR_Z = 0.15
FACE_THRESHOLD = math.radians(30)
ORBIT_SPEED = 0.3
CONTROL_HZ = 10.0
SEARCH_RADIUS = 3.0
MIN_DISTANCE = 0.2
MAX_DISTANCE_FACTOR = 3.0


latest_odom: PoseStamped | None = None
latest_costmap: OccupancyGrid | None = None


def on_odom(msg: PoseStamped) -> None:
    global latest_odom
    latest_odom = msg


def on_costmap(msg: OccupancyGrid) -> None:
    global latest_costmap
    latest_costmap = msg


def orbit_loop(
    cmd_vel_transport: LCMTransport,
    stop_transport: LCMTransport,
    distance: float = 1.0,
    laps: float = 1.0,
    speed: float = 0.2,
    clockwise: bool = False,
    bearing: float = 0.0,
) -> None:
    print("等待 odom 和 costmap 数据...")
    wait_count = 0
    while latest_odom is None or latest_costmap is None:
        time.sleep(0.5)
        wait_count += 1
        if wait_count % 4 == 0:
            print(
                f"  等待中... odom={'有' if latest_odom else '无'} costmap={'有' if latest_costmap else '无'}"
            )
    print("数据就绪，开始 orbit")

    stop_transport.publish(Bool(data=True))

    odom = latest_odom
    costmap = latest_costmap
    robot_xy = np.array([odom.x, odom.y])
    robot_yaw = odom.yaw

    print(f"机器人位置: ({robot_xy[0]:.2f}, {robot_xy[1]:.2f}), yaw={math.degrees(robot_yaw):.1f}°")

    # 诊断 costmap
    grid = costmap.grid
    ox, oy, res = costmap.origin.position.x, costmap.origin.position.y, costmap.resolution
    ys, xs = np.where(grid >= 50)
    if len(xs) > 0:
        w_xs = ox + xs.astype(np.float64) * res
        w_ys = oy + ys.astype(np.float64) * res
        dists = np.sqrt((w_xs - robot_xy[0]) ** 2 + (w_ys - robot_xy[1]) ** 2)
        angles_deg = np.degrees(np.arctan2(w_ys - robot_xy[1], w_xs - robot_xy[0]) - robot_yaw)
        print(f"Costmap: {grid.shape}, resolution={res}m, 占用格={len(xs)}个")
        print(
            f"  占用范围 X: [{w_xs.min():.2f}, {w_xs.max():.2f}], Y: [{w_ys.min():.2f}, {w_ys.max():.2f}]"
        )
        print(f"  最近占用格: d={dists.min():.2f}m, 最远: d={dists.max():.2f}m")
        # 按距离排列前 5 个最近的占用格
        top5 = np.argsort(dists)[:5]
        print("  最近 5 个占用格:")
        for i in top5:
            a = ((angles_deg[i] + 180) % 360) - 180
            print(f"    ({w_xs[i]:.2f}, {w_ys[i]:.2f}) d={dists[i]:.2f}m 方位={a:.0f}°")
        # 按扇区统计
        front_mask = np.abs(((angles_deg + 180) % 360) - 180) < 45
        print(
            f"  前方±45°内: {np.sum(front_mask)}个占用格, 最近={dists[front_mask].min():.2f}m"
            if np.any(front_mask)
            else "  前方±45°内: 无"
        )
    else:
        print("Costmap: 无占用格！")

    edge = extract_edge(costmap, robot_xy, robot_yaw, SEARCH_RADIUS, bearing_deg=bearing)
    if edge is None:
        print("未找到障碍物，退出")
        stop_transport.publish(Bool(data=False))
        return

    dx = edge.point[0] - robot_xy[0]
    dy = edge.point[1] - robot_xy[1]
    rel_angle = math.degrees(math.atan2(dy, dx) - robot_yaw)
    print(
        f"边缘: ({edge.point[0]:.2f}, {edge.point[1]:.2f}), 距离={edge.distance:.2f}m, "
        f"相对角度={rel_angle:.1f}°, 法线=({edge.normal[0]:.2f}, {edge.normal[1]:.2f})"
    )

    object_center = estimate_object_center(costmap, edge.point, cluster_radius=max(distance, 0.8))
    lock_radius = max(distance * 2.5, 1.0)
    lap_tracker = LapTracker(robot_xy, object_center, laps)

    print(
        f"目标中心: ({object_center[0]:.2f}, {object_center[1]:.2f}), lock_radius={lock_radius:.2f}"
    )
    print(
        f"开始绕行: distance={distance}m, laps={laps}, speed={speed}m/s, "
        f"{'顺时针' if clockwise else '逆时针'}"
    )

    period = 1.0 / CONTROL_HZ
    next_time = time.monotonic()
    log_counter = 0

    try:
        while True:
            next_time += period

            odom = latest_odom
            costmap = latest_costmap
            if odom is None or costmap is None:
                continue

            robot_xy = np.array([odom.x, odom.y])
            robot_yaw = odom.yaw

            edge = extract_edge(costmap, robot_xy, robot_yaw, lock_radius, target_xy=object_center)
            if edge is None:
                print("障碍物丢失，停止")
                break

            d = edge.distance
            if d < MIN_DISTANCE:
                print("太近障碍物，安全停止")
                break
            if d > MAX_DISTANCE_FACTOR * distance:
                print(f"太远 d={d:.2f}m > {MAX_DISTANCE_FACTOR * distance:.2f}m，边缘丢失")
                break

            # 面向物品中心（稳定），距离基于边缘（精确）
            theta = math.atan2(object_center[1] - robot_xy[1], object_center[0] - robot_xy[0])
            face_err_rad = _angle_diff(theta, robot_yaw)
            # 转向：面对物体
            angular_z = KP_YAW * face_err_rad
            angular_z = max(-MAX_ANGULAR_Z, min(MAX_ANGULAR_Z, angular_z))

            # 只有面对物体时才前进和横移，否则原地转身
            if abs(face_err_rad) < FACE_THRESHOLD:
                vx_body = max(-0.3, min(0.3, KP_DISTANCE * (d - distance)))
                vy_body = ORBIT_SPEED if not clockwise else -ORBIT_SPEED
            else:
                vx_body = 0.0
                vy_body = 0.0

            log_counter += 1
            if log_counter % 20 == 0:
                pct = lap_tracker.progress * 100
                face_err = math.degrees(_angle_diff(theta, robot_yaw))
                print(
                    f"[{log_counter / CONTROL_HZ:.1f}s] pos=({robot_xy[0]:.2f},{robot_xy[1]:.2f}) "
                    f"yaw={math.degrees(robot_yaw):.0f}° face_err={face_err:.0f}° d={d:.2f}m "
                    f"vx={vx_body:.2f} vy={vy_body:.2f} az={angular_z:.2f} "
                    f"progress={pct:.0f}%"
                )

            twist = Twist(
                linear=Vector3(vx_body, vy_body, 0.0), angular=Vector3(0.0, 0.0, angular_z)
            )
            cmd_vel_transport.publish(twist)

            if lap_tracker.update(robot_xy):
                pct = lap_tracker.progress * 100
                print(f"完成 {laps} 圈 ({pct:.0f}%)!")
                break

            now = time.monotonic()
            sleep_dur = next_time - now
            if sleep_dur > 0:
                time.sleep(sleep_dur)
    except KeyboardInterrupt:
        print("用户取消")
    finally:
        cmd_vel_transport.publish(Twist.zero())
        stop_transport.publish(Bool(data=False))
        print("orbit 结束")


if __name__ == "__main__":
    odom_transport = LCMTransport("/odom", PoseStamped)
    costmap_transport = LCMTransport("/global_costmap", OccupancyGrid)
    cmd_vel_transport = LCMTransport("/cmd_vel", Twist)
    stop_transport = LCMTransport("/stop_movement", Bool)

    odom_transport.subscribe(on_odom)
    costmap_transport.subscribe(on_costmap)

    print("LCM 订阅已启动，等待数据...")
    orbit_loop(
        cmd_vel_transport,
        stop_transport,
        distance=1.0,
        laps=1,
        speed=0.2,
        clockwise=False,
        bearing=0,
    )
