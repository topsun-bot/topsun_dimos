# 中断检查点

这个工具解决一个具体问题：任务被会议、消息或故障打断后，大脑记得“做过什么”，却想不起下一步为什么要做。

离开任务前用 30 秒保存五个字段：目标、最后完成、下一动作、阻碍和验收证据。恢复时不加载整篇笔记，只读取这些线索。

```bash
uv run python tools/interruption_checkpoint/checkpoint.py checkpoint \
  task.json \
  --task-id robot-debug \
  --goal "定位导航漂移原因" \
  --last-done "确认里程计时间戳连续" \
  --next-action "对比第一处轨迹发散前2秒的cmd_vel" \
  --blocker "缺少一次真值回放" \
  --evidence "odom检查日志"
```

恢复任务：

```bash
uv run python tools/interruption_checkpoint/checkpoint.py resume task.json
```

完成任务：

```bash
uv run python tools/interruption_checkpoint/checkpoint.py complete \
  task.json \
  --evidence "回放测试通过"
```

每次更新使用临时文件、`fsync` 和原子替换，尽量避免写入过程中再次中断导致状态文件损坏。旧状态保留在 `history`，完成后的检查点不能被意外覆盖。

它是外部认知支架，不证明人脑能力已经提高，因此使用记录只能作为练习或业务效率证据，不能直接计入延迟回忆认证。

