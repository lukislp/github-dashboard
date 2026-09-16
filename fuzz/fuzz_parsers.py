"""Atheris fuzz harness for the code that turns outside input into domain objects.

Three contracts are under test, all of them pure and dependency-free (the harness imports
nothing beyond the standard library and atheris, so it never needs the app's runtime deps):

1. `Settings.from_env` accepts an arbitrary environment. Either it rejects it with a
   `ConfigurationError`, or the `Settings` it returns honours every bound the loader
   documents - scheme and no trailing slash on the base URL, the per-key integer minimums,
   no empty entry in `allowed_logins`, no trailing slash on the two GitHub URLs.
2. `app.domain.codec` round-trips. A pull request, a workflow run, a preferences record and
   a snapshot that go through `*_to_dict` and back come out identical; for the two records
   whose encoder also sorts, the second pass is identical to the first.
3. The decoders survive hostile payloads. Fed an arbitrary nested structure, they may reject
   it, but only by raising one of the exception types the callers already handle - anything
   else (a hang, a recursion blow-up, an exotic exception escaping a `.get()` chain) is a
   defect, because these decoders run on cache and session data that was JSON at rest.

Run locally (Linux, needs the atheris wheel):
    pip install --require-hashes -r requirements_fuzz.txt
    PYTHONPATH=. python fuzz/fuzz_parsers.py -max_total_time=60
CI runs the same harness for a short, fixed time budget (see .github/workflows/ci.yml).
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from typing import Any

import atheris

from app.domain.codec import (
    changes_from_dict,
    overview_from_dict,
    pr_from_dict,
    pr_to_dict,
    preferences_from_dict,
    preferences_to_dict,
    run_from_dict,
    run_to_dict,
    snapshot_from_dict,
    snapshot_to_dict,
)
from app.domain.models import (
    ChecksState,
    Mergeable,
    Preferences,
    PullRequest,
    RepoGroup,
    ReviewDecision,
    RunStatus,
    Snapshot,
    WorkflowRun,
)
from app.domain.pull_requests import PrState, pr_state
from app.infrastructure.settings import ConfigurationError, Settings

# Every key `Settings.from_env` reads. The four required ones are offered a usable value part
# of the time so the fuzzer reaches the success path and its assertions, instead of bouncing
# off the "GITHUB_CLIENT_ID is required" guard on nearly every input.
_REQUIRED_KEYS = ("GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET", "SECRET_KEY", "BASE_URL")
_OPTIONAL_KEYS = (
    "GITHUB_SCOPES",
    "ALLOWED_LOGINS",
    "CACHE_TTL_SECONDS",
    "SESSION_TTL_HOURS",
    "RUNS_PER_REPO",
    "MAX_CONCURRENCY",
    "STALE_DAYS",
    "LONG_RUN_MINUTES",
    "DB_PATH",
    "REDIS_URL",
    "GITHUB_API_URL",
    "GITHUB_WEB_URL",
    "LOG_LEVEL",
    "SECURITY_ALERTS",
    "HYGIENE_CHECKS",
    "BACKGROUND_REFRESH",
    "BACKGROUND_REFRESH_SECONDS",
    "BACKGROUND_REFRESH_IDLE_MINUTES",
    "MAX_JOB_LOOKUPS",
    "ACTIONS_USAGE",
    "CI_USAGE",
)
_PLAUSIBLE_REQUIRED = {
    "GITHUB_CLIENT_ID": "Iv1.0123456789abcdef",
    "GITHUB_CLIENT_SECRET": "0123456789abcdef0123456789abcdef01234567",
    "SECRET_KEY": "0123456789abcdef0123456789abcdef",
    "BASE_URL": "https://dash.example.org",
}
# The lower bound `from_env` promises for each integer setting.
_INTEGER_MINIMUMS = {
    "cache_ttl_seconds": 0,
    "max_job_lookups": 0,
    "session_ttl_hours": 1,
    "runs_per_repo": 1,
    "max_concurrency": 1,
    "stale_days": 1,
    "long_run_minutes": 1,
    "background_refresh_idle_minutes": 1,
    "background_refresh_seconds": 60,
}
# What the decoders are allowed to raise on a malformed payload: a missing key, a value of the
# wrong type, an unparsable timestamp or enum member. The callers turn these into "cache miss".
_DECODE_ERRORS = (KeyError, TypeError, ValueError, AttributeError, IndexError)


def _datetime(fdp: atheris.FuzzedDataProvider) -> datetime:
    """A UTC instant inside the range `isoformat` and `fromisoformat` both round-trip."""
    seconds = fdp.ConsumeIntInRange(0, 2**31 - 1)
    microseconds = fdp.ConsumeIntInRange(0, 999_999)
    return datetime.fromtimestamp(seconds, tz=UTC).replace(microsecond=microseconds)


def _member(fdp: atheris.FuzzedDataProvider, enum_cls: Any, optional: bool = False) -> Any:
    members = list(enum_cls)
    index = fdp.ConsumeIntInRange(0, len(members) if optional else len(members) - 1)
    return None if index == len(members) else members[index]


def _value(fdp: atheris.FuzzedDataProvider, depth: int) -> Any:
    """An arbitrary JSON-shaped value: what a corrupted cache entry could decode to."""
    kind = fdp.ConsumeIntInRange(0, 7 if depth > 0 else 5)
    if kind == 0:
        return None
    if kind == 1:
        return fdp.ConsumeBool()
    if kind == 2:
        return fdp.ConsumeInt(6)
    if kind == 3:
        return fdp.ConsumeRegularFloat()
    if kind == 4:
        return fdp.ConsumeUnicodeNoSurrogates(24)
    if kind == 5:
        # Timestamp-shaped strings, so the `datetime.fromisoformat` paths get exercised
        # rather than being rejected by the first character every time.
        return f"{fdp.ConsumeUnicodeNoSurrogates(8)}2024-01-0{fdp.ConsumeIntInRange(1, 9)}"
    if kind == 6:
        return [_value(fdp, depth - 1) for _ in range(fdp.ConsumeIntInRange(0, 3))]
    return {
        fdp.ConsumeUnicodeNoSurrogates(12): _value(fdp, depth - 1)
        for _ in range(fdp.ConsumeIntInRange(0, 3))
    }


def _check_settings(fdp: atheris.FuzzedDataProvider) -> None:
    env: dict[str, str] = {}
    for key in _REQUIRED_KEYS:
        choice = fdp.ConsumeIntInRange(0, 2)
        if choice == 0:
            env[key] = _PLAUSIBLE_REQUIRED[key]
        elif choice == 1:
            env[key] = fdp.ConsumeUnicodeNoSurrogates(48)
    for key in _OPTIONAL_KEYS:
        if fdp.ConsumeBool():
            env[key] = fdp.ConsumeUnicodeNoSurrogates(20)

    try:
        settings = Settings.from_env(env)
    except ConfigurationError:
        # The documented way to reject an environment.
        return

    if not settings.base_url.startswith(("http://", "https://")):
        raise AssertionError(f"base_url accepted without a scheme: {settings.base_url!r}")
    if settings.base_url.endswith("/"):
        raise AssertionError(f"base_url kept a trailing slash: {settings.base_url!r}")
    if settings.cookie_secure != settings.base_url.startswith("https://"):
        raise AssertionError(f"cookie_secure disagrees with {settings.base_url!r}")
    if settings.callback_url != f"{settings.base_url}/auth/callback":
        raise AssertionError(f"callback_url is not under base_url: {settings.callback_url!r}")
    if len(settings.secret_key) < 32:
        raise AssertionError(f"SECRET_KEY shorter than 32 accepted: {len(settings.secret_key)}")
    for field, minimum in _INTEGER_MINIMUMS.items():
        value = getattr(settings, field)
        if value < minimum:
            raise AssertionError(f"{field} = {value} is below its minimum of {minimum}")
    for login in settings.allowed_logins:
        if not login:
            raise AssertionError("allowed_logins contains an empty entry")
    # An empty allow-list means "everyone"; an empty *entry* would silently mean the same,
    # which is the case above. These two must not end in a slash or every request path the
    # client builds on top of them gets a doubled separator.
    for url in (settings.github_api_url, settings.github_web_url):
        if url.endswith("/"):
            raise AssertionError(f"GitHub URL kept a trailing slash: {url!r}")
    if settings.redis_url == "":
        raise AssertionError("redis_url is empty rather than None")
    if not settings.db_path or not settings.log_level:
        raise AssertionError("db_path/log_level must never end up empty")


def _check_pull_request(fdp: atheris.FuzzedDataProvider) -> None:
    pull_request = PullRequest(
        number=fdp.ConsumeInt(4),
        title=fdp.ConsumeUnicodeNoSurrogates(32),
        url=fdp.ConsumeUnicodeNoSurrogates(32),
        author=fdp.ConsumeUnicodeNoSurrogates(16) if fdp.ConsumeBool() else None,
        is_draft=fdp.ConsumeBool(),
        updated_at=_datetime(fdp),
        created_at=_datetime(fdp),
        head_branch=fdp.ConsumeUnicodeNoSurrogates(16) if fdp.ConsumeBool() else None,
        review_decision=_member(fdp, ReviewDecision, optional=True),
        checks=_member(fdp, ChecksState, optional=True),
        mergeable=_member(fdp, Mergeable),
        is_bot=fdp.ConsumeBool(),
        stale=fdp.ConsumeBool(),
        age_days=fdp.ConsumeInt(3),
        idle_days=fdp.ConsumeInt(3),
    )
    encoded = pr_to_dict(pull_request)
    if pr_from_dict(encoded) != pull_request:
        raise AssertionError(f"pull request did not survive the round trip: {encoded!r}")

    state = PrState(encoded["state"])
    if state is not pr_state(pull_request):
        raise AssertionError("pr_to_dict disagrees with pr_state")
    # A pull request must never be advertised as ready to merge while it is failing, drafted,
    # conflicting, or has changes requested - that is the one verdict acted on without looking.
    blocked = (
        pull_request.is_draft
        or pull_request.mergeable is Mergeable.CONFLICTING
        or pull_request.checks in (ChecksState.FAILURE, ChecksState.ERROR, ChecksState.PENDING)
        or pull_request.review_decision is ReviewDecision.CHANGES_REQUESTED
    )
    if blocked and state is PrState.READY:
        raise AssertionError(f"blocked pull request reported as ready: {encoded!r}")


def _check_workflow_run(fdp: atheris.FuzzedDataProvider) -> None:
    created_at = _datetime(fdp)
    run = WorkflowRun(
        id=fdp.ConsumeInt(5),
        workflow_name=fdp.ConsumeUnicodeNoSurrogates(24),
        title=fdp.ConsumeUnicodeNoSurrogates(24),
        url=fdp.ConsumeUnicodeNoSurrogates(24),
        branch=fdp.ConsumeUnicodeNoSurrogates(16) if fdp.ConsumeBool() else None,
        event=fdp.ConsumeUnicodeNoSurrogates(12),
        status=_member(fdp, RunStatus),
        run_number=fdp.ConsumeInt(3),
        created_at=created_at,
        updated_at=_datetime(fdp),
        started_at=_datetime(fdp) if fdp.ConsumeBool() else created_at,
        long_running=fdp.ConsumeBool(),
    )
    encoded = run_to_dict(run)
    if run_from_dict(encoded) != run:
        raise AssertionError(f"workflow run did not survive the round trip: {encoded!r}")
    if encoded["duration_seconds"] < 0:
        raise AssertionError(f"negative duration reported: {encoded['duration_seconds']}")
    if run.active and encoded["duration_seconds"] != 0:
        raise AssertionError("a still-running run reported a duration")


def _check_sorting_encoders(fdp: atheris.FuzzedDataProvider) -> None:
    """`preferences_to_dict` and `snapshot_to_dict` sort, so they must be idempotent."""
    preferences = Preferences(
        groups=tuple(
            RepoGroup(
                name=fdp.ConsumeUnicodeNoSurrogates(12),
                repos=tuple(
                    fdp.ConsumeUnicodeNoSurrogates(12) for _ in range(fdp.ConsumeIntInRange(0, 3))
                ),
            )
            for _ in range(fdp.ConsumeIntInRange(0, 3))
        ),
        favorites=tuple(
            fdp.ConsumeUnicodeNoSurrogates(12) for _ in range(fdp.ConsumeIntInRange(0, 3))
        ),
    )
    once = preferences_to_dict(preferences)
    twice = preferences_to_dict(preferences_from_dict(once))
    if once != twice:
        raise AssertionError(f"preferences encoding is not idempotent: {once!r} vs {twice!r}")

    def _names() -> frozenset[str]:
        return frozenset(
            fdp.ConsumeUnicodeNoSurrogates(12) for _ in range(fdp.ConsumeIntInRange(0, 3))
        )

    snapshot = Snapshot(
        taken_at=_datetime(fdp),
        prs=_names(),
        issues=_names(),
        failed_runs=_names(),
        inbox=_names(),
        notifications=_names(),
        alert_repos=_names(),
    )
    encoded = snapshot_to_dict(snapshot)
    if snapshot_from_dict(encoded) != snapshot:
        raise AssertionError(f"snapshot did not survive the round trip: {encoded!r}")


def _check_hostile_payloads(fdp: atheris.FuzzedDataProvider) -> None:
    payload = _value(fdp, depth=3)
    for decode in (
        overview_from_dict,
        changes_from_dict,
        preferences_from_dict,
        snapshot_from_dict,
        pr_from_dict,
        run_from_dict,
    ):
        try:
            decode(payload)  # type: ignore[arg-type]
        except _DECODE_ERRORS:
            # Rejecting a corrupted payload this way is the contract; the callers treat it
            # as a cache miss.
            continue
        except Exception as exc:  # noqa: BLE001 - the whole point is to catch the unexpected
            raise AssertionError(
                f"{decode.__name__} raised {type(exc).__name__} on {payload!r}"
            ) from exc


def test_one_input(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    _check_settings(fdp)
    _check_pull_request(fdp)
    _check_workflow_run(fdp)
    _check_sorting_encoders(fdp)
    _check_hostile_payloads(fdp)


if __name__ == "__main__":
    atheris.instrument_all()
    atheris.Setup(sys.argv, test_one_input)
    atheris.Fuzz()
