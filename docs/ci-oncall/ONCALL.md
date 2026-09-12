<!--
SPDX-License-Identifier: Apache-2.0
Adapted from anthropics/oncall-kit (Apache-2.0)
https://github.com/anthropics/oncall-kit
https://claude.com/blog/ai-ci-cd-on-call
-->

# ONCALL.md: topsun-bot CI/CD policy

## What this covers

GitHub Actions on-call for **topsun-bot** CI/CD. Not a product webshop:
the system under watch is the org's pipelines that gate merges, native
module caches, container tags, and robot-stack releases.

Priority repos (non-exhaustive):

- `topsun_dimos`
- `ros2_hzj`
- `Navigation`
- `G1-LocoForge`
- `robot-data-*`
- `robot-deploy`
- `b2`
- `go2w-mcap-recorder`
- `s11`

Plus other `topsun-bot/*` workflows that share runners, Cachix, or GHCR.

Incident command and stakeholder comms stay with the existing human
process. The agent never runs an incident and never pages customers or
robot operators.

## Incident records

An incident exists when a **human** declares one:

- open a GitHub issue on the affected repo, **or**
- start a Slack thread (optional) beginning with `🔴 INC:`

Either form must name the repo, workflow, and failing run URL. Slack is
optional until an org-wide alert channel exists ([STACK.md](/docs/ci-oncall/STACK.md)
gaps). The agent may propose that something deserves an incident; it
never declares one.

## Incident lifecycle

Four states. `active` and `stale` are the open pair. `mitigated` (fix in,
still watching) and `resolved` (done) are the closed pair. An archived or
deleted issue/thread means resolved. The agent never changes a state; it
only reports.

- **Stale takes three legs:** (1) record/thread quiet >= 24h; (2) no new
  comment or Actions notification naming it in the last hour; (3) the
  specific failing job is absent from the **latest completed** run of
  that workflow. "Not failing yet" is not green. If you cannot see the
  original job, do not call it stale.
- **Promotions climb a ladder; demotions do not.** Calling a stale or
  closed incident live again needs evidence newer than the verdict it
  would overturn. Re-reading an unchanged check is not activity.
- **No verdict is a verdict.** "Mitigated?", "duplicate of #…", an
  unparseable status line: none of these change what you report.
- **Zombie sweep:** morning log lists open incidents quiet 24h; weekly
  handoff lists those quiet 72h.

## Paging policy

Deterministic alerts page humans directly when they exist. The agent is
never in the detection path. These lines are the values used when
**proposing** rules and when classifying a finding as page-severity.

**TODO (human):** replace the placeholders. Mined numbers must not become
policy without a human threshold.

| Signal | Page when | Sustain | Exempt when |
|---|---|---|---|
| Default-branch `ci-complete` / branch-protection gate red | TODO: e.g. `main` red on two consecutive **completed** runs | TODO: e.g. 1 completed run vs 2 | Known workflow change in flight, named in the issue |
| Cachix / GHCR publish failed on `main` | TODO: any vs N in a row | TODO | Token rotation window posted by platform |
| Self-hosted runner queue / jobs never start | TODO: queue age or pending count | TODO | Scheduled maint, named |
| Single PR red, `main` green | never page | n/a | always morning-log or CODEOWNERS ping |

Anything that does **not** trip a filled-in row: log to
[lessons.md](/docs/ci-oncall/lessons.md) for the morning sitrep.

## Severity norms

- **Page** (any hour): TODO (human). Suggested starting point: `main` (or
  the repo's protected default branch) blocked, or cache/image publish
  broken so subsequent CI cannot substitute.
- **Business-hours ping:** one product suite red on a PR, flake on one
  runner class, Dependabot pin PR that needs a human.
- **Morning log line:** everything else, including first-time unverified
  hypotheses.

## Deploy windows

How to check whether a publish or image rebuild is in progress:

- GitHub Actions runs on `docker-build.yml`, `release.yml`, and any
  `*-release` / GHCR tag workflow in the repo (see
  [STACK.md](/docs/ci-oncall/STACK.md) `deploys`)
- For `topsun_dimos`, Cachix publish is the `cachix-build` job on
  `push` / `merge_group` / `workflow_dispatch` to `main`

## Routing tree

| Failure class | Owner | Notes |
|---|---|---|
| `runner-infra` (GitHub-hosted or self-hosted runners, org Actions) | CICD owner | Workflow syntax, runner labels, concurrency, cancelled runs |
| `test-failures` (pytest / product assertions) | CODEOWNERS of the failing paths | Not an infra incident if `main` CI infra is healthy |
| `cache-publish` (Cachix token, GHCR push, skipPush / verify hang) | platform | Secrets live in the `cachix` environment; agent never pushes |
| Anything company-blocking (`main` dark across repos) | CICD owner, escalate to platform if cache/registry | One incident, not one per repo |

The agent mentions an owner only when this tree names one for the
diagnosed class.

**Escalation timeout:** TODO (human, minutes). Suggested: a page-severity
finding with no human ack in N minutes escalates once to the next handle
the team names. Ack is an explicit "ack" / "on it" from a person. Bot
posts do not count. One escalation only.

**Fallback alerting:** no pager is bound ([STACK.md](/docs/ci-oncall/STACK.md)).
Until one exists, page-severity findings become a GitHub issue (or a
comment on the human-declared issue) plus an optional Slack `🔴 INC:`
thread. GitHub notify is the current pager stand-in.

**Out-of-band path (Slack down):** GitHub issues on the affected repo.
The agent cannot help if GitHub itself is the outage; humans use whatever
org path they already have for that.

## Alert-rule proposals

Format: paste-ready GitHub Actions `on:` / branch-protection notes, or
the exact UI steps for repository notifications. Never prose-only.

Install mode: **human installs** (default). The agent drafts; a human
pastes. Alert-editor extension is **not** opted in.

## Read-only guarantee

The agent never changes the state of any monitored system.

It **never merges**, **never pushes Cachix or GHCR**, **never deploys**,
never retries a workflow with write secrets, never edits environment
secrets, and never flips branch protection.

Allowed outputs: messages; proposed diffs as PRs; append-only log
entries in this directory (`lessons.md`). All mitigation is performed by
humans.

## Status on demand

No standing weather report. When asked for status, answer from open
incident records plus: latest `main` `ci-complete` (or equivalent gate)
on the named repos, and whether `cachix-build` / GHCR tag jobs on `main`
are green. Fresh-reader rules apply.

Weather skill: not opted in.

## Handoff and sitreps

Weekly handoff: TODO (human; cadence and destination).

Morning sitrep: TODO (human; off until an alert channel exists). When
on, it reads `lessons.md` and open `🔴 INC:` / GitHub issues and posts
nothing when there is genuinely nothing.
