#!/usr/bin/env bash
# Sync a local Markdown file to a Feishu Wiki child page via lark-cli.
#
# Parent wiki: https://topsunhzj.feishu.cn/wiki/A3tiwPixSi7MyfkYXvNcLQLtn0e
#
# Usage:
#   bash scripts/sync_feishu_wiki_doc.sh
#   bash scripts/sync_feishu_wiki_doc.sh --file docs/development/spatial_memory_architecture.md
#   bash scripts/sync_feishu_wiki_doc.sh --dry-run
#
# Environment:
#   FEISHU_WIKI_PARENT_TOKEN  override --parent-token
#   FEISHU_WIKI_SPACE_ID      override --space-id
#   LARK_CLI_NO_PROXY=1       disable HTTP proxy for lark-cli (recommended if proxy breaks DNS)
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

DEFAULT_FILE="docs/development/spatial_memory_architecture.md"
DEFAULT_PARENT_TOKEN="A3tiwPixSi7MyfkYXvNcLQLtn0e"
DEFAULT_SPACE_ID="7640022585258036421"

FILE="$DEFAULT_FILE"
PARENT_TOKEN="${FEISHU_WIKI_PARENT_TOKEN:-$DEFAULT_PARENT_TOKEN}"
SPACE_ID="${FEISHU_WIKI_SPACE_ID:-$DEFAULT_SPACE_ID}"
TITLE=""
DRY_RUN=0

usage() {
    cat <<'EOF'
Usage: sync_feishu_wiki_doc.sh [OPTIONS]

Options:
  --file PATH           Local Markdown file (default: docs/development/spatial_memory_architecture.md)
  --parent-token TOKEN  Parent wiki node token (default: A3tiwPixSi7MyfkYXvNcLQLtn0e)
  --space-id ID         Wiki space ID (default: 7640022585258036421)
  --title TITLE         Child page title for lookup; default: first "# ..." line in --file
  --dry-run             Print lark-cli requests without executing
  -h, --help            Show this help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --file)
            FILE="$2"
            shift 2
            ;;
        --parent-token)
            PARENT_TOKEN="$2"
            shift 2
            ;;
        --space-id)
            SPACE_ID="$2"
            shift 2
            ;;
        --title)
            TITLE="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

if ! command -v lark-cli >/dev/null 2>&1; then
    echo "Error: lark-cli not found. Install: https://github.com/larksuite/cli" >&2
    exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
    echo "Error: jq not found (required to match wiki child nodes by title)" >&2
    exit 1
fi

if [[ ! -f "$FILE" ]]; then
    echo "Error: file not found: $FILE" >&2
    exit 1
fi

if [[ -z "$TITLE" ]]; then
    TITLE="$(grep -m1 '^# ' "$FILE" | sed 's/^# //')"
fi
if [[ -z "$TITLE" ]]; then
    echo "Error: could not derive title from $FILE; pass --title" >&2
    exit 1
fi

LARK_ARGS=()
if [[ "$DRY_RUN" -eq 1 ]]; then
    LARK_ARGS+=(--dry-run)
fi

echo "Syncing: $FILE"
echo "  title:         $TITLE"
echo "  parent_token:  $PARENT_TOKEN"
echo "  space_id:      $SPACE_ID"

find_obj_token() {
    local raw json
    # Always perform a real read; --dry-run applies only to create/update writes.
    raw="$(lark-cli wiki +node-list \
        --space-id "$SPACE_ID" \
        --parent-node-token "$PARENT_TOKEN" 2>/dev/null || true)"
    json="$(printf '%s\n' "$raw" | awk '/^{/{found=1} found')"
    if [[ -z "$json" ]]; then
        return 0
    fi
    jq -r --arg title "$TITLE" \
        '.data.nodes[]? | select(.title == $title) | .obj_token' <<<"$json" | head -1
}

OBJ_TOKEN="$(find_obj_token)"

if [[ -z "$OBJ_TOKEN" ]]; then
    echo "Creating new wiki child document..."
    RESULT="$(lark-cli docs +create \
        "${LARK_ARGS[@]}" \
        --api-version v2 \
        --content "@$FILE" \
        --doc-format markdown \
        --parent-token "$PARENT_TOKEN")"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "$RESULT"
        exit 0
    fi
    DOC_URL="$(echo "$RESULT" | jq -r '.data.document.url // empty')"
    DOC_ID="$(echo "$RESULT" | jq -r '.data.document.document_id // empty')"
    if [[ -z "$DOC_URL" ]]; then
        echo "Error: create succeeded but no document URL in response" >&2
        echo "$RESULT" >&2
        exit 1
    fi
    echo "Created: $DOC_URL"
    echo "document_id: $DOC_ID"
else
    echo "Updating existing document (obj_token: $OBJ_TOKEN)..."
    RESULT="$(lark-cli docs +update \
        "${LARK_ARGS[@]}" \
        --api-version v2 \
        --doc "$OBJ_TOKEN" \
        --content "@$FILE" \
        --doc-format markdown \
        --command overwrite)"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "$RESULT"
        exit 0
    fi
    DOC_URL="https://topsunhzj.feishu.cn/docx/$OBJ_TOKEN"
    echo "Updated: $DOC_URL"
fi

WIKI_URL="https://topsunhzj.feishu.cn/wiki/$PARENT_TOKEN"
echo "Parent wiki: $WIKI_URL"
