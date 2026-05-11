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
LESSONS_FILE="${STATE_DIR}/lessons_learned.txt"
PROGRESS_FILE="${STATE_DIR}/progress.json"
WORKFLOW_STATE_FILE="${STATE_DIR}/workflow_state.json"
TASK_RESULT_FILE="${STATE_DIR}/task_result.txt"
TEST_REPORT_FILE="${STATE_DIR}/test_report.txt"
ROUND_PLAN_FILE="${STATE_DIR}/current_plan.txt"

MASTER_PROMPT_TEMPLATE="${PROMPT_DIR}/master_scheduler_prompt.txt"
PLANNER_PROMPT_TEMPLATE="${PROMPT_DIR}/planner_prompt.txt"
DEVELOPER_PROMPT_TEMPLATE="${PROMPT_DIR}/developer_prompt.txt"
TESTER_PROMPT_TEMPLATE="${PROMPT_DIR}/tester_prompt.txt"

MASTER_MODEL="${MASTER_MODEL:-balanced-model}"
PLANNER_MODEL="${PLANNER_MODEL:-quality-model}"
DEVELOPER_MODEL="${DEVELOPER_MODEL:-fast-model}"
TESTER_MODEL="${TESTER_MODEL:-quality-model}"

MAX_ROUNDS="${MAX_ROUNDS:-50}"
MAX_FIX_ATTEMPTS="${MAX_FIX_ATTEMPTS:-3}"
ROUND_SLEEP_SECONDS="${ROUND_SLEEP_SECONDS:-2}"
ROLE_CALL_MAX_RETRIES="${ROLE_CALL_MAX_RETRIES:-2}"
ROLE_RETRY_SLEEP_SECONDS="${ROLE_RETRY_SLEEP_SECONDS:-2}"
CONTINUE_ON_ROLE_ERROR="${CONTINUE_ON_ROLE_ERROR:-false}"
MULTI_AGENT_CALL_TEMPLATE="${MULTI_AGENT_CALL_TEMPLATE:-}"

PLANNER_RESUME_FILE="${STATE_DIR}/planner.resume_id"
DEVELOPER_RESUME_FILE="${STATE_DIR}/developer.resume_id"
TESTER_RESUME_FILE="${STATE_DIR}/tester.resume_id"

mkdir -p "$STATE_DIR" "$PROMPT_DIR" "$LOG_DIR"
touch "$LESSONS_FILE" "$TASK_RESULT_FILE" "$TEST_REPORT_FILE" "$ROUND_PLAN_FILE"

if [[ ! -f "$REQUIREMENT_FILE" ]]; then
  echo "缺少需求文件: $REQUIREMENT_FILE"
  exit 1
fi

if [[ -z "$MULTI_AGENT_CALL_TEMPLATE" ]]; then
  echo "未配置 MULTI_AGENT_CALL_TEMPLATE，请先编辑 config.env"
  exit 1
fi

if [[ ! -f "$MASTER_PROMPT_TEMPLATE" ]] || [[ ! -f "$PLANNER_PROMPT_TEMPLATE" ]] || [[ ! -f "$DEVELOPER_PROMPT_TEMPLATE" ]] || [[ ! -f "$TESTER_PROMPT_TEMPLATE" ]]; then
  echo "缺少 prompt 模板文件，请先检查 prompts/ 目录。"
  exit 1
fi

now_iso() {
  date -Iseconds
}

safe_file_content() {
  local file_path="$1"
  if [[ -f "$file_path" ]]; then
    sed 's/"/\\"/g' "$file_path" | tr '\n' ' '
  else
    echo ""
  fi
}

write_progress() {
  local status="$1"
  local round="$2"
  local fix_attempt="$3"
  cat > "$PROGRESS_FILE" <<EOF
{
  "mode": "multi_agent",
  "status": "$status",
  "round": $round,
  "fix_attempt": $fix_attempt,
  "updated_at": "$(now_iso)"
}
EOF
}

write_workflow_state() {
  local status="$1"
  local phase="$2"
  local round="$3"
  local task_id="$4"
  local fix_attempt="$5"
  local dev_owner="$6"
  local test_owner="$7"
  local last_error="$8"
  cat > "$WORKFLOW_STATE_FILE" <<EOF
{
  "status": "$status",
  "phase": "$phase",
  "round": $round,
  "task_id": "$task_id",
  "fix_attempt": $fix_attempt,
  "owner_dev_session": "$dev_owner",
  "owner_test_session": "$test_owner",
  "last_error": "$last_error",
  "updated_at": "$(now_iso)"
}
EOF
}

