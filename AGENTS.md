# Codex Review Configuration

## Severity Definitions

| Level | Meaning | Blocks Merge |
|-------|---------|-------------|
| **P0** | Security vulnerability, data corruption, crash | ✅ Yes |
| **P1** | Incorrect logic, resource leak, concurrency bug | ✅ Yes |
| **P2** | Maintainability issue, reliability concern, performance with user impact | ⚠️ Only if impacts correctness/security/stability |
| **P3** | Style, readability, naming preference | ❌ No |
| **nit** | Trivial suggestion | ❌ No |

## Review Rules

- Only report issues that materially affect **correctness, security, or reliability**.
- **Ignore style-only suggestions** — naming preferences, formatting, import order.
- **P2 issues are non-blocking** unless they impact correctness, security, or stability.
- Do NOT comment on naming preferences or subjective refactors.

## Merge Guidance

If no correctness, security, or reliability issue exists:
- Explicitly state **"LGTM"**
- Explicitly state **"Safe to merge"**
- Add a 👍 reaction to the PR
- Avoid blocking comments on non-essential findings
