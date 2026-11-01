<!--
SPDX-License-Identifier: Apache-2.0
Adapted from anthropics/oncall-kit reference structure (Apache-2.0)
https://github.com/anthropics/oncall-kit
https://claude.com/blog/ai-ci-cd-on-call
New failure class for topsun-bot Cachix / GHCR.
-->

# Triage reference: cache-publish

Cachix binary cache and GHCR image/tag publishes. The agent **proposes**
fixes only. It never authenticates to Cachix, never pushes store paths,
and never `docker push` / `packages: write`.

## Symptoms

- Job log contains `Pushing is disabled.`
- `bin/build-native-modules --verify-published` (or equivalent) waits on
  store paths that never appear; job times out or stays red
- `cachix-build` red; downstream `tests` skipped; `fail-fast` /
  `ci-complete` red
- GHCR tag workflow / `docker-build.yml` cannot push
  `ghcr.io/topsun-bot/...`

## First checks (in order)

1. **Which event?** `pull_request` vs `push` to default branch vs
   `merge_group` vs `workflow_dispatch`. Publish is a **main / trusted**
   path. A PR compile gate should not need a write token.
   Pattern: [topsun_dimos #135](https://github.com/topsun-bot/topsun_dimos/pull/135)
   (`skipPush` on same-repo PR; publish + `--verify-published` on `main`).
   That workflow change is a **separate** PR; this file only documents
   the class.
2. **Token location.** For `topsun_dimos`, `CACHIX_AUTH_TOKEN` is
   consumed from the GitHub Actions **`cachix` environment** (job
   `environment: cachix`), not assumed as a repo-level secret. Confirm
   the environment exists, the secret is set, and the run was allowed
   into that environment. Do not print the token.
   `(source: {{code}} = `.github/workflows/ci.yml` job `cachix-build`)`
3. **Did anything get uploaded?** If the log says `Pushing is disabled.`
   then `--verify-published` will hang or wait until timeout: the paths
   were never pushed. Treat verify-hang as a **missing push**, not a
   slow cache.
4. **Fork vs same-repo.** `cachix-build` must not run on fork
   `pull_request` (write token). A skip on a fork is expected, not an
   incident.
5. **GHCR path.** If the failure is `docker-build` / release image:
   check `packages: write`, the GHCR package permissions, and whether
   the tag job ran on `main` (or the documented tag workflow). Same
   propose-only rule.

## Correlation table

| If you see | And | It usually means | Then (propose only) |
|---|---|---|---|
| `Pushing is disabled.` | `pull_request` and the job still calls verify-published | PR path is using the publish recipe; token absent or `skipPush` not set | Propose the #135 split: build on PR, `skipPush: true`, skip verify and publish marker. Do not paste a token into the PR. *(pattern: #135; treat new repos as unverified until seen)* |
| `Pushing is disabled.` | `push` to `main` / `merge_group` / `workflow_dispatch` | `CACHIX_AUTH_TOKEN` missing/empty in the `cachix` environment | Route to **platform**. Propose setting the environment secret. Agent does not set it. *(unverified until a logged incident)* |
| Verify-published hang / "N paths waiting" | log already said pushing disabled, or no upload lines | verify is polling a cache that was never written | Stop waiting; fix the push path first. Do not "retry verify". *(unverified)* |
| GHCR `denied` / `unauthorized` | tag workflow on `main` | package permission or `packages: write` missing | Route to platform; propose permission fix. Never docker login as the agent. *(unverified)* |
| Marker cache hit, `needs-build=false`, later jobs miss store paths | inputs-hash marker saved without a real publish | marker means "published" but PR/main split was wrong | Propose not saving the marker on PR / skipPush runs (#135). *(unverified)* |

## Known causes

Check [lessons.md](/docs/ci-oncall/lessons.md) for `#cache-publish`.

## Escalation

- `main` cannot publish, subsequent CI cannot substitute -> page-severity
  per [ONCALL.md](/docs/ci-oncall/ONCALL.md) (human thresholds TODO);
  owner is **platform**
- Single PR red only because verify ran without a push -> morning-log /
  CICD owner; not a token leak hunt
- Expected resolution after a human sets the token or merges the skipPush
  split: one completed `main` publish + one PR compile that does not
  call verify

## Hard stops

No `cachix push`, no writing `CACHIX_AUTH_TOKEN`, no GHCR upload, no
merge of the fix PR.
