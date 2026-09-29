"""UKTN — public HTML index plus a bounded per-article fetch.

The old JSON endpoint carries noindex. The public homepage supplies real
article links and some dates; a date embedded in an article slug is also valid
evidence. Unknown dates remain unknown. robots disallows query strings, so
all requests are path-only. Archived JSON captures remain parseable offline.
"""

from __future__ import annotations

from datetime import date
from typing import Iterable
from urllib.parse import urlsplit

from radar.sources._common import (
    LayoutChanged,
    after,
    clean_text,
    guard_nonempty,
    html_doc,
    parse_date,
    require_ok,
    selector_fingerprint,
    strip_html,
    unique_by_id,
)
from radar.sources.base import FetchContext, RawItem

BASE = "https://www.uktech.news"
INDEX = f"{BASE}/"

#: Fetch at most this many article bodies per run. UKTN publishes ~10 a day;
#: the cap is a circuit breaker for the day the index returns 500 items.
MAX_ARTICLE_FETCHES = 30

ARTICLE_SELECTORS = (
    "article .entry-content",
    "div.article-content",
    "div.post-content",
    "article",
)


class QueryStringForbidden(Exception):
    """A UKTN URL grew a query string. robots.txt disallows `/*?`."""


def _assert_no_query(url: str) -> str:
    parts = urlsplit(url)
    if parts.query or parts.fragment or "?" in url:
        raise QueryStringForbidden(
            f"uktn robots.txt disallows /*? — refusing to fetch {url!r}"
        )
    return url


class UktnAdapter:
    key = "uktn"
    kind = "news"
    schedule = "daily"
    requires_browser = False
    track = "A"
    endpoint = INDEX
    homepage = BASE

    def fetch(self, ctx: FetchContext) -> Iterable[RawItem]:
        # No `params=` anywhere in this method. That is the point of the file.
        resp = ctx.http.get(_assert_no_query(INDEX))
        if resp.status == 304:
            return []
        require_ok(resp, self.key, INDEX)

        items = list(after(unique_by_id(self.parse(resp.text)), ctx.since))
        return [self._with_body(ctx, item) for item in items[:MAX_ARTICLE_FETCHES]] \
            + items[MAX_ARTICLE_FETCHES:]

    # ------------------------------------------------------------------ parse

    def parse(self, payload: str | bytes) -> list[RawItem]:
        """The `latest` route returns a bare array of thin post objects."""
        import json

        body = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else payload
        if body.lstrip().startswith('<'):
            from radar.sources._public_news import public_posts
            posts, self.last_fingerprint = public_posts(body,self.key,BASE,'article')
            return [RawItem(source_key=self.key,source_url=p['link'],external_id=p['id'],
                            published_at=p['date'],title=p['title'],body_text=p['excerpt'] or None,
                            structured={'date_confidence': 'exact' if p['date'] else 'unknown',
                                        'needs_article_fetch':True,'full_text_in_feed':False},
                            kind_hint='news_mention') for p in posts]
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise LayoutChanged(self.key, f"response is not JSON: {exc}") from exc

        if isinstance(data, dict):
            # Some WP builds wrap the custom route in {"posts": [...]}.
            data = data.get("posts") or data.get("items") or data.get("data")
        if not isinstance(data, list):
            raise LayoutChanged(self.key, "expected a JSON array of posts")
        guard_nonempty(self.key, data, detail="latest returned no posts", document=body)

        keys: set[str] = set()
        out: list[RawItem] = []
        for post in data:
            if not isinstance(post, dict):
                raise LayoutChanged(self.key, "post is not an object")
            keys.update(post.keys())
            link = post.get("link") or post.get("url") or post.get("permalink")
            title = post.get("title")
            if isinstance(title, dict):
                title = title.get("rendered")
            if not link or not title:
                raise LayoutChanged(self.key, "post is missing link or title")

            published = parse_date(post.get("date_gmt") or post.get("date")) \
                or self._date_from_slug(link)
            excerpt = post.get("excerpt")
            if isinstance(excerpt, dict):
                excerpt = excerpt.get("rendered")

            out.append(RawItem(
                source_key=self.key,
                source_url=link,
                external_id=str(post.get("id") or link),
                published_at=published,
                title=clean_text(strip_html(title)),
                body_text=strip_html(excerpt) or None,
                structured={
                    "date_confidence": "exact" if post.get("date") else "inferred",
                    "needs_article_fetch": True,
                },
                kind_hint="news_mention",
            ))
        self.last_fingerprint = selector_fingerprint(keys)
        return out

    @staticmethod
    def _date_from_slug(link: str) -> date | None:
        """UKTN slugs carry the publish date (04-sources §2, row 7)."""
        return parse_date("-".join(urlsplit(link).path.strip("/").split("/")[:3]))

    # ------------------------------------------------------------ article body

    def parse_article(self, payload: str | bytes) -> str:
        doc = html_doc(payload, self.key)
        for selector in ARTICLE_SELECTORS:
            node = doc.css_first(selector)
            if node is not None:
                text = clean_text(node.text(separator=" ", strip=True))
                if text:
                    return text
        raise LayoutChanged(self.key, f"no article body matched {ARTICLE_SELECTORS}")

    def _with_body(self, ctx: FetchContext, item: RawItem) -> RawItem:
        """One extra GET per article, because `latest` carries no body.

        A single article failing must not lose the index item — the headline
        and date are already useful, and the pipeline can re-fetch tomorrow.
        """
        try:
            resp = ctx.http.get(_assert_no_query(item.source_url))
            if not resp.ok:
                return item
            text = self.parse_article(resp.text)
        except (LayoutChanged, QueryStringForbidden):
            raise
        except Exception:                                # noqa: BLE001
            return item
        structured = dict(item.structured or {})
        structured["needs_article_fetch"] = False
        return RawItem(
            source_key=item.source_key,
            source_url=item.source_url,
            external_id=item.external_id,
            published_at=item.published_at,
            title=item.title,
            body_text=text or item.body_text,
            structured=structured,
            kind_hint=item.kind_hint,
        )


ADAPTER = UktnAdapter()
