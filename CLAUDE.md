# Claude Code instructions

This repository's agent rules live in [AGENTS.md](AGENTS.md). Read that file before you edit anything.

The **Agent PR workflow (mandatory)** section is the working contract for this fork (`topsun-bot/topsun_dimos`):

- one concern per PR and per commit; at most 400 changed lines or 15 files (lockfiles and generated files do not count)
- split larger work (for example weekly upstream syncs of about 10-15 commits)
- when `main` is red, only a PR that fixes `main` may be opened or marked ready
- at most 3 open agent-authored PRs per person
- branch from latest `origin/main` and rebase before opening a PR
- run `uv run mypy`, `SKIP=cargo-fmt,cargo-clippy pre-commit run --all-files`, and `./bin/pytest-fast` before every commit (docs-only changes with no Python changed still run pre-commit; they may skip mypy and pytest-fast); list what you ran under How to Test before marking the PR ready
- never skip tests, retarget them to `self_hosted`, or raise timeouts to go green without a written justification in the PR body
- do not touch unrelated files
- conventional commit messages and PR titles (`feat:`, `fix:`, `docs:`, `chore:`, …)
- keep `dimensionalOS/dimos` syncs in their own PRs with no feature changes
- when the same mistake happens twice, add a rule to AGENTS.md in the same PR that fixes it

Coding-agent style, testing, and docs details: [docs/coding-agents/index.md](docs/coding-agents/index.md). Human contribution process: [CONTRIBUTING.md](CONTRIBUTING.md).
