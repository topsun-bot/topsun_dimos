#!/usr/bin/env bash
# ==========================================================================
# PR Pipeline — after Tester PASS, push code → PR → Codex → merge.
#
# Usage:
#   export GITHUB_TOKEN="ghp_xxx"
#   bash run_pr_pipeline.sh
#
# Environment variables (config.env):
#   PR_BRANCH          — local branch to push (default: current branch)
#   PR_TARGET_BRANCH   — where the PR targets (default: "dev")
#   PR_TITLE           — PR title (default: auto-generated)
#   PR_BODY_FILE       — path to PR body text (default: auto-generated)
#   CODEX_POLL_MINUTES — max minutes to wait for Codex 👍 (default: 30)
# ==========================================================================

set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE_DIR/.."

if [[ -f "./ai_longrun_harness/config.env" ]]; then
  # shellcheck disable=SC1091
  source "./ai_longrun_harness/config.env"
fi

STATE_DIR="${STATE_DIR:-./ai_longrun_harness/state}"
LOG_DIR="${LOG_DIR:-$STATE_DIR/logs}"
PR_BRANCH="${PR_BRANCH:-$(git branch --show-current)}"
PR_TARGET_BRANCH="${PR_TARGET_BRANCH:-dev}"
CODEX_POLL_MINUTES="${CODEX_POLL_MINUTES:-30}"
GITHUB_REPO="${GITHUB_REPO:-}"
GITHUB_TOKEN="${GITHUB_TOKEN:-}"

mkdir -p "$STATE_DIR" "$LOG_DIR"

# ── helpers ──────────────────────────────────────────────────────────

now_iso() { date -Iseconds; }
die() { echo "[$(now_iso)] ERROR: $*" >&2; exit 1; }
info() { echo "[$(now_iso)] INFO: $*"; }

write_pipeline_state() {
  local status="$1" phase="$2"
  cat > "$STATE_DIR/pipeline_state.json" <<EOF
{
  "status": "$status",
  "phase": "$phase",
  "pr_branch": "$PR_BRANCH",
  "pr_target": "$PR_TARGET_BRANCH",
  "updated_at": "$(now_iso)"
}
EOF
}

# ── validation ───────────────────────────────────────────────────────

if [[ -z "$GITHUB_TOKEN" ]]; then
  die "GITHUB_TOKEN not set. Export it or add to config.env"
fi

if ! git rev-parse --git-dir >/dev/null 2>&1; then
  die "Not in a git repository"
fi

if ! command -v gh &>/dev/null; then
  die "GitHub CLI (gh) not found. Install it first."
fi

# ── 1. Commit & push ────────────────────────────────────────────────

info "Step 1: Commit and push branch '$PR_BRANCH'"

write_pipeline_state "running" "commit"

# Check if there's anything to commit
UNTRACKED=$(git ls-files --others --exclude-standard --directory --no-empty-directory | head -20)
MODIFIED=$(git diff --name-only | head -20)
if [[ -z "$UNTRACKED$MODIFIED" ]]; then
  info "No changes to commit. Using existing branch state."
else
  git add -A
  git commit -m "feat: spatial memory & door detection system

- SpatialRecord: unified door/room/landmark data model
- DoorSpatialMemory: JSON-based persistent landmark store
- DoorNavigationSkill: record_door, record_room, query_door, navigate_to_door
- TopologyGraph: A* topological path planning through known doors
- YOLO + CLIP zero-shot door detector
- Blueprints: Go2 and G1 agentic stacks, DeepSeek V4 Pro integration
- McpClient: model_provider and model_kwargs support

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>" || info "Nothing new to commit"
fi

# Push
if ! git push origin "$PR_BRANCH" 2>&1; then
  die "Git push failed. Check your branch and permissions."
fi
info "Pushed $PR_BRANCH to origin"

# ── 2. Create PR ────────────────────────────────────────────────────

info "Step 2: Create PR targetting '$PR_TARGET_BRANCH'"

write_pipeline_state "running" "create_pr"

# Check if PR already exists
EXISTING_PR=$(gh pr list --head "$PR_BRANCH" --base "$PR_TARGET_BRANCH" --state open --json number --jq '.[0].number' 2>/dev/null || echo "")

COMMIT_MSG=$(git log -1 --format="%s")
PR_TITLE="${PR_TITLE:-$COMMIT_MSG}"