append_failure_event() {
  local role="$1"
  local round="$2"
  local fix_attempt="$3"
  local reason="$4"
  cat >> "${STATE_DIR}/errors.log" <<EOF
[$(now_iso)] role=$role round=$round fix_attempt=$fix_attempt reason="$reason"
EOF
}

build_prompt() {
  local template_file="$1"
  local output_file="$2"
  cat > "$output_file" <<EOF
$(cat "$template_file")

-----------------------------
需求文件路径:
$REQUIREMENT_FILE

当前计划:
$ROUND_PLAN_FILE

开发结果:
$TASK_RESULT_FILE

测试报告:
$TEST_REPORT_FILE

经验库:
$LESSONS_FILE
-----------------------------
EOF
}

run_role() {
  local role="$1"
  local model="$2"
  local prompt_file="$3"
  local output_file="$4"
  local resume_id_file="$5"
  local log_file="$6"

  export ROLE MODEL PROMPT_FILE OUTPUT_FILE RESUME_ID_FILE LOG_FILE REQUIREMENT_FILE
  ROLE="$role"
  MODEL="$model"
  PROMPT_FILE="$prompt_file"
  OUTPUT_FILE="$output_file"
  RESUME_ID_FILE="$resume_id_file"
  LOG_FILE="$log_file"

  eval "$MULTI_AGENT_CALL_TEMPLATE"
}

run_role_with_retry() {
  local role="$1"
  local model="$2"
  local prompt_file="$3"
  local output_file="$4"
  local resume_id_file="$5"
  local log_file="$6"
  local round="$7"
  local fix_attempt="$8"

  local attempt=0
  while (( attempt <= ROLE_CALL_MAX_RETRIES )); do
    if run_role "$role" "$model" "$prompt_file" "$output_file" "$resume_id_file" "$log_file"; then
      if [[ -s "$output_file" ]]; then
        return 0
      fi
      append_failure_event "$role" "$round" "$fix_attempt" "output_empty"
    else
      append_failure_event "$role" "$round" "$fix_attempt" "command_failed_attempt_${attempt}"
    fi
    ((attempt++))
    sleep "$ROLE_RETRY_SLEEP_SECONDS"
  done
  return 1
}

current_round=0
current_fix_attempt=0
current_phase="init"
current_task_id=""
current_dev_owner=""
current_test_owner=""

on_script_error() {
  local code=$?
  local msg="script_error_exit_code_${code}_phase_${current_phase}"
  write_progress "crashed" "$current_round" "$current_fix_attempt"
  write_workflow_state "crashed" "$current_phase" "$current_round" "$current_task_id" "$current_fix_attempt" "$current_dev_owner" "$current_test_owner" "$msg"
  append_failure_event "system" "$current_round" "$current_fix_attempt" "$msg"
  exit "$code"
}
trap on_script_error ERR

round=1
write_progress "running" "$round" 0
write_workflow_state "running" "start" "$round" "" 0 "" "" ""

