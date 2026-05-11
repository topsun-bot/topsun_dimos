#!/usr/bin/env bash
# ===========================================================================
# Multi-Agent Development Pipeline — Claude Code Orchestrator
# ===========================================================================
# Architecture:
#   Orchestrator (this script) → Planner → Developer → Tester → loop on fail
#
# Principles:
#   - Each agent runs in a fresh Claude Code session (clean context)
#   - File-based communication between agents (no context pollution)
#   - Bug author fixes, bug reporter verifies (max N retries)
#   - Experience accumulation to avoid repeating mistakes
#   - Different models per role (speed vs quality)
#
# Usage:
#   ./scripts/pipeline.sh <requirement.md>         Start pipeline
#   ./scripts/pipeline.sh --resume                 Resume from last checkpoint
#   ./scripts/pipeline.sh --status                 Show current state
#   ./scripts/pipeline.sh --clean                  Reset pipeline state
# ===========================================================================

set -euo pipefail

# ---- Config (overridable via .pipeline/config.json) ----
MAX_RETRIES=3
MODEL_PLANNER="opus"
MODEL_DEVELOPER="sonnet"
MODEL_TESTER="opus"
PERMISSION_MODE="auto"
EFFORT="high"

# ---- Paths ----
PIPELINE_DIR=".pipeline"
SESSION_DIR="$PIPELINE_DIR/session"
LOG_DIR="$PIPELINE_DIR/logs"
CONFIG_FILE="$PIPELINE_DIR/config.json"
STATE_FILE="$PIPELINE_DIR/state"
RETRY_FILE="$PIPELINE_DIR/retry_count"
EXPERIENCE_FILE="$PIPELINE_DIR/experience.md"

# ---- Phase identifiers ----
PHASE_INIT="init"
PHASE_PLAN="plan"
PHASE_DEVELOP="develop"
PHASE_TEST="test"
PHASE_FIX="fix"
PHASE_DONE="done"
PHASE_FAILED="failed"

# ==================================================================
# Configuration
# ==================================================================

