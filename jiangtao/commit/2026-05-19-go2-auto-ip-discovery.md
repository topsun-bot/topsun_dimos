# 2026-05-19 — Go2 自动发现并校验 ROBOT_IP

## 背景

Go2 在 LAN 上的 IP 由路由器 DHCP 分配，**经常发生漂移**（重启路由 / 续约失败 /
切换网络）。原先的 `dimos run unitree-go2` 行为是：

- 直接读 `ROBOT_IP` 环境变量
- 不做任何校验就拿去建 WebRTC 连接
- IP 漂了就报 ICE timeout / connection refused，体验非常差

需求是：开机即用，IP 漂移自动识别，多狗时让用户按 serial 选。

## 改动一览

| 文件 | 修改内容 |
|------|----------|
| `dimos/robot/unitree/go2/cli/landiscovery.py` | `_candidate_ifaces()` 黑名单加 `utun` 前缀和隧道地址段（`100.64.0.0/10`、`198.18.0.0/15`），避免代理 / VPN 接口把 multicast 探测吞掉 |
| `dimos/robot/unitree/go2/connection.py` | 新增 `_resolve_robot_ip(hint, total_timeout=20s, settle_after_first=3s)` 助手；`make_connection()` 的 `webrtc` 分支**永远**调它（哪怕 `ROBOT_IP` 已设也会校验） |
| `dimos/robot/cli/dimos.py` | `run()` 命令里加 **pre-flight 块**：检测到 blueprint 含 `GO2Connection`、且非 sim/replay 时，在**主进程**里先解析 IP，再注入到 `global_config.robot_ip` |

## 解析规则（核心交互）

```
启动 → 主进程发 LAN multicast 探测（最多 20s，发现首台后再等 3s 收尾）
      ├── 发现 0 台                          → RuntimeError + 排障提示
      ├── ROBOT_IP 命中扫描结果              → 用它（"valid, online"）
      ├── ROBOT_IP 没命中 且 只发现 1 台      → 用那唯一一台（打 stale 警告）
      └── ROBOT_IP 没命中 且 发现 ≥ 2 台      → 列出 (#, SERIAL, IP, IFACE) 让用户按编号选
```

> 用户认 dog 是按 **SERIAL** 不是 IP，所以打印必须带 serial。

## 为什么解析放在主进程（不是 worker）

`ModuleCoordinator` 用 **forkserver** 启动 worker 进程，子进程没有 TTY
（stdin 被 detach），`typer.prompt()` 在 worker 里会直接抛
`Multiple Go2 robots found ... but stdin is not a TTY`。

因此在 `cli/dimos.py:run()` 里**先**调一次 `_resolve_robot_ip()`，把结果写回
`global_config.robot_ip` 和 `cli_config_overrides["robot_ip"]`；worker 起来后
拿到的就是已校验的 IP，`make_connection()` 里的 `_resolve_robot_ip` 也会命中
hint 直接返回，等于零额外开销。

## 用例

```bash
unset ROBOT_IP
dimos run unitree-go2
# → "ROBOT_IP not set — scanning LAN ... +saw serial=B42D2000xxxxxxxx ..."
# → 单台: "Found 1 Go2 ... Using it."
# → 多台: 打表 + prompt 选择

ROBOT_IP=10.10.196.189 dimos run unitree-go2
# 在 LAN 上 → "ROBOT_IP=10.10.196.189 is online, using it."
# 不在 LAN 上但还有别的狗 → stale 警告 + 列表选

ROBOT_IP=192.168.1.99 dimos run unitree-go2
# 192.168.1.99 已下线 → "Warning: ... stale" → 列出在线设备让用户选
```

## landiscovery 接口黑名单的踩坑

发现公司开发机 / 笔记本经常有这些"伪以太网"接口：

| 接口 | 来源 | 现象 |
|------|------|------|
| `utun*` | macOS / Surge / Shadowsocks-Linux | 抢 multicast 端口，丢回包 |
| `100.64.x.x` | Tailscale / CGNAT | iface 名字像 `tailscale0`，已经在 prefix 里；但有的 docker bridge 也借这个段 |
| `198.18.x.x` | Clash / Surge 默认 fake-ip | 接口名 `Meta`、`utun` 或随机字符串 |

修复后 multicast 探测在主网卡上独占发送，回包 100% 命中，原来"有时找不到狗"的偶发问题消失。

## 验证

- `python3 -m py_compile` 三文件全通过
- 主进程 prompt 路径靠 `sys.stdin.isatty()` 守门，CI 里也安全
- mujoco / replay 路径**不触发** discover（`make_connection` 早期返回）

## 文件清单

```
dimos/robot/unitree/go2/cli/landiscovery.py          # _candidate_ifaces() 黑名单
dimos/robot/unitree/go2/connection.py                # +_resolve_robot_ip / make_connection
dimos/robot/cli/dimos.py                             # +pre-flight in run()
```
