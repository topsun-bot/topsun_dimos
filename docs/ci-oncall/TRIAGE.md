<!--
SPDX-License-Identifier: Apache-2.0
Adapted from anthropics/oncall-kit triage skill (Apache-2.0)
https://github.com/anthropics/oncall-kit/blob/main/skills/triage/SKILL.md
https://claude.com/blog/ai-ci-cd-on-call
-->

# TRIAGE.md: CI first pass

Condensed from the upstream triage skill. Standing contract: propose,
do not act; every claim carries a link; data before theory; log to
[lessons.md](/docs/ci-oncall/lessons.md) without asking.

## Procedure

1. **Load context.** Read [ONCALL.md](/docs/ci-oncall/ONCALL.md),
   [STACK.md](/docs/ci-oncall/STACK.md), and
   [lessons.md](/docs/ci-oncall/lessons.md) from disk.

2. **Classify** and load the matching file in `references/`:

   | Symptom looks like | Load |
   |---|---|
   | Tests failing, flaking, or silently not running; pytest / `ci-complete` red | [references/test-failures.md](/docs/ci-oncall/references/test-failures.md) |
   | `Pushing is disabled`, Cachix verify hang, GHCR tag/push failure | [references/cache-publish.md](/docs/ci-oncall/references/cache-publish.md) |
   | Jobs not starting, agents stuck, disk/OOM, runner labels, capacity | [references/runner-infra.md](/docs/ci-oncall/references/runner-infra.md) |
   | None of the above | No reference. Say so. Timeline first (merges, workflow diffs, pins), then blast radius. |

   If the table and the directory disagree, trust the directory and flag
   the drift.

3. **Check the log first.** Search `lessons.md` for this class's `#tag`
   only.

4. **Correlate.** Same window on sibling jobs/repos: one Cachix outage
   can look like five pytest failures. Batch shared-cause alerts into
   one diagnosis. Unrelated failures stay separate.

5. **Run first checks** in the reference against `STACK.md` bindings
   (usually the failed Actions run). Establish onset and what changed
   (workflow YAML, lockfile, image digest, secrets).

6. **Cross-check blame against what is red now.** A PR that *started* a
   failure is the live cause only if the latest **completed** run still
   shows the symptom.

7. **Post the diagnosis**

   > **What's happening:** one sentence a newcomer can use.
   > **Root cause (confidence high/medium/low):** mechanism, each claim linked.
   > **Blast radius:** which repos, workflows, and gates.
   > **Proposed fix:** the action, why it is safe, what to watch. Not executed.
   > **Ruled out:** alternatives and the evidence that killed them.
   > **Would change my mind:** the one observation that would.

8. **Route** only if [ONCALL.md](/docs/ci-oncall/ONCALL.md) names an
   owner for this class.

9. **When a human deploys a fix:** re-check the original failing job on
   a completed run (half / full / double the reference's expected
   window, three checks). Do not mark resolved.

10. **Afterwards:** append `lessons.md`. If the reference was wrong,
    propose a playbook PR. Ask: would a rule have caught this earlier?
    Draft a paste-ready rule; a human installs it.

## Hard stops

The agent never merges, never pushes Cachix or GHCR, never deploys, and
never retries a job that has write access to `CACHIX_AUTH_TOKEN` or
`packages: write`.