if [[ -n "$EXISTING_PR" ]]; then
  info "PR #$EXISTING_PR already exists. Updating."
  PR_NUMBER="$EXISTING_PR"
  gh pr edit "$PR_NUMBER" --title "$PR_TITLE" --body-file "$STATE_DIR/pr_body.md" 2>/dev/null || true
else
  # Generate PR body
  cat > "$STATE_DIR/pr_body.md" <<PRBODY
## Summary

Spatial Memory & Embodied RAG for Unitree robots.

### New Features
- **Spatial Landmark System**: Unified data model (SpatialRecord) for doors, rooms, and generic landmarks with JSON persistence
- **Door Navigation Skills**: record_door, record_room, query_door, navigate_to_door agent skills
- **Topological Navigation**: A* path planning through known doors and rooms for efficient multi-waypoint navigation
- **Automatic Door Detection**: YOLO + CLIP zero-shot pipeline (door detection + open/closed classification)
- **DeepSeek V4 Pro Integration**: Full agentic blueprints for both Go2 and G1 robots

### Blueprints Added
- \`unitree-go2-agentic-deepseek\` — Go2 with DeepSeek V4 Pro
- \`unitree-g1-agentic-deepseek\` — G1 with DeepSeek V4 Pro (real robot)
- \`unitree-g1-agentic-sim-deepseek\` — G1 with DeepSeek V4 Pro (MuJoCo simulation)

### Testing
- 34 unit tests added for SpatialRecord, DoorSpatialMemory, and TopologyGraph
- Tested in replay mode with Go2 recorded data
- Tested in G1 MuJoCo simulation

PRBODY

  PR_OUTPUT=$(gh pr create \
    --base "$PR_TARGET_BRANCH" \
    --head "$PR_BRANCH" \
    --title "$PR_TITLE" \
    --body-file "$STATE_DIR/pr_body.md" \
    --label "automerge" 2>&1)
  PR_NUMBER=$(echo "$PR_OUTPUT" | grep -oP '/pull/\K[0-9]+' | head -1 || echo "")

  if [[ -z "$PR_NUMBER" ]]; then
    # Fallback: try to get the PR number from list
    PR_NUMBER=$(gh pr list --head "$PR_BRANCH" --base "$PR_TARGET_BRANCH" --state open --json number --jq '.[0].number' 2>/dev/null || echo "")
  fi

  if [[ -z "$PR_NUMBER" ]]; then
    die "Failed to create PR. Output: $PR_OUTPUT"
  fi
  info "Created PR #$PR_NUMBER"
fi

echo "PR_NUMBER=$PR_NUMBER" > "$STATE_DIR/pr_info.txt"
REPO="${GITHUB_REPO:-$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null)}"
echo "PR_URL=https://github.com/$REPO/pull/$PR_NUMBER" >> "$STATE_DIR/pr_info.txt"

# ── 3. Check Codex review status (续期模式 — 不设超时) ──────────────

info "Step 3: Checking Codex review status (PR #$PR_NUMBER)"

write_pipeline_state "running" "codex_review"

CODEX_BOT="chatgpt-codex-connector"
if [[ -z "$REPO" ]]; then
  die "Cannot determine GitHub repo. Set GITHUB_REPO in config.env"
fi

# Check PR state
PR_STATE=$(gh api "repos/$REPO/pulls/$PR_NUMBER" --jq .state 2>/dev/null || echo "unknown")
if [[ "$PR_STATE" != "open" ]]; then
  die "PR #$PR_NUMBER is $PR_STATE (not open). Cannot continue."
fi

# Save latest PR info for resume
echo "LAST_CHECK=$(date -Iseconds)" >> "$STATE_DIR/pr_info.txt"

# Collect all Codex comments (if any)
gh api "repos/$REPO/pulls/$PR_NUMBER/comments" --jq '.[].body' > "$STATE_DIR/codex_comments.txt" 2>/dev/null || true
gh api "repos/$REPO/issues/$PR_NUMBER/comments" --jq '.[].body' >> "$STATE_DIR/codex_comments.txt" 2>/dev/null || true
gh api "repos/$REPO/pulls/$PR_NUMBER/reviews" --jq '.[].body' >> "$STATE_DIR/codex_comments.txt" 2>/dev/null || true

COMMENT_COUNT=$(wc -l < "$STATE_DIR/codex_comments.txt" 2>/dev/null || echo 0)

# Check for Codex 👍 approval
count_thumbs() {
  local TOTAL=0 PR_T COMMENT_T REVIEW_T CID N
  PR_T=$(gh api -H "Accept: application/vnd.github+json" "repos/$REPO/issues/$PR_NUMBER/reactions" --jq "[.[] | select(.content == \"+1\" and (.user.login | test(\"$CODEX_BOT\")))] | length" 2>/dev/null || echo 0)
  COMMENT_T=0
  for CID in $(gh api --paginate "repos/$REPO/issues/$PR_NUMBER/comments" --jq '.[].id' 2>/dev/null); do
    N=$(gh api "repos/$REPO/issues/comments/$CID/reactions" --jq "[.[] | select(.content == \"+1\" and (.user.login | test(\"$CODEX_BOT\")))] | length" 2>/dev/null || echo 0)
    COMMENT_T=$((COMMENT_T + N))
  done
  REVIEW_T=0
  for CID in $(gh api --paginate "repos/$REPO/pulls/$PR_NUMBER/comments" --jq '.[].id' 2>/dev/null); do
    N=$(gh api "repos/$REPO/pulls/comments/$CID/reactions" --jq "[.[] | select(.content == \"+1\" and (.user.login | test(\"$CODEX_BOT\")))] | length" 2>/dev/null || echo 0)
    REVIEW_T=$((REVIEW_T + N))
  done
  echo $((PR_T + COMMENT_T + REVIEW_T))
}

THUMBS=$(count_thumbs)

# Check if Codex reviewed the latest commit
LATEST_SHA=$(gh api "repos/$REPO/pulls/$PR_NUMBER" --jq '.head.sha' 2>/dev/null || echo "")
CODEX_REVIEWED=$(gh api "repos/$REPO/pulls/$PR_NUMBER/reviews" --jq "[.[] | select(.user.login | test(\"$CODEX_BOT\") and .commit.oid == \"$LATEST_SHA\")] | length" 2>/dev/null || echo 0)

# ── 4. Decide next action based on Codex state ──────────────────────

if [[ "$THUMBS" -ge 1 ]]; then
  # Codex approved
  info "Codex approved! Enabling auto-merge."
  PR_URL=$(gh pr view "$PR_NUMBER" --json url --jq .url 2>/dev/null || echo "https://github.com/$REPO/pull/$PR_NUMBER")
  gh pr merge "$PR_URL" --auto --squash --delete-branch 2>&1 || true
  write_pipeline_state "completed" "merged"
  info "Pipeline complete. PR will merge once CI passes."
  exit 0

elif [[ "$CODEX_REVIEWED" -gt 0 ]] && [[ "$COMMENT_COUNT" -gt 5 ]]; then
  # Codex reviewed latest commit and left comments → need fixes
  info "Codex reviewed commit ${LATEST_SHA:0:8} and left comments."
  info "Comments saved to $STATE_DIR/codex_comments.txt"
  info "---"
  info "Fix ALL P0/P1 blocking issues, then re-run this script to push and re-trigger."

  write_pipeline_state "paused" "codex_rejected"
  exit 1

elif [[ "$CODEX_REVIEWED" -gt 0 ]] && [[ "$COMMENT_COUNT" -le 5 ]]; then
  # Codex reviewed but no comments and no 👍 — might be informational review
  # Trigger @codex review to get a definitive answer
  info "Codex reviewed but no clear signal. Triggering explicit review."
  gh pr comment "$PR_NUMBER" --repo "$REPO" --body "@codex review" 2>/dev/null || true
  info "Re-run this script later to check results."
  write_pipeline_state "paused" "waiting_codex"
  exit 0

else
  # Codex hasn't reviewed latest commit yet
  info "Codex has not yet reviewed commit ${LATEST_SHA:0:8}."
  info "The GitHub Auto Merge workflow will poll for results automatically."
  info "Check back later or re-run this script to check status."
  info "PR: https://github.com/$REPO/pull/$PR_NUMBER"

  write_pipeline_state "paused" "waiting_codex"
  exit 0
fi

# ── 4. Enable auto-merge ─────────────────────────────────────────────

info "Step 4: Codex approved. Enabling auto-merge."

write_pipeline_state "running" "auto_merge"

PR_URL=$(gh pr view "$PR_NUMBER" --json url --jq .url 2>/dev/null || echo "https://github.com/$REPO/pull/$PR_NUMBER")
gh pr merge "$PR_URL" --auto --squash --delete-branch 2>&1 || \
  info "Auto-merge already enabled or failed (CI may still be running)."

write_pipeline_state "completed" "merged"
info "Pipeline complete. PR #$PR_NUMBER will merge once CI passes."
