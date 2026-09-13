"""Bounded LRU cache for conditional GitHub REST requests (ETag revalidation).

GitHub's REST API supports conditional `GET`s: send back the ETag of a previously seen
response in an `If-None-Match` header, and an unchanged resource comes back as a `304` with an
empty body - which, unlike a `200`, does not count against the token's REST rate limit. This
module only holds the (etag, parsed body) pairs; the request/response plumbing lives in
`app.infrastructure.github_http`. GraphQL is not covered here: GitHub does not support
conditional requests for GraphQL, only REST.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

_DEFAULT_MAX_ENTRIES = 2000


def fingerprint(token: str) -> str:
    """A short, non-reversible key for a token, so the cache never stores the token itself."""
    return hashlib.sha256(token.encode()).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class _Entry:
    etag: str
    body: Any


class ConditionalCache:
    """Bounded LRU of (etag, parsed body), keyed by (token fingerprint, url with query).

    Different tokens never share an entry, even for the identical URL: GitHub's per-repository
    responses can differ by the caller's permissions. `hits`/`misses`/`stores` are plain
    attributes (not properties) so the caller can read them straight off for metrics later;
    `get` counts a hit when an entry already exists for the key and a miss otherwise, `put`
    counts a store and evicts the least-recently-used entry once `max_entries` is exceeded.
    """

    def __init__(self, max_entries: int = _DEFAULT_MAX_ENTRIES) -> None:
        self._max_entries = max_entries
        self._entries: OrderedDict[tuple[str, str], _Entry] = OrderedDict()
        self.hits = 0
        self.misses = 0
        self.stores = 0

    def get(self, token_fingerprint: str, url: str) -> tuple[str, Any] | None:
        """The cached `(etag, body)` for `(token_fingerprint, url)`, or `None` if absent."""
        key = (token_fingerprint, url)
        entry = self._entries.get(key)
        if entry is None:
            self.misses += 1
            return None
        self.hits += 1
        self._entries.move_to_end(key)
        return entry.etag, entry.body

    def put(self, token_fingerprint: str, url: str, *, etag: str, body: Any) -> None:
        """Store (or refresh) the entry for `(token_fingerprint, url)`."""
        key = (token_fingerprint, url)
        self._entries[key] = _Entry(etag, body)
        self._entries.move_to_end(key)
        self.stores += 1
        if len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
