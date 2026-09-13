# Contributing to github-dashboard

Thanks for taking the time. This is a single-maintainer project, so the process is deliberately
small - but it is the same for every change, including the maintainer's own.

## How changes get in

1. Open an issue first for anything bigger than a typo or an obvious bug fix, so the direction can
   be agreed before you spend time on it. Use the templates under `.github/ISSUE_TEMPLATE/`.
2. Fork the repository (or branch, if you have write access) and make your change on a branch.
3. Open a pull request against `main`. The pull-request template asks for what changed and why.
4. `main` is protected: a PR merges only after the whole test stage of
   [`.github/workflows/ci.yml`](.github/workflows/ci.yml) is green and the branch is up to date
   with `main` (enable auto-merge and it lands on its own once that is the case). Nobody pushes
   to `main` directly, not even the maintainer.

## What a pull request needs

- **Conventional Commits.** The version and the changelog are generated from the commit messages
  (`feat:` = minor release, `fix:` = patch release, `build:`/`ci:`/`docs:`/`test:` = no release).
  Squash-merge keeps the PR title as the commit message, so give the PR a Conventional Commit
  title. Commit messages are plain English without tool or AI attribution.
- **Tests for new functionality.** New features and bug fixes come with tests under `tests/`
  (`tests/unit`, `tests/integration`, and `tests/contract` for the real GitHub API). A PR that
  adds behaviour without a test is asked to add one.
- **Formatting and linting.** `ruff check app tests` and `ruff format --check app tests` run as
  required checks; run both (or `ruff check --fix` / `ruff format`) before pushing.
- **No secrets, tokens or personal data** in code, tests or logs.

## Running things locally

```bash
uv sync
uv run ruff check app tests
uv run ruff format --check app tests
uv run pytest -q
GH_TOKEN=$(gh auth token) uv run pytest -q tests/contract   # optional live check
```

See the README's "Setup" and "Development" sections for running the app itself.

## Security issues

Please do not open a public issue for a vulnerability - use the private reporting path described
in [SECURITY.md](SECURITY.md).
