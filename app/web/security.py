"""Cookie signing, OAuth state tokens and security headers."""

from __future__ import annotations

import secrets

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

SESSION_COOKIE = "ghd_session"
STATE_COOKIE = "ghd_oauth_state"
LANG_COOKIE = "ghd_lang"
THEME_COOKIE = "ghd_theme"
STATE_MAX_AGE = 600

# Web fonts (IBM Plex) are self-hosted under /static/fonts, so style-src and font-src no
# longer need the Google Fonts hosts.
_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "font-src 'self'; "
    "img-src 'self' data: https://avatars.githubusercontent.com; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


class CookieSigner:
    """Signs opaque values so a tampered cookie is rejected before any lookup."""

    def __init__(self, secret_key: str) -> None:
        self._sessions = URLSafeTimedSerializer(secret_key, salt="ghd-session")
        self._states = URLSafeTimedSerializer(secret_key, salt="ghd-oauth-state")

    def sign_session(self, session_id: str) -> str:
        return self._sessions.dumps(session_id)

    def unsign_session(self, value: str | None) -> str | None:
        if not value:
            return None
        try:
            return self._sessions.loads(value)
        except BadSignature:
            return None

    def new_state(self) -> str:
        return self._states.dumps(secrets.token_urlsafe(24))

    def verify_state(self, cookie_value: str | None, query_value: str | None) -> bool:
        if not cookie_value or not query_value:
            return False
        try:
            self._states.loads(cookie_value, max_age=STATE_MAX_AGE)
        except (BadSignature, SignatureExpired):
            return False
        return secrets.compare_digest(cookie_value, query_value)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("Content-Security-Policy", _CSP)
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if request.url.scheme == "https":
            headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response
