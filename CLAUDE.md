# Claude Code instructions

This repository's agent rules live in [AGENTS.md](AGENTS.md). Read that file before you edit anything.

The **Agent PR workflow (mandatory)** section is the working contract for this fork (`topsun-bot/topsun_dimos`):

- one concern per PR and per commit
- branch from latest `origin/main` and rebase before opening a PR
- run `uv run mypy`, `SKIP=cargo-fmt,cargo-clippy pre-commit run --all-files`, and `./bin/pytest-fast` before every commit
- never skip tests, retarget them to `self_hosted`, or raise timeouts to go green without a written justification in the PR body
- do not touch unrelated files
- conventional commit messages (`feat:`, `fix:`, `docs:`, `chore:`, …)
- keep `dimensionalOS/dimos` syncs in their own PRs with no feature changes

Coding-agent style, testing, and docs details: [docs/coding-agents/index.md](docs/coding-agents/index.md). Human contribution process: [CONTRIBUTING.md](CONTRIBUTING.md).
