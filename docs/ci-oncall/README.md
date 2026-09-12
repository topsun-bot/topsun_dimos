<!--
SPDX-License-Identifier: Apache-2.0
Adapted from anthropics/oncall-kit (Apache-2.0)
https://github.com/anthropics/oncall-kit
https://claude.com/blog/ai-ci-cd-on-call
-->

# CI on-call for topsun-bot

Claude/agent-assisted first response for **GitHub Actions** failures across
the topsun-bot org. This directory is the local playbook: policy, capability
map, incident log, and triage references.

The agent is **read-only**. It investigates, classifies, and proposes. A
human decides, merges, publishes cache, and deploys. There is no Slack
Claude Tag yet; until that exists, a human pastes a red CI into an issue
or chat and the agent works from this tree.

## Upstream and local SDLC

- Kit: [anthropics/oncall-kit](https://github.com/anthropics/oncall-kit)
  (Apache-2.0)
- Write-up: [Claude on call for CI/CD](https://claude.com/blog/ai-ci-cd-on-call)
- Local AI-native SDLC: agents may draft and diagnose; a human must
  understand and ship. See
  [AGENTS.md](https://github.com/topsun-bot/topsun_dimos/blob/main/AGENTS.md),
  [AI_POLICY.md](https://github.com/topsun-bot/topsun_dimos/blob/main/AI_POLICY.md),
  [CONTRIBUTING.md](https://github.com/topsun-bot/topsun_dimos/blob/main/CONTRIBUTING.md),
  and [coding-agent docs](/docs/coding-agents/index.md).

This directory is documentation only. It does not change
[`.github/workflows/ci.yml`](/.github/workflows/ci.yml). Cachix
publish-on-PR vs publish-on-main is a separate change
([topsun_dimos #135](https://github.com/topsun-bot/topsun_dimos/pull/135)).

## Files

| File | Role |
|---|---|
| [ONCALL.md](/docs/ci-oncall/ONCALL.md) | Standing policy: declare, page vs morning-log, routing |
| [STACK.md](/docs/ci-oncall/STACK.md) | Capability map (code, logs, pager, deploys, flags) |
| [TRIAGE.md](/docs/ci-oncall/TRIAGE.md) | Condensed triage procedure |
| [lessons.md](/docs/ci-oncall/lessons.md) | Append-only incident log |
| [references/test-failures.md](/docs/ci-oncall/references/test-failures.md) | Product / pytest class |
| [references/cache-publish.md](/docs/ci-oncall/references/cache-publish.md) | Cachix / GHCR publish class |
| [references/runner-infra.md](/docs/ci-oncall/references/runner-infra.md) | Runner / capacity class |

## How an agent should use this on the next red CI

1. Load [ONCALL.md](/docs/ci-oncall/ONCALL.md), [STACK.md](/docs/ci-oncall/STACK.md),
   and [lessons.md](/docs/ci-oncall/lessons.md) from disk. Files beat memory.
2. Classify the symptom using [TRIAGE.md](/docs/ci-oncall/TRIAGE.md) and load
   the matching `references/*.md`. If none match, say so and start from
   timeline (what changed), then blast radius.
3. Search `lessons.md` for that class's `#tag` only. A matching past
   incident is the first hypothesis.
4. Run the reference's first checks against the bindings in `STACK.md`
   (usually the failed GitHub Actions run log). Every claim needs a link.
5. Post a diagnosis: what is happening, root cause + confidence, blast
   radius, **proposed** fix, ruled-out alternatives. Do not execute the
   fix.
6. Route only if `ONCALL.md`'s tree names an owner for that class.
7. After a human lands a fix, append `lessons.md`. If the playbook was
   wrong or thin, propose a docs PR. Never merge, never push Cachix/GHCR,
   never deploy.
