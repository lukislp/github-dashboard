"""Configuration from environment variables, validated once at start-up."""

from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Settings:
    github_client_id: str
    github_client_secret: str
    secret_key: str
    base_url: str
    github_scopes: str
    allowed_logins: frozenset[str]
    cache_ttl_seconds: int
    session_ttl_hours: int
    runs_per_repo: int
    max_concurrency: int
    stale_days: int
    long_run_minutes: int
    db_path: str
    redis_url: str | None
    github_api_url: str
    github_web_url: str
    log_level: str
    security_alerts: bool
    hygiene_checks: bool
    background_refresh: bool
    background_refresh_seconds: int
    background_refresh_idle_minutes: int
    max_job_lookups: int
    actions_usage: bool
    ci_usage: bool

    @property
    def callback_url(self) -> str:
        return f"{self.base_url}/auth/callback"

    @property
    def cookie_secure(self) -> bool:
        return self.base_url.startswith("https://")

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env

        def required(key: str) -> str:
            value = env.get(key, "").strip()
            if not value:
                raise ConfigurationError(f"environment variable {key} is required")
            return value

        def integer(key: str, default: int, minimum: int = 1) -> int:
            raw = env.get(key, "").strip()
            if not raw:
                return default
            try:
                value = int(raw)
            except ValueError as exc:
                raise ConfigurationError(f"{key} must be an integer") from exc
            if value < minimum:
                raise ConfigurationError(f"{key} must be >= {minimum}")
            return value

        def boolean(key: str, default: bool) -> bool:
            raw = env.get(key, "").strip().lower()
            if not raw:
                return default
            if raw in ("true", "1"):
                return True
            if raw in ("false", "0"):
                return False
            raise ConfigurationError(f"{key} must be one of: true, false, 1, 0")

        secret_key = required("SECRET_KEY")
        if len(secret_key) < 32:
            raise ConfigurationError("SECRET_KEY must be at least 32 characters")

        base_url = required("BASE_URL").rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            raise ConfigurationError("BASE_URL must start with http:// or https://")

        allowed = frozenset(
            login.strip().lower()
            for login in env.get("ALLOWED_LOGINS", "").split(",")
            if login.strip()
        )
        return cls(
            github_client_id=required("GITHUB_CLIENT_ID"),
            github_client_secret=required("GITHUB_CLIENT_SECRET"),
            secret_key=secret_key,
            base_url=base_url,
            github_scopes=(
                env.get("GITHUB_SCOPES", "").strip()
                or "repo read:org security_events notifications"
            ),
            allowed_logins=allowed,
            cache_ttl_seconds=integer("CACHE_TTL_SECONDS", 120, minimum=0),
            session_ttl_hours=integer("SESSION_TTL_HOURS", 168),
            runs_per_repo=integer("RUNS_PER_REPO", 5),
            max_concurrency=integer("MAX_CONCURRENCY", 8),
            stale_days=integer("STALE_DAYS", 14),
            long_run_minutes=integer("LONG_RUN_MINUTES", 30),
            db_path=env.get("DB_PATH", "./data/sessions.db").strip() or "./data/sessions.db",
            redis_url=env.get("REDIS_URL", "").strip() or None,
            github_api_url=(
                env.get("GITHUB_API_URL", "").strip() or "https://api.github.com"
            ).rstrip("/"),
            github_web_url=(env.get("GITHUB_WEB_URL", "").strip() or "https://github.com").rstrip(
                "/"
            ),
            log_level=env.get("LOG_LEVEL", "INFO").strip().upper() or "INFO",
            security_alerts=boolean("SECURITY_ALERTS", True),
            hygiene_checks=boolean("HYGIENE_CHECKS", True),
            background_refresh=boolean("BACKGROUND_REFRESH", True),
            background_refresh_seconds=integer("BACKGROUND_REFRESH_SECONDS", 240, minimum=60),
            background_refresh_idle_minutes=integer(
                "BACKGROUND_REFRESH_IDLE_MINUTES", 30, minimum=1
            ),
            max_job_lookups=integer("MAX_JOB_LOOKUPS", 20, minimum=0),
            actions_usage=boolean("ACTIONS_USAGE", True),
            ci_usage=boolean("CI_USAGE", True),
        )
