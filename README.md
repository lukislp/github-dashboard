# github-dashboard

Every repository you can reach on GitHub on one screen: open pull requests, open issues, the last
five workflow runs of each repository, and the totals across all of them. Multi-user: each person
signs in with their own GitHub account and sees exactly what that account sees. Nothing is loaded
without a login.

## What it shows

- **Totals**: repositories, open pull requests, open issues, failed runs, running runs, open
  security alerts, unreleased commits, unread notifications.
- **Repository table**, sortable and filterable (search, owner, archived, forks, "needs attention").
  Each row expands into the open pull requests, open issues and recent runs of that repository,
  plus its Dependabot/code-scanning/secret-scanning alert counts and release status.
- **Failed runs feed** across all repositories, newest first.
- UI in English and German (toggle in the header), light and dark mode follow the system.

**When is a repository "failing"?** When at least one of its last `RUNS_PER_REPO` (default 5)
workflow runs concluded with `failure`, `timed_out` or `startup_failure`, regardless of branch.
The five runs are drawn as a small history strip in the table; hover shows workflow and result.

### Repository hygiene

Each non-archived, non-forked repository gets a hygiene score (0-100%) from nine checks, all
read from the same batched GraphQL query (no extra REST calls):

1. **readme** - a `README.md` exists at the repository root.
2. **license** - GitHub detected a license (`licenseInfo`).
3. **ci_workflow** - `.github/workflows` contains at least one `.yml`/`.yaml` file.
4. **dependency_updates** - a Dependabot (`.github/dependabot.yml`/`.yaml`) or Renovate
   (`renovate.json`, `.github/renovate.json`, `.renovaterc.json`) configuration is present.
5. **branch_protection** - at least one branch protection rule or ruleset is configured.
6. **vulnerability_alerts** - Dependabot alerts are enabled for the repository.
7. **delete_branch_on_merge** - merged branches are deleted automatically.
8. **security_policy** - a `SECURITY.md` exists (root or `.github/`).
9. **codeowners** - a `CODEOWNERS` file exists (root or `.github/`).

Archived repositories and forks still have all nine checks computed but are marked "not
applicable" with a fixed score of 100, so the UI can grey them out instead of penalising them.
Set `HYGIENE_CHECKS=false` to skip scoring altogether (every repository is then reported as not
applicable, with no checks). Hygiene contributes four totals (`repos_without_ci`,
`repos_without_protection`, `repos_without_dependency_updates`, `repos_without_license`) and an
account-wide `hygiene_average`, both computed only over applicable repositories.

### Branches without a pull request

Each repository also reports its branches (other than the default one) that have no open or
closed pull request pointing at them, oldest last-commit first, so forgotten branches surface
without an extra API call. Branch data is limited to the first 25 branches per repository (a
GraphQL page limit chosen to keep the batched query's cost and latency predictable; see
"Architecture" below); repositories with more branches than that will under-report
`branches_without_pr` for the branches beyond the first 25, though `branch_count` always
reflects the true total.

## Setup

### 1. Create a GitHub OAuth App

GitHub → Settings → Developer settings → OAuth Apps → *New OAuth App*.

| Field | Value |
|---|---|
| Homepage URL | `https://dashboard.example.com` (or `http://localhost:8000`) |
| Authorization callback URL | `<Homepage URL>/auth/callback` |

Copy the client ID and generate a client secret.

### 2. Configure

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"   # -> SECRET_KEY
```

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `GITHUB_CLIENT_ID` | yes | | OAuth App client ID |
| `GITHUB_CLIENT_SECRET` | yes | | OAuth App client secret |
| `SECRET_KEY` | yes | | Signs cookies, encrypts tokens at rest (min. 32 chars) |
| `BASE_URL` | yes | | Public URL; callback is `<BASE_URL>/auth/callback` |
| `GITHUB_SCOPES` | no | `repo read:org security_events notifications` | `repo` is needed for private repositories and their runs; `security_events` for code/secret-scanning alerts; `notifications` for the notifications feed |
| `ALLOWED_LOGINS` | no | *(everyone)* | Comma-separated GitHub logins allowed to sign in |
| `CACHE_TTL_SECONDS` | no | `120` | How long an overview is served from cache |
| `SESSION_TTL_HOURS` | no | `168` | Login lifetime |
| `RUNS_PER_REPO` | no | `5` | Recent runs inspected per repository |
| `MAX_CONCURRENCY` | no | `8` | Parallel GitHub requests per refresh |
| `STALE_DAYS` | no | `14` | Days without activity before a PR/issue is flagged stale |
| `LONG_RUN_MINUTES` | no | `30` | Minutes an active run may run before it is flagged long-running |
| `SECURITY_ALERTS` | no | `true` | Fetch code-scanning and secret-scanning alerts per repository (`true`/`false`/`1`/`0`) |
| `HYGIENE_CHECKS` | no | `true` | Score each repository's hygiene from the batched GraphQL query (`true`/`false`/`1`/`0`); when `false` every repository is reported as not applicable instead |
| `DB_PATH` | no | `./data/sessions.db` | SQLite session store (single replica) |
| `REDIS_URL` | no | | Redis for sessions and cache (multiple replicas) |

### 3. Run

Locally:

```bash
uv venv && uv pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000
```

Docker:

```bash
docker compose up --build
```

Kubernetes (single replica with a PersistentVolumeClaim for SQLite):

```bash
kubectl create namespace github-dashboard
kubectl -n github-dashboard create secret generic github-dashboard-secrets \
  --from-literal=GITHUB_CLIENT_ID=... \
  --from-literal=GITHUB_CLIENT_SECRET=... \
  --from-literal=SECRET_KEY=... \
  --from-literal=BASE_URL=https://dashboard.example.com
