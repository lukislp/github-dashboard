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
