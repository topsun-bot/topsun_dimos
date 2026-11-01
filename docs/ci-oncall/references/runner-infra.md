<!--
SPDX-License-Identifier: Apache-2.0
Adapted from anthropics/oncall-kit (Apache-2.0)
https://github.com/anthropics/oncall-kit/blob/main/skills/triage/references/runner-infra.md
https://claude.com/blog/ai-ci-cd-on-call
-->

# Triage reference: runner-infra

Short CI default for topsun-bot. GitHub-hosted and self-hosted Actions
runners. Not the fictional webshop scheduler.

## Symptoms

- Jobs queued, never start; pending age climbing
- Jobs start then die (OOM, disk, network, lost runner)
- One label unhealthy (`ubuntu-latest`, `ubuntu-24.04-arm`,
  `self-hosted` + `Linux` + `base`, `self-hosted` + `Linux` + `large`)
  while others are fine
- Org-wide Actions outage or hosted-runner stockout

## First checks (in order)

1. **Scope by runner class.** All classes or one? One class points at
   that image, label, or machine; all classes points at GitHub Actions
   or org runner groups.
   `(source: {{logs}} = queue / setup steps; GitHub status if hosted)`
2. **Hosted vs self-hosted.** `ubuntu-latest` / `ubuntu-24.04-arm` are
   GitHub-hosted. ROS / `self_hosted` pytest on `topsun_dimos` uses
   `[self-hosted, Linux, base]` (often a ros-dev container). Large suite
   uses `[self-hosted, Linux, large]`.
3. **Timeline.** Runner image updates, workflow `runs-on:` changes,
   concurrency groups that cancel in-progress runs, disk-clean steps.
   `(sources: {{code}} workflow diffs)`
4. **Read one dead job honestly.** Full log. OOM and disk-full show up
   as pytest or as "runner flakiness". `fail-fast` cancelling siblings
   is a consequence, not a second incident.

## Correlation table

| If you see | And | It usually means | Then |
|---|---|---|---|
| Pending forever on `self-hosted` | other hosted jobs run | offline or saturated org runner | CICD owner; check runner is idle and labeled. *(unverified)* |
| Job dies at a consistent wall time | cleanup cron / disk prune on the runner | GC starving live jobs | propose rescheduling the prune. *(unverified)* |
| Hosted `ubuntu-*` queued org-wide | GitHub status incident | provider stockout / outage | communicate; paging will not create machines. *(unverified)* |
| OOM in ros-dev / 6g container | memory options on the self-hosted job | container ceiling, not a test bug | propose memory/label change; route CICD. *(unverified)* |
| Many jobs cancelled together | `fail-fast` or `concurrency: cancel-in-progress` | one real failure plus cancellations | diagnose the first failure; do not treat cancels as flakes. *(unverified)* |

## Known causes

Check [lessons.md](/docs/ci-oncall/lessons.md) for `#runner-infra`.

## Escalation

- All classes down or `main` cannot start jobs -> page-severity per
  [ONCALL.md](/docs/ci-oncall/ONCALL.md); owner is **CICD owner**
- Provider stockout -> business-hours ping + status; do not page in a
  loop
- Expected resolution for a runner restart: one completed job on that
  label. If it still never starts, the runner was not the cause.

## Propose only

Do not SSH to runners, do not re-register them, do not change org runner
groups.
