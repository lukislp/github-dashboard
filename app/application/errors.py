"""Application-level exceptions. Adapters translate transport errors into these."""


class ApplicationError(Exception):
    """Base class for errors the web layer maps to HTTP responses."""


class AuthenticationError(ApplicationError):
    """The GitHub token is missing, expired or was revoked."""


class AccessDenied(ApplicationError):
    """The GitHub account is valid but not allowed to use this deployment."""


class RateLimited(ApplicationError):
    """GitHub refused the request because the token's rate limit is exhausted."""


class GitHubUnavailable(ApplicationError):
    """GitHub answered with an unexpected error or was unreachable."""


class ActionsUnavailable(ApplicationError):
    """Workflow runs of one repository cannot be read (Actions disabled, no permission)."""


class RunNotRerunnable(ApplicationError):
    """GitHub refused to rerun a workflow run's failed jobs (409: still in progress or too old)."""


class PreferencesInvalid(ValueError):
    """Submitted preferences violate a stored-state limit.

    Carries the client-facing text in `detail` instead of relying on `str(exc)`. The web layer
    must only ever return `detail`, so that an unrelated `ValueError` raised further down (a
    JSON parser complaint, a codec failure) can never leak internals into an HTTP response.
    Subclasses `ValueError` so existing callers that expect one keep working.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail
