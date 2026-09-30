"""Explicit, dated Catalyst cohort lists from Techscaler's public resources.

Programme membership is discovery evidence, never proof of headquarters,
registration, sector, funding or an individual company's investment stage.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

from radar.fetch.layout import LayoutChanged
from radar.sources._common import clean_text, html_doc, parse_date, require_ok, selector_fingerprint
from radar.sources.base import RawItem

BASE = "https://www.techscaler.co.uk"
INDEX = BASE + "/resources"
MAX_ARTICLES = 5
MAX_BYTES = 1_000_000
REQUEST_OPTIONS = dict(max_bytes=MAX_BYTES, timeout=8, max_retries=0, follow_redirects=False)


def _text(node):
    return clean_text(node.text(separator=" ", strip=True)).replace("\u200d", "").strip()


class TechscalerCatalystAdapter:
    key = "techscaler_catalyst"
    kind = "accelerator"
    track = "A"
    schedule = "weekly"
    requires_browser = False
    homepage = BASE
    endpoint = INDEX

    def _doc(self, payload):
        size = len(payload if isinstance(payload, bytes) else payload.encode())
        if size > MAX_BYTES:
            raise LayoutChanged(self.key, "document exceeds reviewed 1MB limit")
        return html_doc(payload, self.key)

    def parse_index(self, payload):
        doc = self._doc(payload)
        cards = doc.css('.blog_item.w-dyn-item')
        if not cards:
            raise LayoutChanged(self.key, "resource cards missing")
        posts = {}
        for card in cards:
            link = card.css_first('.blog_title-link[href]')
            if not link:
                continue
            title = _text(link)
            # Only cohort introductions, not applications, alumni or a programme recap.
            if not re.search(r'^(introducing|meet)\b.*\bcatalyst\b.*\bcohort\b', title, re.I):
                continue
            url = urljoin(BASE, link.attributes['href'])
            if urlsplit(url).scheme != 'https' or urlsplit(url).hostname != urlsplit(BASE).hostname or not urlsplit(url).path.startswith('/resources/'):
                continue
            stamp = card.css_first('.blog_date-wrapper')
            published = parse_date(_text(stamp)) if stamp else None
            if published is None:
                raise LayoutChanged(self.key, "cohort introduction lacks publication date")
            posts[url] = published
        if not posts:
            raise LayoutChanged(self.key, "no reviewed Catalyst cohort introduction cards")
        return sorted(posts.items(), key=lambda p: p[1], reverse=True)

    def parse(self, payload, source_url):
        doc = self._doc(payload)
        title = doc.css_first('h1')
        stamp = doc.css_first('.blog-post-header_date-wrapper')
        published = parse_date(_text(stamp)) if stamp else None
        content = doc.css_first('.text-rich-text.w-richtext')
        if not title or published is None or not content:
            raise LayoutChanged(self.key, "cohort title, publication date or article wrapper missing")
        cohort_title = _text(title)
        if not re.search(r'\bcatalyst\b.*\bcohort\b', cohort_title, re.I):
            raise LayoutChanged(self.key, "article is not a Catalyst cohort announcement")
        start = next((n for n in content.css('h2,h3,h4') if _text(n).casefold().rstrip(':') == 'meet the cohort'), None)
        if start is None:
            raise LayoutChanged(self.key, "explicit Meet the cohort section missing")
        nodes = []
        node = start.next
        ended = False
        while node is not None:
            if node.tag in ('h2', 'h3', 'h4'):
                ended = _text(node).casefold().rstrip(':') == 'up next'
                break
            if node.tag in ('p', 'ul', 'ol'):
                nodes.append(node)
            node = node.next
        if not ended or not nodes:
            raise LayoutChanged(self.key, "cohort list or Up next boundary missing")
        text = ' '.join(_text(n) for n in nodes)
        names = []
        seen = set()
        for value in text.split(','):
            name = clean_text(value).strip()
            normal = name.casefold()
            if not name or normal in {'n/a', 'nil'} or normal in seen:
                continue
            seen.add(normal)
            names.append(name)
        if not names:
            raise LayoutChanged(self.key, "cohort list contains no named participants")
        links = {}
        for n in nodes:
            for anchor in n.css('a[href]'):
                label = _text(anchor)
                url = urljoin(source_url, anchor.attributes['href'])
                if urlsplit(url).scheme in ('https', 'http') and urlsplit(url).hostname != urlsplit(BASE).hostname:
                    links.setdefault(label.casefold(), []).append({'label': label, 'url': url})
        self.last_fingerprint = selector_fingerprint(['h1', '.blog-post-header_date-wrapper', '.text-rich-text.w-richtext>Meet the cohort:..Up next'])
        return [RawItem(source_key=self.key, source_url=source_url,
                        external_id=f'{source_url}#{name.casefold()}', published_at=published,
                        title=name, body_text=f'{name} is listed in {cohort_title}.', kind_hint='accelerator_cohort',
                        structured={'company_name': name, 'program': 'Techscaler Catalyst',
                                    'cohort': cohort_title, 'date_confidence': 'exact',
                                    'company_link_evidence': links.get(name.casefold(), []),
                                    'needs_article_fetch': False}) for name in names]

    def fetch(self, ctx):
        self.last_failures = []
        self.last_fingerprint = None
        response = ctx.http.get(INDEX, **REQUEST_OPTIONS)
        if response.status == 304:
            return []
        require_ok(response, self.key, INDEX)
        posts = self.parse_index(response.text)
        items = []
        for url, published in posts[:MAX_ARTICLES]:
            if ctx.since and published < ctx.since:
                continue
            try:
                response = ctx.http.get(url, **REQUEST_OPTIONS)
                if response.status == 304:
                    continue
                require_ok(response, self.key, url)
                got = self.parse(response.text, url)
                if got[0].published_at != published:
                    raise LayoutChanged(self.key, "index and article publication dates disagree")
                items.extend(got)
            except Exception as exc:
                self.last_failures.append(f'{url}: {exc}')
        return items


ADAPTER = TechscalerCatalystAdapter()
