"""Bounded, permitted article hydration before prose extraction.

Unread excerpts are withheld, never promoted to complete article evidence.
Only named content wrappers are read; navigation/full-page fallback is forbidden.
"""
from dataclasses import replace
from urllib.parse import urlsplit, urljoin

from radar.sources._common import clean_text, html_doc, header_noindex

MAX_ARTICLES = 20
ARTICLE_TIMEOUT = 8.0
SELECTORS = {
    'businesscloud': ('div.post-content',),
    'bdaily_regional': ('div.Artivent__content',),
    'startups_magazine': ('div.post-content.entry-content',),
    'cambridge_enterprise': ('main section.block--text div.prose',),
    'ucl_ventures': ('main .sidebar-content-page__left-content .basic-content__column',),
    'sheffield': ('main .block-field-blocknodenews-articlebody',),
    'edinburgh_innovations': ('div[data-block="contentBlock"] div.prose',),
    'uktn': ('div.js-post-content', 'article .entry-content', 'div.article-content', 'div.post-content'),
}
DEFAULT_SELECTORS = ('article .entry-content', 'div.article-content', 'div.post-content')


def article_text(payload, source_key):
    doc = html_doc(payload, source_key)  # strips scripts and honors meta noindex
    for selector in SELECTORS.get(source_key, DEFAULT_SELECTORS):
        nodes = doc.css(selector)
        text = clean_text(' '.join(node.text(separator=' ', strip=True) for node in nodes))
        if len(text) >= 200:
            return text
    raise ValueError('no substantive article content in reviewed wrappers')


def article_links(payload, source_key, base_url):
    """Retain labelled links only from the same reviewed article wrappers."""
    doc = html_doc(payload, source_key)
    for selector in SELECTORS.get(source_key, DEFAULT_SELECTORS):
        nodes = doc.css(selector)
        if len(clean_text(' '.join(n.text(separator=' ', strip=True) for n in nodes))) < 200:
            continue
        links = []
        for node in nodes:
            for anchor in node.css('a[href]'):
                label = clean_text(anchor.text(separator=' ', strip=True))
                url = urljoin(base_url, anchor.attributes['href'])
                if label and urlsplit(url).scheme in ('http', 'https'):
                    evidence = {'label': label, 'url': url}
                    if evidence not in links:
                        links.append(evidence)
        return links[:100]
    return []


def hydrate_articles(items, ctx, *, kind, limit=MAX_ARTICLES):
    if kind not in ('news', 'spinout'):
        return items, []
    out, failures, fetched = [], [], 0
    for item in items:
        structured = dict(item.structured or {})
        if structured.get('full_text_in_feed') is not False or structured.get('needs_article_fetch') is False:
            out.append(item)
            continue
        if fetched >= limit:
            failures.append(f'budget: {item.external_id}: article fetch limit reached; excerpt withheld')
            continue
        fetched += 1
        try:
            parts = urlsplit(item.source_url)
            if parts.scheme not in ('http', 'https') or not parts.hostname:
                raise ValueError('invalid article URL')
            if item.source_key == 'uktn' and (parts.query or parts.fragment):
                raise ValueError('UKTN query/fragment prohibited')
            from radar.qa.provenance import _public
            current = item.source_url
            for hop in range(4):
                # Injected offline clients do not resolve fixture hosts; real
                # HttpClient always rejects private DNS answers before GET.
                _public(current, resolve=hasattr(ctx.http, '_client'))
                if item.source_key == 'uktn' and (urlsplit(current).query or urlsplit(current).fragment):
                    raise ValueError('UKTN query/fragment prohibited')
                resp = ctx.http.get(current, timeout=ARTICLE_TIMEOUT,
                                    max_retries=0, follow_redirects=False)
                if resp.status in (301, 302, 303, 307, 308):
                    location = resp.headers.get('location')
                    if not location:
                        raise ValueError('redirect without destination')
                    current = urljoin(current, location)
                    continue
                if resp.status == 304:
                    resp = ctx.http.get(current, headers={'Cache-Control': 'no-cache'},
                                        timeout=ARTICLE_TIMEOUT, max_retries=0,
                                        follow_redirects=False)
                    if resp.status in (301, 302, 303, 307, 308):
                        current = urljoin(current, resp.headers.get('location', ''))
                        continue
                break
            else:
                raise ValueError('article redirect limit exceeded')
            if not resp.ok:
                raise ValueError(f'HTTP {resp.status}; article unavailable')
            if header_noindex(resp.headers):
                raise ValueError('article requests noindex')
            text = article_text(resp.text, item.source_key)
            structured.update(full_text_in_feed=True, needs_article_fetch=False,
                              article_body_hydrated=True,
                              company_link_evidence=article_links(resp.text, item.source_key, current))
            out.append(replace(item, body_text=text, structured=structured))
        except Exception as exc:  # isolate one article; preserve the healthy feed
            failures.append(f'{item.external_id}: {type(exc).__name__}: {exc}; excerpt withheld')
    return out, failures
