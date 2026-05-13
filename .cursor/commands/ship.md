# /ship — end-to-end change + verify + PR (manual steps where needed)

Use when the user invokes **`/ship <one-line request>`**. Execute the request in order; stop and report on failure.

## Conventions (this repo)

- **Host setup:** before the first `/ship` on a new machine, run `bash scripts/setup_agent_host.sh` if `gh auth status -h github.com` fails (non-interactive `gh` / git push).
- **Base branch for PRs**: **`dev`** (not `main`). Confirm default remote branch with `git remote show origin | sed -n '/HEAD branch/s/.*: //p'` if unsure.
- **Branch naming**: `feat/…`, `fix/…`, `refactor/…`, `docs/…`, `test/…`, `chore/…`, `perf/…` per `AGENTS.md`.
- **Verify**: `bash scripts/verify.sh` from repo root before commit/push.

## Steps

1. **Sync and branch**
   `git fetch origin` → create/checkout a new branch from latest **`origin/dev`** (or `origin/main` only if the team explicitly uses it for this task).

2. **Implement** the user’s one-line request with minimal, focused diffs.

3. **Verify**
   `bash scripts/verify.sh` — fix failures; do not remove checks from the script to “get green”.

4. **Commit**
   Clear message; do not include secrets or unrelated files.

5. **Push (non-interactive)**
   Keep the same shell session; avoid opening editors for commit messages if the agent can pass `-m`. Example:

   ```bash
   git push -u origin HEAD
   ```

   `scripts/verify.sh` exports **`GIT_TERMINAL_PROMPT=0`** so `git` does not hang waiting for HTTPS credentials in headless runs (configure SSH or a credential helper first).

6. **Open PR** targeting **`dev`** (fully non-interactive — do not rely on `$EDITOR`)

   ```bash
   export GH_PROMPT_DISABLED=1
   gh pr create \
     --base dev \
     --head "$(git branch --show-current)" \
     --title "feat: <short title>" \
     --body "## Summary
   …
   ## How to test / Test plan
   - \`bash scripts/verify.sh\`
   ## Risk
   Low / …
   ## Related
   …
   - [ ] I have read the CLA and I hereby sign it"
   ```

   Use a here-doc or `--body-file` if the body is long. Never run bare `gh pr create` without `--title`/`--body` in agent flows (may open an editor and stall). When using Cursor **Task** tools, request **`all`** (or `git_write` + `network` together) in **one** run for push + `gh` + installs so the IDE does not prompt per permission; do not abort the background agent mid-run.

7. **Automation you cannot do in-repo**
   Tell the user to enable **Allow auto-merge** / branch protection / required checks on GitHub if they use that workflow; you cannot click Settings for them.

8. **After merge**
   User may delete the branch; locally `git checkout dev && git pull` (or their usual sync).

## Hard stops

- No force-push to **`main`** / **`dev`** / shared release branches.
- No skipping `verify.sh` to rush a push.
- No editing teammates’ uncommitted work without confirmation.