kubectl apply -k deploy/k8s/base
```

Edit `deploy/k8s/base/ingress.yaml` for your host, ingress class and TLS issuer. For more than one
replica set `REDIS_URL`, drop the PVC volume and switch the Deployment strategy to `RollingUpdate`.
The container runs as a non-root user with a read-only root filesystem; `/data` and `/tmp` are the
only writable paths. `/healthz` is the liveness probe, `/readyz` touches the session store.

## Security notes

- OAuth authorization-code flow with a signed, short-lived `state` cookie.
- The session cookie carries only a signed session ID (`HttpOnly`, `SameSite=Lax`, `Secure` on HTTPS).
- GitHub tokens are stored Fernet-encrypted, keyed from `SECRET_KEY`. Rotating the key invalidates
  all sessions.
- Signing out deletes the session and revokes the token at GitHub.
- A `401` from GitHub (token revoked in your GitHub settings) deletes the session immediately.
- Strict Content-Security-Policy; no inline scripts. The only external resources are the web fonts.
- No data is shared between users; the cache is keyed by GitHub user ID.
- `security_events` lets the app read Dependabot, code-scanning and secret-scanning alerts;
  `notifications` lets it read (but never mark read/unsubscribe) your notifications feed. Both
  are read-only. If a signed-in token lacks either scope, or a repository has the underlying
  feature disabled, that part is simply omitted (shown as unavailable, not zero).
- Each refresh now costs up to 4 REST requests per non-archived repository (workflow runs,
  code-scanning alerts, secret-scanning alerts, and a commit comparison for repositories with a
  release), on top of the batched GraphQL query. Set `SECURITY_ALERTS=false` to skip the two
  security REST calls per repository if that cost is too high for a large account.

## Per-user state

The dashboard remembers two things per signed-in GitHub account, independent of any session:

- **Preferences**: your custom repository groups and favourites (`GET`/`PUT /api/preferences`).
- **Snapshot**: the identity of everything you had already seen (open PRs/issues, failed runs,
  inbox items, notifications, repositories with open security alerts) as of your last
  `POST /api/seen`. Every `GET /api/overview` diffs the current data against it and returns the
  result as `changes`, so the UI can highlight what is new since your last visit.

Both are keyed by GitHub user ID, stored in the same place as sessions (the SQLite file, or Redis
when `REDIS_URL` is set), and **survive logout and re-login** — they are wiped only if you delete
the underlying store.

## Architecture

```
app/
  domain/          models, aggregation (build_overview), snapshot/diff, JSON codec — pure, no I/O
  application/     use cases (CompleteLogin, GetOverview, GetChanges, SavePreferences…) and ports
  infrastructure/  adapters: GitHub HTTP (GraphQL + REST), SQLite/Redis sessions and user state,
                   memory/Redis cache, Fernet cipher, settings from env
  web/             FastAPI routers (incl. /api/preferences, /api/seen), composition root
                   (container.py), templates, static assets
tests/
  unit/            domain + use cases with in-memory fakes
  integration/     adapters (respx-mocked GitHub, SQLite, fakeredis) and HTTP routes
  contract/        against the real GitHub API; runs only when GH_TOKEN is set
```

Dependencies point inwards only. Repositories with their open PR/issue counts, Dependabot alert
counts, latest release, hygiene facts (license/workflows/dependency-update config/branch
protection/rulesets/security policy/CODEOWNERS) and branches without a pull request all come
from one paginated GraphQL query (25 repositories and 25 branch refs per page — pushed further
and the combined query intermittently hit GitHub's per-query resource limits); workflow runs,
code/secret scanning alerts and unreleased-commit counts come from the REST API, fetched
concurrently with a semaphore. Archived repositories are not queried for runs, security alerts
or release status.

## Development

```bash
ruff check app tests && ruff format --check app tests
pytest -q
GH_TOKEN=$(gh auth token) pytest -q tests/contract   # optional live check
```

## License

MIT
