# GitHub SSH and PR CI/CD Workflow

This document records the end-to-end workflow used to pull this repository, configure GitHub SSH access, create a test branch, push it, and open a pull request to trigger CI/CD.

## Context

- Repository: `topsun-bot/topsun_dimos`
- Local directory: `/home/lenovo/桌面/dimos`
- Base branch: `dev`
- Test branch: `dimos-ycy`
- GitHub account used for the final SSH and commit flow: `Yaocheng-yan`
- Pull request created: `https://github.com/topsun-bot/topsun_dimos/pull/14`

## 1. Clone the Repository

The current directory was empty, so the repository was cloned directly into it:

```bash
git clone "https://github.com/topsun-bot/topsun_dimos.git" .
```

During checkout, Git LFS failed to download one object:

```text
Object does not exist on the server: [404] Object does not exist on the server
fatal: assets/dimensional.command-center-extension-0.0.1.foxe: smudge filter lfs failed
```

The clone itself succeeded, but the working tree checkout was incomplete. To recover the source checkout without downloading LFS content, checkout was retried with LFS smudge disabled:

```bash
GIT_LFS_SKIP_SMUDGE=1 git checkout -f HEAD
```

After that, the repository was clean and on the `dev` branch:

```text
Your branch is up to date with 'origin/dev'.
```

## 2. Check GitHub and SSH Status

The repository remote initially used HTTPS:

```bash
git remote -v
```

Result:

```text
origin  https://github.com/topsun-bot/topsun_dimos.git (fetch)
origin  https://github.com/topsun-bot/topsun_dimos.git (push)
```

GitHub CLI showed two logged-in accounts, with `Yaocheng-yan` active:

```bash
gh auth status
```

SSH authentication was tested with:

```bash
ssh -T git@github.com
```

At first, SSH authenticated as `xidikr`, which meant the machine had a working GitHub SSH key, but it was associated with the wrong GitHub account for this workflow.

## 3. Configure SSH for the Correct GitHub Account

The existing SSH config used `id_ed25519_github` for `github.com`:

```sshconfig
Host github.com
    HostName ssh.github.com
    Port 443
    User git
    IdentityFile ~/.ssh/id_ed25519_github
    IdentitiesOnly yes
    ForwardAgent yes
```

A dedicated key was generated for `Yaocheng-yan`:

```bash
ssh-keygen -t ed25519 -C "Yaocheng-yan@github" -f "$HOME/.ssh/id_ed25519_github_yaocheng_yan" -N ""
```

Adding the key initially failed because the GitHub CLI token did not have the `admin:public_key` scope:

```text
This API operation needs the "admin:public_key" scope.
```

The token scope was refreshed through GitHub's device authorization flow:

```bash
gh auth refresh -h github.com -s admin:public_key
```

After authorization, the public key was added to GitHub:

```bash
gh ssh-key add "$HOME/.ssh/id_ed25519_github_yaocheng_yan.pub" --title "lenovo ThinkBook dimos Yaocheng-yan"
```

Then `~/.ssh/config` was updated to use the new key:

```sshconfig
Host github.com
    HostName ssh.github.com
    Port 443
    User git
    IdentityFile ~/.ssh/id_ed25519_github_yaocheng_yan
    IdentitiesOnly yes
    ForwardAgent yes
```

SSH was tested again:

```bash
ssh -T git@github.com
```

Expected result:

```text
Hi Yaocheng-yan! You've successfully authenticated, but GitHub does not provide shell access.
```

This confirms that SSH authentication is working for the intended GitHub account.

## 4. Configure Git Commit Identity

Git refused to commit until `user.name` and `user.email` were configured:

```text
Author identity unknown
fatal: unable to auto-detect email address
```

The final flow used a real configured Git identity instead of temporary command-line identity overrides:

```bash
git config --global user.name "Yaocheng-yan"
git config --global user.email "282550715+Yaocheng-yan@users.noreply.github.com"
```

