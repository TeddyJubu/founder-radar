"""The one protocol every source adapter implements.

Adding a source is: write this file's protocol, register one line in
`__init__.py`, add a sheet row, add one fixture test. Nothing else changes,
because the pipeline only ever sees `RawItem` (02-architecture §4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Literal, Protocol, runtime_checkable

SourceKind = Literal["registry", "spinout", "accelerator", "grant", "news", "portfolio"]


@dataclass(frozen=True)
class RawItem:
    """One raw mention, before we know if it's real or relevant."""

    source_key: str
    source_url: str                 # the exact page a human can open
    external_id: str                # stable id within the source, for de-dup
    published_at: date | None       # None only if the source truly has no date
    title: str
    body_text: str | None = None    # for adapters that hand off to the reader
    structured: dict | None = None  # for adapters that already know the answer
    kind_hint: str | None = None    # spinout | cohort | grant | funding_round


@dataclass
class FetchContext:
    """Everything an adapter is allowed to reach for. No globals."""

    http: Any                       # radar.fetch.http.HttpClient
    config: Any                     # radar.config.models.Config
    db: Any | None = None           # radar.store.db.Db — for fetch_log / snapshots
    since: date | None = None
    now: date | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class SourceAdapter(Protocol):
    key: str
    kind: SourceKind
    schedule: str                   # daily | weekly | monthly
    requires_browser: bool

    def fetch(self, ctx: FetchContext) -> Iterable[RawItem]: ...


class SourceError(Exception):
    """A source failed. Caught per-source so the other thirteen continue."""

    def __init__(self, source_key: str, message: str) -> None:
        super().__init__(f"{source_key}: {message}")
        self.source_key = source_key
        self.message = message


class SourceBlocked(SourceError):
    """The site answered but refused *us* — 401/403/429/451 from a WAF or an
    anti-bot plugin, as opposed to the site being down.

    This is a distinct failure mode and is recorded as `degraded`, not
    `failed`: the distinction matters because a block is usually fixable on
    our side (contact the site, get allowlisted) and because a Tier 1 source
    blocked for two consecutive checks is worth a heartbeat alert
    (02-architecture §7, `radar.notify.heartbeat`).
    """
