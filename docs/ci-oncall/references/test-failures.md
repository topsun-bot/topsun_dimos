<!--
SPDX-License-Identifier: Apache-2.0
Adapted from anthropics/oncall-kit (Apache-2.0)
https://github.com/anthropics/oncall-kit/blob/main/skills/triage/references/test-failures.md
https://claude.com/blog/ai-ci-cd-on-call
-->

# Triage reference: test-failures

Seed for topsun-bot GitHub Actions. Rows without a real incident are
**unverified**. Promote into certainty only via [lessons.md](/docs/ci-oncall/lessons.md).

## Symptoms

- Pytest (or a sibling suite) red on a PR or on `main`
- Branch-protection / `ci-complete` red while a product job failed
- Tests that should run silently not running (executed-count drop, green gate)
- Green locally, red in Actions (or the reverse)
- ROS / webrtc / pin-related import or container failures that show up as
  test errors

## First checks (stop when the timeline explains the symptom)

1. **Scope it.** One job, one Python version, one OS, or the whole
   matrix? One test node or many? Open the failed Actions run, not the
   PR conversation summary.
   `(source: {{logs}} = the failing job log)`
2. **Gate vs product.** On `topsun_dimos`, `ci-complete` is the
   branch-protection aggregator (`alls-green` over lint/rust/native/
   md-babel/web/tests/self-hosted). A red gate with a red `tests` job is
   a product failure. A red gate with skipped `tests` because
   `cachix-build` failed is **not** this class; switch to
   [cache-publish](/docs/ci-oncall/references/cache-publish.md).
3. **Timeline.** What merged or Dependabot-bumped in the preceding
   window: `uv.lock`, `pyproject.toml` extras (`webrtc`, `unitree`),
   `docker/ros-dev-pin/Dockerfile` digest, workflow `if:`?
   `(sources: {{code}}, {{deploys}})`
4. **Read one failure honestly.** Full log, including the "re-run
   failing tests with maximum verbosity" step when present. OOM,
   timeout, image pull, and `set -e` script traps masquerade as pytest
   failures.
5. **Silent-skip check.** If the report is "tests not running": compare
   collected/executed count to the last green run of the same job.

## Correlation table

Mined "if you see X and Y, it means Z" rows. **unverified** until a
real incident is logged.

| If you see | And | It usually means | Then |
|---|---|---|---|
| `ci-complete` / protection gate red | `tests` (or the repo's pytest job) failed; Cachix jobs green | product regression or flake; **pytest gate** | bisect the failing node; route to CODEOWNERS; propose a test fix or skip-with-expiry. *(unverified)* |
| Import / resolution errors for `aiortc`, `aiohttp`, `unitree_webrtc_connect`, or ROS Python bits | `pyproject.toml` / `uv.lock` pin moved, or `ros-dev` digest in `docker/ros-dev-pin/Dockerfile` moved | **ROS / webrtc dep pin** drift, not a logic bug | propose pinning the last green extra/digest; do not "just bump". *(unverified)* |
| Job dies in a `run:` bash step with no pytest traceback | script uses `set -e` / `set -euo pipefail`, or `cmd && exit 1` after a re-run | **`set -e` bash trap**: early exit, pipefail, or inverted `&& exit 1` leaving the job red even after a pass | read the exact step; propose a script fix; do not retry-for-luck. *(unverified)* |
| Failures across unrelated PRs at once | runner image, shared container, or lockfile change in the window | environment regression | pin previous image/digest; see also [runner-infra](/docs/ci-oncall/references/runner-infra.md). *(unverified)* |
| Green locally, red in CI | test reads wall-clock, network, GPU, LFS, or ROS | hermeticity | route to test owner via CODEOWNERS; not infra. *(unverified)* |
| Executed-test count dropped | marker / `addopts` / `-m` filter changed | skip/filter over-match | propose revert of the filter; verify collected count. *(unverified)* |

## Known causes

Check [lessons.md](/docs/ci-oncall/lessons.md) for `#test-failures`
before theorizing.

## Escalation

- Broad + blocking `main` merges -> page-severity per
  [ONCALL.md](/docs/ci-oncall/ONCALL.md) (human thresholds still TODO)
- Single-test flake on one PR -> business-hours ping to CODEOWNERS
- Expected resolution for a pin revert: one completed CI run. If the
  same node still fails on the pinned extra/digest, the pin was not the
  cause.

## Propose only

Do not merge the revert, do not push a lockfile, do not deploy an image.
