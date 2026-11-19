"""Optimistic concurrency for configuration mutations (contract 1.4.0 conventions).

The single logical configuration revision (`Store.config_revision`) backs strong
ETags on configuration reads and mandatory `If-Match` preconditions on mutations:
missing header -> 428, stale/malformed -> 412.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TypeVar

from opal_say.persistence.store import Store
from opal_say.service.errors import InvalidRequest, PreconditionFailed, PreconditionRequired

T = TypeVar("T")

_ETAG = re.compile(r'"[^"\s]+"')


def config_etag(store: Store) -> str:
    """Strong validator for the logical configuration: `"r<config_revision>"`."""
    return f'"r{store.config_revision()}"'


def mutate_with_revision(store: Store, if_match: str | None, fn: Callable[[], T]) -> T:
    """Run `fn` only when `if_match` equals the current configuration revision.

    The comparison and the mutation are serialized under the store's write lock so
    a concurrent writer cannot interleave between check and mutation (no TOCTOU).
    """
    if if_match is None:
        raise PreconditionRequired("configuration mutations require an If-Match header")
    expected = _revision(if_match)
    if expected is None:
        raise InvalidRequest.single(
            ["header", "if-match"],
            'malformed If-Match: one strong quoted ETag required (e.g. "r7"); weak tags, lists and * are rejected',
            input_=if_match,
        )
    with store.write_lock():
        current = config_etag(store)
        if expected != current:
            raise PreconditionFailed(
                f"If-Match {if_match!r} does not match current configuration revision {config_etag(store)}"
            )
        return fn()


def _revision(tag: str) -> str | None:
    return tag if _ETAG.fullmatch(tag) else None
