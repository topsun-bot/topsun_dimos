# PR 描述：Go2 + Mid-360 onboard nav blueprint

> 直接复制下面"## 开始"以下的内容到 GitHub PR 正文。
>
> base 分支：`jtlinux`（按 [plan.md §13 Q2](plan.md#13-待决策事项) 已决——单 PR 直接到 jtlinux）
> head 分支：`feat/go2-nav-onboard`（建议命名）

---

## 开始

## 目的

为 Go2 EDU + Jetson Orin NX 16GB 拓展坞 + Mid-360 平台新增一份 onboard 导航蓝图 `unitree-go2-nav-onboard`，把 Go2 从老的"WebRTC 4D LiDAR + ReplanningAStar"路线切到"FastLio2 + PGO + nav_stack(planner=simple)"，**解决长距离漂移**。

## 范围

- 新增 [`dimos/robot/unitree/go2/config.py`](dimos/robot/unitree/go2/config.py)：Go2 平台 RobotConfig + Mid-360 实测外参 + 临时复用 G1 路径库（带 TODO）
- 新增 [`dimos/robot/unitree/go2/blueprints/navigation/unitree_go2_nav_onboard.py`](dimos/robot/unitree/go2/blueprints/navigation/unitree_go2_nav_onboard.py)：**直接显式装配** 5 大模块（GO2Connection + FastLio2 + create_nav_stack + MovementManager + vis_module），**不**基于 `unitree_go2_basic`
- 自动注册到 [`dimos/robot/all_blueprints.py`](dimos/robot/all_blueprints.py)
- 新增 3 个 smoke tests：
  - [`dimos/robot/unitree/go2/blueprints/navigation/test_unitree_go2_nav_onboard.py`](dimos/robot/unitree/go2/blueprints/navigation/test_unitree_go2_nav_onboard.py)
  - [`dimos/navigation/nav_stack/test_go2_nav_onboard_remappings.py`](dimos/navigation/nav_stack/test_go2_nav_onboard_remappings.py)
- 更新 [`docs/platforms/quadruped/go2/index.md`](docs/platforms/quadruped/go2/index.md)：加新蓝图入口 + onboard 章节
- 配套 runbook、systemd unit、网络检查脚本、日志摘要工具（仓库内 `jiangtao/` 目录，按 .gitignore 不进 PR；如需进 PR 可单独迁移到 `docs/` / `scripts/`）

## 非目标（本 PR 一律不做）

- 不引入 FarPlanner（首期只用 SimplePlanner）
- 不启用 NavRecord record / replay（aarch64 TLS allocation failure 风险未验证，`record=False`）
- 不做地图持久化 / 跨重启复用
- 不做绝对定位源融合（GNSS / AprilTag / RTK）
- 不生成 Go2 专属 local planner 路径库（**临时复用 G1**，已在代码注释和本 PR 风险段双处 warning）
- 不替换 PGO 闭环检测算法（不动 C++）

## 默认行为（merge default）

| 项 | 值 | 来源 |
|---|---|---|
| `planner` | `simple` | review-v2 §实施边界 |
| `record` | `false` | review-v2 / R5 |
| `path_follower.max_speed` | **`0.4` m/s** | review-v2 R15 |
| `local_planner.max_speed` | `0.4` m/s | 与 path_follower 一致 |
| `global_config.n_workers` | `12` | review-v2 |
| `vehicle_height` | `0.4` m | Go2 EDU 实测 |
| 老 `unitree-go2` 蓝图 | **不动一行** | 双蓝图并存，回滚单条命令 |

## 决策记录（已锁死，不允许 PR 中途改）

完整 13 条 Q1-Q13 见 [plan.md §13](jiangtao/plan/plan.md#13-待决策事项)。关键 6 条：

- **Q1**: 基线分支 = `jtlinux` @ `81e9f144`
- **Q2**: 单 PR 直接到 jtlinux（将来回 main 时再拆双 PR）
- **Q4**: NavRecord 禁用
- **Q11**: 蓝图**显式装配**（不基于 unitree_go2_basic）
- **Q12**: `path_follower.max_speed` merge default = `0.4`
- **Q13**: merge gate = 5m 点击 / teleop 抢占 / goal 取消 / 回滚 各成功 1 次

## Mid-360 实测外参

```python
# dimos/robot/unitree/go2/config.py
T_Dog2lidar = [0.1870, 0.0, 0.0803, 0.0, 0.226, 0.0]   # xyz rpy (m, rad)
# x = 0.187 m  前方
# y = 0.0   m  居中
# z = 0.0803 m 上方（IMU 系下；最终 mount.z = base_link 离地高度 0.30 + 0.0803 = 0.3803 m）
# pitch = 0.226 rad ≈ 13°（绕 Y 轴向下俯）
```

## 手工验证（merge gate；7 步全过才能合）

参考 [plan.md §10.4 手工验收脚本](jiangtao/plan/plan.md#104-手工验收脚本) +
[runbook §14 验收清单](jiangtao/runbook/go2-nav-onboard.md#14-merge-gate-验收清单值班工程师每天打勾):

- [ ] 1. **网络**：`bash jiangtao/scripts/check_network.sh` → 7/7 OK
- [ ] 2. **启动**：`dimos run unitree-go2-nav-onboard` → 10 分钟无 worker 死亡
- [ ] 3. **看点云**：rerun-web → mid-360 点云持续刷新 + odometry 平滑
- [ ] 4. **看闭环**：走 ~20 m"出去-回来" → 日志含 `loop closure triggered` + map→odom TF 跳变 < 0.5 m
- [ ] 5. **看导航**：rerun 点击 5 m 外目标 → 机器人朝目标走 + 全规划链路更新
- [ ] 6. **抢占**：手柄 teleop / 发 NaN goal → 自动导航立即让出
- [ ] 7. **回滚**：`dimos stop && dimos run unitree-go2` → 老链路 10 分钟内恢复

## 自动化测试

CI 必过：

```bash
pytest dimos/robot/unitree/go2/blueprints/navigation/test_unitree_go2_nav_onboard.py
pytest dimos/navigation/nav_stack/test_go2_nav_onboard_remappings.py
pytest dimos/robot/test_all_blueprints_generation.py
```

本机 + 拓展坞均**已 9/9 PASS**（开发期）。

## 灰度门槛（合并后第 2 周开始要求）

不进 merge gate，但部署后第 2 周必须达到（详见 [plan.md §1.1.2](jiangtao/plan/plan.md#112-灰度门槛pr-合并后第-2-周开始要求))：

- D-4 直线 5 m × 10 次成功率 ≥ 80%
- D-5 绕障 × 5 次成功率 ≥ 80%
- 30 分钟稳定（CPU < 70% / RAM < 8GB）

## 风险

| # | 风险 | 缓解 |
|---|---|---|
| R3 | Mid-360 外参参考系若错误，SLAM 直接漂飞 | 已用用户实测 + 在 [`config.py`](dimos/robot/unitree/go2/config.py) `_GO2_BASE_LINK_GROUND_HEIGHT = 0.30` 显式声明 base_link 离地高度，**C-3 期还要实测复核** |
| R5 | `NavRecord` 在 aarch64 上有 TLS allocation failure | 默认 `record=False`，二期再独立验证 |
| R6 | Orin NX 16GB 不在仓库 tested 列表 | 已在拓展坞跑通 `dimos --help` + `pytest` 9/9；30 分钟压力测试在灰度阶段 |
| R8 | G1 path 库仅为临时兼容（`vehicle_length/width` 不匹配 Go2） | [`config.py`](dimos/robot/unitree/go2/config.py) 已加 TODO；二期重新生成 Go2 专属路径库 |
| R7 | `TerrainMapExt` 闭环跳变后短时不一致 | 4 秒衰减自动覆盖；二期监听 `pgo_tf` |

## 部署形态

```
笔记本 ──WiFi/SSH──→ 拓展坞 (Jetson Orin NX 16GB, Ubuntu 20.04 aarch64)
                    │
                    ├─USB-Eth (192.168.123.18)── Mid-360 (192.168.123.20)
                    │
                    └─有线直连 ────────────────── Go2 主控板 (192.168.123.161)
```

dimos 整体跑在拓展坞；笔记本只看 rerun-web。详见 [runbook](jiangtao/runbook/go2-nav-onboard.md)。

## reviewer

按 [plan.md §11.4 reviewer 视角](jiangtao/plan/plan.md#114-reviewer-视角review-v2-推荐):

- **导航模块**: @???（看蓝图装配 / planner=simple / remap）
- **平台 / 机器人**: @???（看 RobotConfig / 外参 / 路径库引用 / 速度限制）
- **基础设施**: @???（看分支基线 / 命令注册 / docs / runbook / 回滚）

## 关联文档

- 计划与决策：[`jiangtao/plan/plan.md`](jiangtao/plan/plan.md)（1054 行 + 14 章 + 6 附录）
- 技术原理：[`jiangtao/plan/dimos-go2-mid360-nav-stack.md`](jiangtao/plan/dimos-go2-mid360-nav-stack.md)（1571 行）
- 现场 runbook：[`jiangtao/runbook/go2-nav-onboard.md`](jiangtao/runbook/go2-nav-onboard.md)
- 评估意见 v1：[`jiangtao/doc/Go2 与 Mid-360 新导航栈规划评估与代码编写计划.docx`](jiangtao/doc/)
- 评估意见 v2：[`jiangtao/plan/Go2与Mid-360新导航栈最终开发说明.md`](Go2%E4%B8%8EMid-360%E6%96%B0%E5%AF%BC%E8%88%AA%E6%A0%88%E6%9C%80%E7%BB%88%E5%BC%80%E5%8F%91%E8%AF%B4%E6%98%8E.md)

> 本 PR 描述基于 dimos `jtlinux` @ `81e9f144`（2026-05-21）。
