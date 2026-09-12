<!--
SPDX-License-Identifier: Apache-2.0
Adapted from anthropics/oncall-kit (Apache-2.0)
https://github.com/anthropics/oncall-kit
https://claude.com/blog/ai-ci-cd-on-call
-->

# STACK.md: capability map

Generated: 2026-09-12 · Channel: none (docs-only seed) · Probed by: human/agent seed, not oncall-setup

| Capability | Bound to | Probe result | Notes |
|---|---|---|---|
| `code` | GitHub `topsun-bot/*` | read PRs, Actions YAML, CODEOWNERS | Start with `topsun_dimos`; same pattern on priority repos in [ONCALL.md](/docs/ci-oncall/ONCALL.md) |
| `metrics` | none | gap | No Datadog / Grafana / Prometheus org-wide. Do not invent dashboards. |
| `logs` | GitHub Actions run logs (primary) | read job logs + annotations on the failing run | Job log URL is the evidence link. No central log store. |
| `pager` | TBD / GitHub notify | not tested | Repository or org notification on failed `main` runs. No PagerDuty. |
| `alert-channels` | none | gap | No org-wide CI alert Slack channel yet. |
| `incidents` | GitHub issues, optional Slack `🔴 INC:` thread | read + comment when asked | Human declares; see [ONCALL.md](/docs/ci-oncall/ONCALL.md) |
| `deploys` | GHCR tag workflows / `release.yml` where present | read workflow runs | `topsun_dimos`: [`.github/workflows/docker-build.yml`](/.github/workflows/docker-build.yml) (`ghcr.io/topsun-bot/*`), [`.github/workflows/release.yml`](/.github/workflows/release.yml). Other robot repos: look for the same basenames. |
| `flags` | n/a | n/a | Most robot / CI repos have no feature-flag service. Treat workflow `if:` and environment gates as config, not flags. |

## Gaps

- No Slack Claude Tag (no channel memory, no scheduled routines).
- `CACHIX_AUTH_TOKEN` lives in the GitHub Actions **`cachix` environment**
  (not a repo-level Actions secret by default). Missing/empty token on a
  publish path logs `Pushing is disabled.` See
  [cache-publish](/docs/ci-oncall/references/cache-publish.md) and
  [topsun_dimos #135](https://github.com/topsun-bot/topsun_dimos/pull/135).
- No org-wide alert channel.
- No Datadog / equivalent metrics. Timeline is Actions run history + git
  history.

## Access posture

All bindings are read-only. The kit does not carry write credentials to
Cachix, GHCR, or GitHub environments. The agent never pushes cache and
never deploys. Alert-editor extension is not opted in.
