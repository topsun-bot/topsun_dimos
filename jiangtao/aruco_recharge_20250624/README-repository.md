# 官方 ArUco 回充资料的仓库保留范围

本目录保留宇树官方压缩包中的可审查资料：

- `aruco_config.yaml`：相机参数和安装几何初值；
- `aruco_id0.png`：官方 AprilTag 36h11、ID 0 标记；
- `readme-CN.md` / `readme-EN.md`：压缩包自带说明。

`aruco_id0.png` 是不带 quiet zone 的原始码图。2026-08-12 使用 OpenCV
`DICT_APRILTAG_36h11` 验证：裸图因黑边贴图边界而无法检测，四周增加至少 10 px
白边后可稳定识别为 ID 0。实际打印仍按宇树说明在二维码四周保留约 1 cm 白边。

压缩包中的 `aruco_recharge` 是 Linux ARM64 闭源可执行文件，不能在当前 macOS
本机 DimOS 运行，也没有随包提供可再分发许可证，因此不纳入 Git。当前自研功能的
正式入口是 `unitree-go2-auto-recharge` 蓝图；本目录只作为参数和二维码来源留档。