load_config() {
  if [ ! -f "$CONFIG_FILE" ]; then
    log "Config $CONFIG_FILE not found, using defaults"
    return
  fi

  # Use python3 for robust JSON parsing
  local cfg
  cfg=$(python3 -c "
import json
with open('$CONFIG_FILE') as f:
    c = json.load(f)

# Validate model values
valid_models = {'opus', 'sonnet', 'haiku'}
for role in ('planner', 'developer', 'tester'):
    m = c.get('models', {}).get(role, '')
    if m and m not in valid_models:
        print(f'WARNING: Unknown model \"{m}\" for {role}, using default')

print(json.dumps({
    'max_retries': c.get('max_retries', $MAX_RETRIES),
    'model_planner': c.get('models', {}).get('planner', '$MODEL_PLANNER'),
    'model_developer': c.get('models', {}).get('developer', '$MODEL_DEVELOPER'),
    'model_tester': c.get('models', {}).get('tester', '$MODEL_TESTER'),
    'permission_mode': c.get('permission_mode', '$PERMISSION_MODE'),
    'effort': c.get('effort', '$EFFORT'),
}))
" 2>&1) || die "Failed to parse $CONFIG_FILE"

  MAX_RETRIES=$(echo "$cfg" | python3 -c "import json,sys; print(json.load(sys.stdin)['max_retries'])")
  MODEL_PLANNER=$(echo "$cfg" | python3 -c "import json,sys; print(json.load(sys.stdin)['model_planner'])")
  MODEL_DEVELOPER=$(echo "$cfg" | python3 -c "import json,sys; print(json.load(sys.stdin)['model_developer'])")
  MODEL_TESTER=$(echo "$cfg" | python3 -c "import json,sys; print(json.load(sys.stdin)['model_tester'])")
  PERMISSION_MODE=$(echo "$cfg" | python3 -c "import json,sys; print(json.load(sys.stdin)['permission_mode'])")
  EFFORT=$(echo "$cfg" | python3 -c "import json,sys; print(json.load(sys.stdin)['effort'])")

  log "Config loaded: max_retries=$MAX_RETRIES"
  log "  Planner: $MODEL_PLANNER | Developer: $MODEL_DEVELOPER | Tester: $MODEL_TESTER"
}

# ==================================================================
# Utilities
# ==================================================================

setup_dirs() {
  mkdir -p "$SESSION_DIR" "$LOG_DIR"
}

log() { mkdir -p "$LOG_DIR"; echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_DIR/pipeline.log"; }
die() { log "FATAL: $*"; exit 1; }

get_state() { cat "$STATE_FILE" 2>/dev/null || echo "$PHASE_INIT"; }
set_state() { echo "$1" > "$STATE_FILE"; log "→ Phase: $1"; }

get_retry() { cat "$RETRY_FILE" 2>/dev/null || echo "0"; }
set_retry() { echo "$1" > "$RETRY_FILE"; }

inc_retry() {
  local n; n=$(get_retry)
  n=$((n + 1))
  set_retry "$n"
  log "Retry $n/$MAX_RETRIES"
}

reset_retry() { set_retry 0; }
clean_state() { rm -f "$STATE_FILE" "$RETRY_FILE"; rm -rf "$SESSION_DIR"/*; log "Pipeline state cleaned"; }

# Verify claude CLI is available
check_claude() {
  if ! command -v claude &>/dev/null; then
    die "claude CLI not found. Install it first: https://claude.ai/claude-code"
  fi
  log "Using $(claude --version 2>/dev/null || echo 'claude CLI')"
}

# Verify requirement file exists
check_requirement() {
  if [ -z "${REQUIREMENT:-}" ]; then
    die "No requirement file specified. Usage: $0 <requirement.md>"
  fi
  if [ ! -f "$REQUIREMENT" ]; then
    die "Requirement file not found: $REQUIREMENT"
  fi
  log "Requirement: $(realpath "$REQUIREMENT")"
}

# ==================================================================
# Agent Prompts (appended to Claude Code's default system prompt)
# ==================================================================

# Role definitions are used with --append-system-prompt
# Task instructions are passed via -p (positional)

PLANNER_ROLE() { cat << 'EOF'
You are the PLANNER agent in a multi-agent development pipeline.

## Your Role
Analyze requirements and create detailed, actionable development plans. You set the direction for the entire pipeline.

## Process
1. Read the requirement file thoroughly
2. Explore the codebase to understand current structure, conventions, and patterns
3. Create a step-by-step implementation plan
4. Write a technical specification with clear acceptance criteria

## Output Files
- `.pipeline/session/plan.md` — Step-by-step implementation plan with:
  - Task breakdown (each task should be self-contained, suitable for one Claude Code session)
  - File paths to create or modify
  - Dependencies between tasks
  - Testing strategy for each task
  - Acceptance criteria

- `.pipeline/session/spec.md` — Technical specification with:
  - Architecture overview
  - Interface definitions
  - Data flow
  - Error handling strategy
  - Edge cases to consider

## Guidelines
- Be specific about file paths and code patterns
- Consider edge cases, error handling, and testing throughout
- Break work into the smallest reasonable chunks
- Read existing code to match the project's style and conventions
- If the project has CLAUDE.md or AGENTS.md, read those for conventions
EOF
}

DEVELOPER_ROLE() { cat << 'EOF'
You are the DEVELOPER agent in a multi-agent development pipeline.

## Your Role
Implement features and fix bugs based on the plan and test feedback. You own the code quality.

## Process
1. Read `.pipeline/session/plan.md` and `.pipeline/session/spec.md`
2. Read `.pipeline/experience.md` if it exists (learn from past mistakes)
3. Explore the relevant parts of the codebase
4. Implement the changes step by step
5. Run existing tests to verify you haven't broken anything

## Accountability
- You are responsible for the code you write
- If the Tester finds bugs, you will fix them (not someone else)
- Test your code before declaring it done

## Fix Mode (when retrying after test failure)
- Read `.pipeline/session/test-report.md` for specific failure details
- Focus on fixing the reported issues only — do not rewrite unrelated code
- Read `.pipeline/experience.md` to avoid repeating past mistakes
- After fixing, run the tests again

## Guidelines
- Follow the project's existing code style and conventions
- Add necessary tests alongside implementation
- Don't leave TODO comments — implement or don't
- Prefer small, focused changes over large rewrites
- Run `git diff` to review your changes before finishing
EOF
}

TESTER_ROLE() { cat << 'EOF'
You are the TESTER agent in a multi-agent development pipeline.

## Your Role
Test and review the implementation for quality, correctness, and alignment with requirements. You are the quality gate.

## Process
1. Read `.pipeline/session/plan.md` and `.pipeline/session/spec.md`
2. Explore the implementation thoroughly
3. Run all existing tests (pytest, etc.)
4. Review the code for:
   - Correctness: does it meet the spec?
   - Quality: is it well-structured and maintainable?
   - Edge cases: are error paths handled?
   - Test coverage: are new features tested?
5. Write the test report

## Output Files
- `.pipeline/session/test-report.md` — Detailed test report with:
  - Summary (what was tested, overall result)
  - Pass/fail details for each requirement
  - Specific issues found (with file paths and line numbers if applicable)
  - Severity of each issue (blocker / major / minor)

- `.pipeline/session/test_result` — Single line: PASS or FAIL
  - PASS: all blockers resolved, code meets spec
  - FAIL: one or more blocker issues remain

## Accountability
- You validate whether the fix resolved the issue
- If you marked a bug as fixed but it's not, the pipeline will loop
- Be thorough but fair — not every minor style issue is a blocker

## Guidelines
- Run tests, don't just review code
- Distinguish between blockers (must fix) and suggestions (nice to have)
- If tests don't exist, write them to verify the implementation
- Check both happy path and error paths
EOF
}

# ==================================================================
# Agent Runner
# ==================================================================

run_agent() {
  local role="$1"
  local model="$2"
  local role_upper; role_upper=$(echo "$3" | tr '[:lower:]' '[:upper:]')
  local role_func="${role_upper}_ROLE"
  local task_prompt="$4"
  local log_file="$LOG_DIR/${role}.log"

  log "=== Running $role agent (model: $model) ===" | tee -a "$log_file"

  # Get the role prompt from the function
  local role_prompt
  role_prompt=$("$role_func")

  # Build claude args
  local claude_args=(
    claude -p "$task_prompt"
    --append-system-prompt "$role_prompt"
    --model "$model"
    --permission-mode "$PERMISSION_MODE"
    --effort "$EFFORT"
    --no-session-persistence
    --output-format text
  )

  # Run claude
  set +e
  "${claude_args[@]}" 2>&1 | tee -a "$log_file"
  local exit_code=${PIPESTATUS[0]}
  set -e

  if [ $exit_code -ne 0 ]; then
    log "ERROR: $role agent failed (exit code: $exit_code)"
    return $exit_code
  fi

  log "$role agent completed successfully"
  return 0
}

# ==================================================================
# Phase Implementations
# ==================================================================

phase_planner() {
  log "══════════════════ PHASE: PLANNER ══════════════════"

  local task
  task=$(cat << TASK
Read the requirement file at "$REQUIREMENT" and create a development plan.

Steps:
1. First, read the requirement file to understand what needs to be done
2. Explore the codebase structure — read key files to understand conventions
3. Create a detailed implementation plan at .pipeline/session/plan.md
4. Create a technical specification at .pipeline/session/spec.md

Be thorough. The Developer agent will follow your plan exactly, so include specific file paths, code patterns, and testing strategies.
TASK
)

  run_agent "planner" "$MODEL_PLANNER" "planner" "$task"
}

phase_developer() {
  log "══════════════════ PHASE: DEVELOPER ══════════════════"

  local fix_mode="${1:-false}"
  local task

  if [ "$fix_mode" = "fix" ]; then
    task=$(cat << TASK
The Tester found issues in the previous implementation. Fix them.

1. Read .pipeline/session/test-report.md for the specific issues to fix
2. Read .pipeline/session/plan.md and .pipeline/session/spec.md for context
3. Read .pipeline/experience.md if it exists to avoid past mistakes
4. Fix each reported issue carefully
5. Run existing tests to verify nothing is broken

Fix ONLY the issues reported. Do not rewrite unrelated code.
TASK
)
  else
    task=$(cat << TASK
Implement the features described in the plan.

1. Read .pipeline/session/plan.md for the implementation plan
2. Read .pipeline/session/spec.md for the technical specification
3. Read .pipeline/experience.md if it exists to learn from past mistakes
4. Explore the codebase to understand the relevant parts
5. Implement each task from the plan
6. Run existing tests to verify nothing is broken

Be thorough. Implement all tasks from the plan.
TASK
)
  fi

  run_agent "developer" "$MODEL_DEVELOPER" "developer" "$task"
}

phase_tester() {
  log "══════════════════ PHASE: TESTER ══════════════════"

  local task
  task=$(cat << TASK
Test the implementation against the plan and specification.

1. Read .pipeline/session/plan.md for the requirements
2. Read .pipeline/session/spec.md for the technical spec
3. Explore the implemented code thoroughly
4. Run all existing tests
5. Review code quality, correctness, and edge case handling
6. Check if tests exist for new features — if not, write them
7. Write .pipeline/session/test-report.md with detailed findings
8. Write .pipeline/session/test_result with either PASS or FAIL

Be thorough. A FAIL should only be for real blockers, not minor style preferences.
TASK
)

  # Save test_result from previous run if it exists (for fix loop accountability)
  if [ -f "$SESSION_DIR/test_result" ]; then
    log "Previous test result: $(cat "$SESSION_DIR/test_result")"
  fi

  run_agent "tester" "$MODEL_TESTER" "tester" "$task"
}

check_test_result() {
  if [ ! -f "$SESSION_DIR/test_result" ]; then
    log "WARNING: No test_result file found, assuming FAIL"
    return 1
  fi
  local result
  result=$(tr '[:lower:]' '[:upper:]' < "$SESSION_DIR/test_result" | head -1)
  if [ "$result" = "PASS" ]; then
    log "TEST RESULT: PASS ✓"
    return 0
  else
    log "TEST RESULT: FAIL ✗"
    return 1
  fi
}

# ==================================================================
# Experience Accumulation
# ==================================================================

update_experience() {
  local retry_count
  retry_count=$(get_retry)
  if [ "$retry_count" -gt 0 ]; then
    # Append to experience file (the tester's last report captures the issues)
    {
      echo ""
      echo "---"
      echo "## Pipeline Run $(date '+%Y-%m-%d %H:%M')"
      echo "- Requirement: $REQUIREMENT"
      echo "- Retries needed: $retry_count"
      echo "- Result: $(cat "$SESSION_DIR/test_result" 2>/dev/null || echo 'unknown')"
    } >> "$EXPERIENCE_FILE" 2>/dev/null || true
    log "Experience updated: $EXPERIENCE_FILE"
  fi
}

# ==================================================================
# Main Pipeline Loop
# ==================================================================

run_pipeline() {
  local requirement_file="$1"
  local state
  state=$(get_state)

  log "Pipeline starting (requirement: $requirement_file)"
  log "Initial state: $state"

  while true; do
    state=$(get_state)

    case "$state" in
      "$PHASE_INIT")
        set_state "$PHASE_PLAN"
        ;;

      "$PHASE_PLAN")
        phase_planner || die "Planner failed"
        set_state "$PHASE_DEVELOP"
        ;;

      "$PHASE_DEVELOP")
        phase_developer false || die "Developer failed"
        set_state "$PHASE_TEST"
        ;;

      "$PHASE_TEST")
        phase_tester || die "Tester failed"
        if check_test_result; then
          set_state "$PHASE_DONE"
        else
          local retry_count
          retry_count=$(get_retry)
          if [ "$retry_count" -ge "$MAX_RETRIES" ]; then
            log "Max retries ($MAX_RETRIES) reached"
            set_state "$PHASE_FAILED"
          else
            inc_retry
            set_state "$PHASE_FIX"
          fi
        fi
        ;;

      "$PHASE_FIX")
        phase_developer fix || die "Developer fix failed"
        set_state "$PHASE_TEST"
        ;;

      "$PHASE_DONE")
        update_experience
        log "══════════════════ PIPELINE COMPLETE ══════════════════"
        log "All phases completed successfully!"
        echo ""
        echo "  ✓ Plan:     $SESSION_DIR/plan.md"
        echo "  ✓ Spec:     $SESSION_DIR/spec.md"
        echo "  ✓ Test:     $SESSION_DIR/test-report.md"
        echo "  ✓ Logs:     $LOG_DIR/"
        echo ""
        echo "Review the changes and commit when ready."
        return 0
        ;;

      "$PHASE_FAILED")
        update_experience
        log "══════════════════ PIPELINE FAILED ══════════════════"
        log "Pipeline failed after $MAX_RETRIES retries."
        echo ""
        echo "  ✗ Last test report: $SESSION_DIR/test-report.md"
        echo "  ✗ Logs:             $LOG_DIR/"
        echo ""
        echo "Check the test report for remaining issues."
        return 1
        ;;

      *)
        die "Unknown state: $state"
        ;;
    esac
  done
}

# ==================================================================
# Entry Point
# ==================================================================

main() {
  local cmd="${1:-}"
  REQUIREMENT=""

  # Parse commands
  case "$cmd" in
    --resume)
      shift
      if [ -f "$STATE_FILE" ]; then
        log "Resuming pipeline from state: $(get_state)"
      else
        die "No pipeline state found to resume. Start with: $0 <requirement.md>"
      fi
      setup_dirs
      load_config
      check_claude
      run_pipeline "(resumed)"
      ;;

    --status)
      if [ ! -f "$STATE_FILE" ]; then
        echo "No pipeline running."
        exit 0
      fi
      echo "State:     $(get_state)"
      echo "Retries:   $(get_retry)/$MAX_RETRIES"
      echo "Directory: $(realpath "$PIPELINE_DIR")"
      if [ -f "$SESSION_DIR/test-report.md" ]; then
        echo "Last report: $SESSION_DIR/test-report.md"
      fi
      ;;

    --clean)
      clean_state
      ;;

    -h|--help)
      echo "Multi-Agent Development Pipeline"
      echo ""
      echo "Usage:"
      echo "  $0 <requirement.md>         Start pipeline"
      echo "  $0 --resume                 Resume from checkpoint"
      echo "  $0 --status                 Show pipeline status"
      echo "  $0 --clean                  Reset pipeline state"
      echo "  $0 -h --help                Show this help"
      echo ""
      echo "Architecture:"
      echo "  Orchestrator → Planner → Developer → Tester → [loop on fail]"
      echo ""
      echo "Config: $CONFIG_FILE"
      echo "Logs:   $LOG_DIR/"
      ;;

    *)
      REQUIREMENT="$cmd"
      if [ -z "$REQUIREMENT" ]; then
        die "Usage: $0 <requirement.md>"
      fi
      check_requirement
      setup_dirs
      load_config
      check_claude
      clean_state  # fresh start
      run_pipeline "$REQUIREMENT"
      ;;
  esac
}

main "$@"
