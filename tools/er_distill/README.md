# er_distill — Gemini ER 1.6 黑盒实验脚手架（直蒸馏已暂停）

> **合规硬门（2026-07-15）**：现行 [Gemini API 条款](https://ai.google.dev/gemini-api/terms)
> 禁止使用服务开发与其竞争的模型。未经 Google 书面授权和公司法务确认，不得调用
> `gen_dataset.py` 将 ER 输出用于训练、筛选或优化学生模型。key 是否存在不改变这条硬门。

历史方案见 [旧 PRD](../../docs/development/gemini_er_distill_prd.md)。当前执行路线见：

- [分层多 Teacher 蒸馏与 G1 单策略方案](../../docs/development/gemini_er_multiteacher_distillation_plan.md)
- [蒸馏阶段门、指标与评分卡](../../docs/development/gemini_er_distillation_scorecard.md)

## 历史实验流水线（暂停；不要按下列顺序执行）

```bash
# 0) 抽帧（已跑过，产出 data/.lfs/distill/frames_v0/，1432 帧 + index.jsonl）
.venv/bin/python tools/er_distill/extract_frames.py --interval 1.0

# 1) BLOCKED：教师打标。仅在取得 Google 书面授权和公司法务确认后才能解除。
# export GEMINI_API_KEY=你的key
# .venv/bin/python tools/er_distill/gen_dataset.py --qps 0.5

# 2) 学生零样本基线（mlx-vlm 独立环境 ~/.venvs/er-distill）
~/.venvs/er-distill/bin/python tools/er_distill/run_student.py \
    --model mlx-community/gemma-4-26b-a4b-it-4bit

# 3) 一致性评测（蒸馏前 = 基线；蒸馏后换 --student 文件再跑）
.venv/bin/python tools/er_distill/eval_agreement.py \
    --teacher data/.lfs/distill/frames_v0/teacher_raw.jsonl \
    --student data/.lfs/distill/frames_v0/student_raw.jsonl
```

## 文件说明

| 文件 | 作用 | 依赖环境 |
|------|------|----------|
| `extract_frames.py` | 从 Go2 真值 DB 抽相机帧 | 项目 `.venv`（要 dimos） |
| `teacher_client.py` | Gemini ER 1.6 API 封装 + 4 类任务 prompt | 项目 `.venv`（google-genai） |
| `gen_dataset.py` | 批量教师打标 → `teacher_raw.jsonl` | 项目 `.venv` |
| `run_student.py` | Gemma 4 本机推理同批帧 → `student_raw.jsonl` | `~/.venvs/er-distill`（mlx-vlm） |
| `eval_agreement.py` | 学生 vs 教师一致性指标 | 项目 `.venv`（只用 typer+标准库） |

坐标约定统一为教师格式：`[y, x]` / `[ymin, xmin, ymax, xmax]`，归一化 0-1000。