The configuration was verified with:

```bash
git config --get user.name
git config --get user.email
```

Expected result:

```text
Yaocheng-yan
282550715+Yaocheng-yan@users.noreply.github.com
```

## 5. Create the Test Branch

A new local branch was created from `dev`:

```bash
git switch -c "dimos-ycy"
```

The branch was verified with:

```bash
git branch --show-current
```

Expected result:

```text
dimos-ycy
```

At this point, the branch existed only locally. It was not visible on GitHub until it was pushed.

## 6. Make a Small README Change

To trigger the PR CI/CD flow, a harmless HTML comment was added near the top of `README.md`:

```html
<!-- CI/CD PR flow test comment. -->
```

The diff was checked with:

```bash
git diff -- README.md
```

The change was intentionally small and documentation-only.

## 7. Switch the Repository Remote to SSH

The repository remote originally used HTTPS. Since SSH was configured and verified, the remote was switched to SSH:

```bash
git remote set-url origin git@github.com:topsun-bot/topsun_dimos.git
```

This allows future `git push` and `git pull` operations to use the configured SSH key.

## 8. Commit and Push the Branch

The README change was staged and committed:

```bash
git add README.md
git commit -m "test: trigger PR CI flow"
```

Then the branch was pushed and linked to the remote branch:

```bash
git push -u origin HEAD
```

Successful result:

```text
[dimos-ycy 3a8cc162] test: trigger PR CI flow
1 file changed, 1 insertion(+)
* [new branch] HEAD -> dimos-ycy
Branch 'dimos-ycy' set up to track remote branch 'dimos-ycy' from 'origin'.
```

After this step, `dimos-ycy` became visible on GitHub.

## 9. Create the Pull Request

The pull request was created against `dev`:

```bash
gh pr create \
  --base dev \
  --head dimos-ycy \
  --title "test: trigger PR CI flow" \
  --body "$(cat <<'EOF'
## Summary
- Add a harmless README comment to test the PR CI/CD flow.

## Test plan
- PR creation should trigger the configured GitHub Actions workflows.

EOF
)"
```

Created PR:

```text
https://github.com/topsun-bot/topsun_dimos/pull/14
```

## Troubleshooting Notes

### Git LFS checkout failed

If clone succeeds but checkout fails because an LFS object is missing from the server, recover the working tree without downloading LFS objects:

```bash
GIT_LFS_SKIP_SMUDGE=1 git checkout -f HEAD
```

This leaves LFS-managed files as pointer files or avoids downloading missing binary content, while allowing normal source files to be checked out.

### Branch exists locally but not on GitHub

A local branch is not visible on GitHub until pushed:

```bash
git push -u origin HEAD
```

### SSH works, but shows the wrong GitHub user

Run:

```bash
ssh -T git@github.com
```

If the greeting shows the wrong account, check `~/.ssh/config` and ensure `IdentityFile` points to the key added to the intended GitHub account.

### GitHub CLI cannot add SSH keys

If `gh ssh-key add` fails with a missing `admin:public_key` scope, refresh the token:

```bash
gh auth refresh -h github.com -s admin:public_key
```

Then retry:

```bash
gh ssh-key add "$HOME/.ssh/id_ed25519_github_yaocheng_yan.pub" --title "lenovo ThinkBook dimos Yaocheng-yan"
```

### Git cannot commit because identity is missing

Configure a real Git identity before committing:

```bash
git config --global user.name "Yaocheng-yan"
git config --global user.email "282550715+Yaocheng-yan@users.noreply.github.com"
```

Avoid temporary author overrides when the goal is to validate the normal developer workflow.

## Final State

- SSH authentication works as `Yaocheng-yan`.
- The repository remote uses SSH.
- Git commit identity is configured globally.
- Branch `dimos-ycy` exists locally and on GitHub.
- Commit `3a8cc162` was pushed.
- PR `#14` was created to test CI/CD.
