#!/usr/bin/env bash
# check_network.sh —— 拓展坞上跑，一键 PASS/FAIL 报告。
#
# 检查项：
#   1. wlan0 / go2eth 网卡 UP
#   2. 192.168.123.18（拓展坞 LAN）在本机网卡上
#   3. 192.168.123.20（mid-360）可达
#   4. 192.168.123.161（Go2 运控）可达
#   5. mid-360 UDP 端口 56500 可探测（point data）
#   6. 拓展坞 .venv 已装好（dimos 命令可用）
#   7. 反向 SSH 隧道 7890（如启用）通过笔记本访问 PyPI
#
# 退出码：0 = 全过；非 0 = 失败项数
#
# 用法：
#   bash jiangtao/scripts/check_network.sh
#   bash jiangtao/scripts/check_network.sh --no-proxy   # 跳过代理检查
#
# 详细信息见 jiangtao/plan/plan.md §2 实测环境清点。

set -u

LIDAR_HOST_IP="${LIDAR_HOST_IP:-192.168.123.18}"
LIDAR_IP="${LIDAR_IP:-192.168.123.20}"
ROBOT_IP_DIRECT="${ROBOT_IP_DIRECT:-192.168.123.161}"
PROXY_PORT="${PROXY_PORT:-7890}"

CHECK_PROXY=1
for arg in "$@"; do
    case "$arg" in
        --no-proxy) CHECK_PROXY=0 ;;
    esac
done

PASS=0
FAIL=0

ok() {
    PASS=$((PASS + 1))
    printf '  \033[32mOK\033[0m %s\n' "$1"
}

bad() {
    FAIL=$((FAIL + 1))
    printf '  \033[31mFAIL\033[0m %s\n' "$1"
}

skip() {
    printf '  \033[33mSKIP\033[0m %s\n' "$1"
}

section() {
    printf '\n\033[1m=== %s ===\033[0m\n' "$1"
}

# 1. 网卡 UP
section "1. 网卡状态"
if ip -br addr show go2eth 2>/dev/null | grep -q UP; then
    ok "go2eth UP"
else
    bad "go2eth 没 UP（直连 Go2 + mid-360 的有线网卡）"
fi
if ip -br addr show 2>/dev/null | grep -q "wlan0.*UP"; then
    ok "wlan0 UP（公司 WiFi，控制台连接）"
else
    skip "wlan0 没 UP（如果走有线公网或不需要外网，可忽略）"
fi

# 2. 拓展坞本机有 192.168.123.18
section "2. 本机 IP 是否含 $LIDAR_HOST_IP"
if ip -br addr show 2>/dev/null | grep -q "$LIDAR_HOST_IP"; then
    ok "本机网卡上有 $LIDAR_HOST_IP（FastLio2 _validate_network 必过）"
else
    bad "本机没有 $LIDAR_HOST_IP，FastLio2 启动时 _validate_network 会拒绝；请配 go2eth 网卡 IP 或导出 LIDAR_HOST_IP=<本机正确 IP>"
fi

# 3 / 4. 设备可达
section "3. 设备 ICMP 可达性"
for target in "$LIDAR_IP:mid-360" "$ROBOT_IP_DIRECT:Go2 运控（直连）"; do
    ip="${target%%:*}"
    name="${target##*:}"
    if ping -c 2 -W 2 "$ip" >/dev/null 2>&1; then
        ok "$name ($ip) 可达"
    else
        bad "$name ($ip) ping 失败"
    fi
done

# 5. mid-360 UDP 端口 56500（可探测但不一定 reply ICMP，所以仅看 socket 能否 send）
section "4. mid-360 UDP 端口 56500（point data）"
if command -v nc >/dev/null 2>&1; then
    if timeout 2 nc -u -z -w 1 "$LIDAR_IP" 56500 2>&1 | grep -qi "open\|succeeded"; then
        ok "UDP $LIDAR_IP:56500 可发送"
    else
        # nc 对 UDP 探测不一定准，作为参考
        skip "UDP $LIDAR_IP:56500 探测不确定（mid-360 不主动响应连接，需运行时验证）"
    fi
else
    skip "nc 未安装，跳过 UDP 端口探测"
fi

# 6. dimos venv
section "5. dimos venv 状态"
DIMOS_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
if [ -x "$DIMOS_DIR/.venv/bin/dimos" ]; then
    ok ".venv/bin/dimos 可执行"
    if "$DIMOS_DIR/.venv/bin/dimos" list 2>/dev/null | grep -q unitree-go2-nav-onboard; then
        ok "蓝图 unitree-go2-nav-onboard 已注册"
    else
        bad "蓝图 unitree-go2-nav-onboard 未注册（跑 pytest dimos/robot/test_all_blueprints_generation.py）"
    fi
else
    bad ".venv/bin/dimos 不存在；请先 uv sync"
fi

# 7. 代理（如果启用）
if [ "$CHECK_PROXY" -eq 1 ]; then
    section "6. 反向 SSH 代理（用于装包 / nix build）"
    if curl -sI --connect-timeout 3 -x "http://127.0.0.1:$PROXY_PORT" https://pypi.org 2>&1 | head -1 | grep -q "200"; then
        ok "本地 127.0.0.1:$PROXY_PORT 走 PyPI 通（笔记本侧 ssh -R 已建立）"
    else
        skip "127.0.0.1:$PROXY_PORT 不可用（首次装包时需要笔记本侧跑 ssh -R 7890:127.0.0.1:7890 unitree@<host>）"
    fi
fi

# 总结
echo
printf '\033[1m=== 总结 ===\033[0m\n'
printf '  通过: %d   失败: %d\n' "$PASS" "$FAIL"
if [ "$FAIL" -eq 0 ]; then
    printf '\033[32m全部通过，可以启动 dimos run unitree-go2-nav-onboard\033[0m\n'
    exit 0
else
    printf '\033[31m有 %d 项失败，请按提示修复后再启动\033[0m\n' "$FAIL"
    exit "$FAIL"
fi