while (( round <= MAX_ROUNDS )); do
  echo "========== Round $round =========="
  current_round="$round"
  current_fix_attempt=0
  current_phase="plan"
  current_task_id="round_${round}"

  planner_prompt="${STATE_DIR}/planner_prompt_round_${round}.txt"
  planner_output="${STATE_DIR}/planner_output_round_${round}.txt"
  build_prompt "$PLANNER_PROMPT_TEMPLATE" "$planner_prompt"
  write_workflow_state "running" "planner" "$round" "$current_task_id" 0 "$current_dev_owner" "$current_test_owner" ""
  if ! run_role_with_retry "planner" "$PLANNER_MODEL" "$planner_prompt" "$planner_output" "$PLANNER_RESUME_FILE" "${LOG_DIR}/planner.log" "$round" 0; then
    write_progress "failed_planner" "$round" 0
    write_workflow_state "failed" "planner" "$round" "$current_task_id" 0 "$current_dev_owner" "$current_test_owner" "planner_call_failed"
    if [[ "$CONTINUE_ON_ROLE_ERROR" == "true" ]]; then
      ((round++))
      sleep "$ROUND_SLEEP_SECONDS"
      continue
    fi
    exit 1
  fi
  cp "$planner_output" "$ROUND_PLAN_FILE"
  task_id_candidate="$(awk -F': ' '/^TASK_ID:/{print $2; exit}' "$ROUND_PLAN_FILE" | awk 'NF')"
  if [[ -n "$task_id_candidate" ]]; then
    current_task_id="$task_id_candidate"
  fi

  fix_attempt=0
  current_dev_owner="$(safe_file_content "$DEVELOPER_RESUME_FILE")"
  current_test_owner="$(safe_file_content "$TESTER_RESUME_FILE")"
  while true; do
    current_fix_attempt="$fix_attempt"
    current_phase="develop"
    developer_prompt="${STATE_DIR}/developer_prompt_round_${round}_fix_${fix_attempt}.txt"
    developer_output="${STATE_DIR}/developer_output_round_${round}_fix_${fix_attempt}.txt"
    build_prompt "$DEVELOPER_PROMPT_TEMPLATE" "$developer_prompt"
    write_workflow_state "running" "developer" "$round" "$current_task_id" "$fix_attempt" "$current_dev_owner" "$current_test_owner" ""
    if ! run_role_with_retry "developer" "$DEVELOPER_MODEL" "$developer_prompt" "$developer_output" "$DEVELOPER_RESUME_FILE" "${LOG_DIR}/developer.log" "$round" "$fix_attempt"; then
      write_progress "failed_developer" "$round" "$fix_attempt"
      write_workflow_state "failed" "developer" "$round" "$current_task_id" "$fix_attempt" "$current_dev_owner" "$current_test_owner" "developer_call_failed"
      if [[ "$CONTINUE_ON_ROLE_ERROR" == "true" ]]; then
        break
      fi
      exit 1
    fi
    cp "$developer_output" "$TASK_RESULT_FILE"

    current_phase="test"
    tester_prompt="${STATE_DIR}/tester_prompt_round_${round}_fix_${fix_attempt}.txt"
    tester_output="${STATE_DIR}/tester_output_round_${round}_fix_${fix_attempt}.txt"
    build_prompt "$TESTER_PROMPT_TEMPLATE" "$tester_prompt"
    write_workflow_state "running" "tester" "$round" "$current_task_id" "$fix_attempt" "$current_dev_owner" "$current_test_owner" ""
    if ! run_role_with_retry "tester" "$TESTER_MODEL" "$tester_prompt" "$tester_output" "$TESTER_RESUME_FILE" "${LOG_DIR}/tester.log" "$round" "$fix_attempt"; then
      write_progress "failed_tester" "$round" "$fix_attempt"
      write_workflow_state "failed" "tester" "$round" "$current_task_id" "$fix_attempt" "$current_dev_owner" "$current_test_owner" "tester_call_failed"
      if [[ "$CONTINUE_ON_ROLE_ERROR" == "true" ]]; then
        break
      fi
      exit 1
    fi
    cp "$tester_output" "$TEST_REPORT_FILE"

    awk '/^LESSONS:/{flag=1; next} flag{print}' "$developer_output" >> "$LESSONS_FILE"
    awk '/^LESSONS:/{flag=1; next} flag{print}' "$tester_output" >> "$LESSONS_FILE"

    if rg -q "^TEST_STATUS:[[:space:]]*PASS" "$TEST_REPORT_FILE"; then
      echo "Round $round 通过测试。"
      write_workflow_state "running" "task_pass" "$round" "$current_task_id" "$fix_attempt" "$current_dev_owner" "$current_test_owner" ""
      break
    fi

    current_phase="fix"
    ((fix_attempt++))
    write_progress "running" "$round" "$fix_attempt"
    if (( fix_attempt > MAX_FIX_ATTEMPTS )); then
      echo "Round $round 修复次数超过上限($MAX_FIX_ATTEMPTS)，停止。"
      write_progress "stopped_max_fix_attempts" "$round" "$fix_attempt"
      write_workflow_state "failed" "fix_limit" "$round" "$current_task_id" "$fix_attempt" "$current_dev_owner" "$current_test_owner" "max_fix_attempts_reached"
      exit 1
    fi
  done

  if rg -q "^GLOBAL_STATUS:[[:space:]]*DONE" "$ROUND_PLAN_FILE"; then
    echo "主计划判定全部完成。"
    write_progress "completed" "$round" 0
    write_workflow_state "completed" "done" "$round" "$current_task_id" 0 "$current_dev_owner" "$current_test_owner" ""
    exit 0
  fi

  ((round++))
  write_progress "running" "$round" 0
  write_workflow_state "running" "next_round" "$round" "" 0 "$current_dev_owner" "$current_test_owner" ""
  sleep "$ROUND_SLEEP_SECONDS"
done

echo "达到最大轮次: $MAX_ROUNDS，已停止。"
write_progress "stopped_max_rounds" "$round" 0
write_workflow_state "stopped" "max_rounds" "$round" "" 0 "$current_dev_owner" "$current_test_owner" "max_rounds_reached"
exit 0
