#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE_DIR"

if [[ -f "./config.env" ]]; then
  # shellcheck disable=SC1091
  source "./config.env"
elif [[ -f "./config.env.example" ]]; then
  # shellcheck disable=SC1091
  source "./config.env.example"
fi

REQUIREMENT_FILE="${REQUIREMENT_FILE:-./requirement_template.txt}"
STATE_DIR="${STATE_DIR:-./state}"
PROMPT_DIR="${PROMPT_DIR:-./prompts}"
LOG_DIR="${LOG_DIR:-./state/logs}"
TASK_QUEUE_FILE="${STATE_DIR}/task_queue.txt"
LESSONS_FILE="${STATE_DIR}/lessons_learned.txt"
PROGRESS_FILE="${STATE_DIR}/progress.json"

MODEL="${SINGLE_MODEL:-fast-model}"
MAX_CYCLES="${SINGLE_MAX_CYCLES:-200}"
SLEEP_SECONDS="${SINGLE_SLEEP_SECONDS:-2}"
SINGLE_AGENT_CALL_TEMPLATE="${SINGLE_AGENT_CALL_TEMPLATE:-}"

mkdir -p "$STATE_DIR" "$PROMPT_DIR" "$LOG_DIR"
touch "$TASK_QUEUE_FILE" "$LESSONS_FILE"

if [[ ! -f "$REQUIREMENT_FILE" ]]; then
  echo "缺少需求文件: $REQUIREMENT_FILE"
  exit 1
fi

if [[ -z "$SINGLE_AGENT_CALL_TEMPLATE" ]]; then
  echo "未配置 SINGLE_AGENT_CALL_TEMPLATE，请先编辑 config.env"
  exit 1
fi

now_iso() {
  date -Iseconds
}

write_progress() {
  local status="$1"
  local cycle="$2"
  local pending_count="$3"
  cat > "$PROGRESS_FILE" <<EOF
{
  "mode": "single_agent_ralph",
  "status": "$status",
  "cycle": $cycle,
  "pending_tasks": $pending_count,
  "updated_at": "$(now_iso)"
}
EOF
}

pending_count() {
  awk 'NF{count++} END{print count+0}' "$TASK_QUEUE_FILE"
}

pop_first_task() {
  local first
  first="$(awk 'NF{print; exit}' "$TASK_QUEUE_FILE")"
  if [[ -z "$first" ]]; then
    echo ""
    return
  fi
  awk 'BEGIN{done=0} {if(!done && NF){done=1; next} print}' "$TASK_QUEUE_FILE" > "${TASK_QUEUE_FILE}.tmp"
  mv "${TASK_QUEUE_FILE}.tmp" "$TASK_QUEUE_FILE"
  echo "$first"
}

append_task() {
  local task="$1"
  if [[ -n "$task" ]]; then
    echo "$task" >> "$TASK_QUEUE_FILE"
  fi
}

run_agent_once() {
  local prompt_file="$1"
  local output_file="$2"
  local log_file="$3"
  export MODEL PROMPT_FILE OUTPUT_FILE LOG_FILE
  PROMPT_FILE="$prompt_file"
  OUTPUT_FILE="$output_file"
  LOG_FILE="$log_file"
  eval "$SINGLE_AGENT_CALL_TEMPLATE"
}

cycle=1
write_progress "running" "$cycle" "$(pending_count)"

while (( cycle <= MAX_CYCLES )); do
  todo="$(pop_first_task)"
  if [[ -z "$todo" ]]; then
    write_progress "completed" "$cycle" 0
    echo "全部任务完成。"
    exit 0
  fi

  prompt_file="${STATE_DIR}/single_prompt.txt"
  output_file="${STATE_DIR}/single_output_cycle_${cycle}.txt"
  log_file="${LOG_DIR}/single_agent.log"

  cat > "$prompt_file" <<EOF
你是长任务执行智能体。请严格执行下面任务，并按指定格式输出。

【需求文件】
$REQUIREMENT_FILE

【当前任务】
$todo

【经验库】
$LESSONS_FILE

【输出要求】
请输出以下四段：
1) RESULT: 本次完成结果
2) NEW_TASKS: 若有后续子任务，每行一个，以 "- " 开头；没有就写 "NONE"
3) ISSUES: 遇到的问题
4) LESSONS: 新增经验（可为空）
EOF

  run_agent_once "$prompt_file" "$output_file" "$log_file" || true

  if [[ ! -s "$output_file" ]]; then
    echo "第 $cycle 轮输出为空，任务重新入队: $todo" | tee -a "$log_file"
    append_task "$todo"
    ((cycle++))
    write_progress "running" "$cycle" "$(pending_count)"
    sleep "$SLEEP_SECONDS"
    continue
  fi

  awk '/^LESSONS:/{flag=1; next} flag{print}' "$output_file" >> "$LESSONS_FILE"

  new_tasks="$(awk '/^NEW_TASKS:/{flag=1; next} /^ISSUES:/{flag=0} flag{print}' "$output_file" | sed 's/^- //g' | awk 'NF')"
  if [[ -n "$new_tasks" ]] && ! grep -qx "NONE" <<< "$new_tasks"; then
    while IFS= read -r t; do
      append_task "$t"
    done <<< "$new_tasks"
  fi

  ((cycle++))
  write_progress "running" "$cycle" "$(pending_count)"
  sleep "$SLEEP_SECONDS"
done

write_progress "stopped_max_cycles" "$cycle" "$(pending_count)"
echo "达到最大循环次数: $MAX_CYCLES，已停止。"
exit 0
