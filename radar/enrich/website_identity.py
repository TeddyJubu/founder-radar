"""Verify source-named company sites, never search for or guess an identity.

An explicit primary-site UK legal registration statement plus its registry
profile is required. Collisions remain reviewable; this module never merges.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit

from radar.qa.provenance import _public
from radar.resolve.normalise import norm_key, norm_domain, norm_ch_number
from radar.sources._common import html_doc, clean_text, header_noindex
from radar.sources.companies_house import CH_API_BASE, CH_PROFILE_URL
from radar.store.db import now_iso

LINK_FIELD = 'company_website_link'
RETRY_PREFIX = 'website_identity_attempt:'
# A number alone (VAT, postcode, phone, client example) is never identity.
REGISTRATION = re.compile(r'\b(?:company\s+(?:registration\s+)?(?:number|no\.?|#)|registered\s+(?:company\s+)?(?:number|no\.?))\s*[:#]?\s*((?:(?:SC|NI|OC|SO|NC|FC)\d{1,6}|\d{6,8}))\b', re.I)
UK_REGISTERED = re.compile(r'\bregistered\s+in\s+(?:England(?:\s+and\s+Wales)?|Wales|Scotland|Northern\s+Ireland|the\s+(?:UK|United\s+Kingdom))\b', re.I)
LEGAL_LINK = re.compile(r'\b(?:privacy|terms|legal|about|contact)\b', re.I)
UK_JURISDICTIONS = {'england-wales', 'england', 'wales', 'scotland', 'northern-ireland', 'united-kingdom'}


def observe(db, cid, field, value, *, source_key, source_type, source_url):
    value = json.dumps(value, sort_keys=True)
    if db.one('SELECT 1 FROM observation WHERE company_id=? AND field=? AND value_json=? AND source_url=?',
              (cid, field, value, source_url)):
        return
    db.execute('INSERT INTO observation(company_id,field,value_json,source_key,source_type,source_url,confidence,observed_at,extractor_ver) VALUES (?,?,?,?,?,?,?,?,?)',
               (cid, field, value, source_key, source_type, source_url, 1.0, now_iso(), 'website_identity_v1'))


def retain_company_links(db, cid, name, links, *, source_key, source_url):
    """Only exact source-labelled company links become verification candidates."""
    for link in links[:100]:
        if not isinstance(link, dict) or norm_key(link.get('label')) != norm_key(name):
            continue
        url = link.get('url')
        try:
            _public(url, resolve=False)
            if not norm_domain(url) or norm_domain(url) == norm_domain(source_url):
                continue
        except (ValueError, TypeError):
            continue
        observe(db, cid, LINK_FIELD, {'url': url, 'label': link['label']},
                source_key=source_key, source_type='news', source_url=source_url)


def _page(http, url, domain, budget):
    """Public same-domain redirects, robots enforced by HttpClient per hop."""
    for _ in range(3):
        _public(url, resolve=hasattr(http, '_client'))
        if norm_domain(url) != domain:
            raise ValueError('cross-domain redirect')
        if not budget.spend():
            raise ValueError('request budget exhausted')
        response = http.get(url, timeout=8, max_retries=0, follow_redirects=False, max_bytes=1_000_000)
        if response.status in (301, 302, 303, 307, 308):
            location = response.headers.get('location')
            if not location:
                raise ValueError('redirect without location')
            url = urljoin(url, location)
            continue
        if header_noindex(response.headers):
            raise ValueError('site requests noindex')
        if not response.ok or response.status == 304:
            raise ValueError(f'site HTTP {response.status}; registration unknown')
        return html_doc(response.text, 'company_site'), url
    raise ValueError('redirect limit exceeded')


def _statements(doc):
    # Local statements, never the full-page concatenation: a provider/client
    # number elsewhere must not borrow the site's own name or UK declaration.
    statements = []
    for node in doc.css('p, li, address, footer'):
        text = clean_text(node.text(separator=' ', strip=True))
        if len(text) <= 700 and UK_REGISTERED.search(text):
            for match in REGISTRATION.finditer(text):
                if not re.search(r'VAT\s*$', text[:match.start()], re.I):
                    statements.append((norm_ch_number(match.group(1)), text))
    return list(dict.fromkeys(statements))


def _identity_statement(statement, name, legal_name):
    # Require explicit company ownership in the same statement. A bare legal
    # name in a client/provider list does not establish a trading relationship.
    names = [legal_name, name] if norm_key(name) == norm_key(legal_name) else [legal_name]
    # Legal suffix spelling is equivalent; all other tokens remain exact.
    def literal(n):
        escaped = re.escape(n)
        return re.sub(r'(?:Limited|Ltd)$', r'(?:Limited|Ltd)', escaped, flags=re.I)
    owns = False
    for n in names:
        match = re.search(r'(?<!\w)' + literal(n) + r'(?!\w)\s*,?\s*(?:is\s+)?(?:a\s+company\s+)?registered\s+in\b', statement, re.I)
        if match and not re.search(r'\b(?:client|customer|provider|example)\s*:?\s*$', statement[:match.start()], re.I):
            owns = True
    if not owns:
        return False
    if norm_key(name) == norm_key(legal_name):
        return True
    escaped_brand, escaped_legal = r'(?<!\w)' + re.escape(name) + r'(?!\w)', r'(?<!\w)' + literal(legal_name) + r'(?!\w)'
    return bool(re.search(escaped_brand + r'\s+is\s+(?:a\s+)?trading\s+name\s+of\s+' + escaped_legal,
                          statement, re.I) or
                re.search(escaped_legal + r'\s+(?:trading\s+as|t/a)\s+' + escaped_brand, statement, re.I))


def collect_source_links(db, http, *, budget, limit=20, company_ids=()):
    """Explicit historical repair: reread at most two cited articles per company.

    This collects links, not extracted company facts. Article/company identity
    still comes from the stored named-company record and exact anchor label.
    """
    from radar.sources._article import article_links, SELECTORS
    params = []
    where = ''
    if company_ids:
        where = ' AND c.id IN (' + ','.join('?' for _ in company_ids) + ')'
        params.extend(company_ids)
    rows = db.query('SELECT c.id,c.canonical_name FROM company c WHERE c.companies_house_no IS NULL AND c.merged_into IS NULL' + where + ' ORDER BY c.last_seen DESC,c.id LIMIT ?', (*params, limit))
    result = dict(articles_read=0, companies_linked=0, unavailable=0)
    for row in rows:
        linked = False
        sources = db.query('SELECT source_key,source_url FROM company_source WHERE company_id=? ORDER BY last_seen DESC', (row['id'],))
        for source in [s for s in sources if s['source_key'] in SELECTORS][:2]:
            if budget.exhausted:
                return result
            try:
                doc, final = _page(http, source['source_url'], norm_domain(source['source_url']), budget)
                links = article_links(doc.html, source['source_key'], final)
                retain_company_links(db, row['id'], row['canonical_name'], links,
                                     source_key=source['source_key'], source_url=source['source_url'])
                result['articles_read'] += 1
                linked |= bool(db.one('SELECT 1 FROM observation WHERE company_id=? AND field=?', (row['id'], LINK_FIELD)))
            except Exception:
                result['unavailable'] += 1
        result['companies_linked'] += int(linked)
    return result


def verify_missing_crns(db, http, *, api_key, budget, limit=20, base_url=CH_API_BASE, company_ids=()):
    """Retry after 24h; at most 20 companies and 4 site pages each per run.

    All HTTP calls consume the shared enrichment budget. No credentials means
    no site calls. Evidence and reasons persist without altering verdicts.
    """
    result = dict(attempted=0, verified=0, review=0, skipped=0, verified_company_ids=[], outcomes=[])
    if not api_key or budget.exhausted:
        return result
    where = ' AND id IN (' + ','.join('?' for _ in company_ids) + ')' if company_ids else ''
    rows = db.query("SELECT * FROM company WHERE companies_house_no IS NULL AND merged_into IS NULL AND EXISTS (SELECT 1 FROM observation o WHERE o.company_id=company.id AND o.field=?)" + where + " ORDER BY COALESCE((SELECT value FROM _meta m WHERE m.key=? || company.id),'') ASC,last_seen DESC,id", (LINK_FIELD, *company_ids, RETRY_PREFIX))
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    for row in rows:
        if result['attempted'] >= limit or budget.exhausted:
            break
        cid = row['id']
        previous = db.one('SELECT value FROM _meta WHERE key=?', (RETRY_PREFIX + cid,))
        if previous and previous['value'] > cutoff:
            result['skipped'] += 1
            continue
        if row['country_iso2'] and row['country_iso2'] != 'GB':
            result['skipped'] += 1
            result['outcomes'].append({'company_id': cid, 'outcome': 'known non-UK country; unchanged'})
            continue
        result['attempted'] += 1
        db.execute('INSERT OR REPLACE INTO _meta(key,value) VALUES (?,?)', (RETRY_PREFIX + cid, now_iso()))
        reason, profile, proof_url, statement = 'no explicit matching UK registration', None, None, None
        links = []
        try:
            links = [json.loads(o['value_json']) for o in db.query('SELECT value_json FROM observation WHERE company_id=? AND field=? ORDER BY observed_at DESC', (cid, LINK_FIELD))]
            domains = {norm_domain(link['url']) for link in links}
            if len(domains) != 1 or None in domains:
                raise ValueError('ambiguous company websites')
            domain = domains.pop()
            doc, home = _page(http, links[0]['url'], domain, budget)
            pages = [(doc, home)]
            legal_urls = []
            for anchor in doc.css('a[href]'):
                url = urljoin(home, anchor.attributes['href']).split('#')[0]
                if LEGAL_LINK.search(anchor.text()) and norm_domain(url) == domain and url not in legal_urls and url != home:
                    legal_urls.append(url)
            # Inspect all bounded legal pages before accepting any number so
            # competing registrations cannot be silently selected.
            unavailable = []
            for url in legal_urls[:3]:
                try:
                    pages.append(_page(http, url, domain, budget))
                except Exception as exc:
                    if budget.exhausted:
                        raise ValueError('request budget exhausted') from exc
                    unavailable.append({'url': url, 'reason': str(exc) if isinstance(exc, ValueError) else type(exc).__name__})
            statements = [(number, text, url) for page, url in pages for number, text in _statements(page)]
            numbers = {number for number, _, _ in statements}
            if len(numbers) != 1:
                raise ValueError('ambiguous or absent registration number')
            number = numbers.pop()
            if not budget.spend():
                raise ValueError('request budget exhausted')
            response = http.get(f'{base_url.rstrip("/")}/company/{number}', auth=(api_key, ''), check_robots=False,
                                timeout=8, max_retries=0, follow_redirects=False)
            if not response.ok:
                raise ValueError('registry profile unavailable')
            profile = response.json()
            if (profile.get('company_number') != number or profile.get('company_status') != 'active'
                    or profile.get('jurisdiction') not in UK_JURISDICTIONS):
                raise ValueError('registry identity is not active UK company')
            matches = [(text, url) for _, text, url in statements
                       if _identity_statement(text, row['canonical_name'], profile.get('company_name') or '')]
            if not matches:
                raise ValueError('site identity does not match company and registry legal name')
            statement, proof_url = matches[0]
            created = profile.get('date_of_creation')
            if not created:
                raise ValueError('registry incorporation date unavailable')
            from datetime import date
            date.fromisoformat(created)
            with db.tx():
                if db.one('SELECT id FROM company WHERE companies_house_no=? AND id<>? AND merged_into IS NULL', (number, cid)):
                    raise ValueError('registration belongs to another stored company; manual review')
                # Recheck immediately before the write; never overwrite an identity.
                current = db.one('SELECT companies_house_no,country_iso2 FROM company WHERE id=?', (cid,))
                if current['companies_house_no'] or (current['country_iso2'] and current['country_iso2'] != 'GB'):
                    raise ValueError('company identity changed; manual review')
                from radar.resolve.match import Record
                from radar.resolve.merge import attach
                registry_url = CH_PROFILE_URL.format(number)
                attach(db, cid, Record(name=row['canonical_name'], ch_number=number), source_key='companies_house',
                       source_type='registry', source_url=registry_url, external_id=number)
                # Registered office establishes legal jurisdiction, never operational HQ.
                observe(db, cid, 'registered_office_address', profile.get('registered_office_address') or {},
                        source_key='companies_house', source_type='registry', source_url=registry_url)
                if created:
                    db.execute("UPDATE company SET incorporated_on=COALESCE(incorporated_on,?),age_source=CASE WHEN incorporated_on IS NULL THEN 'companies_house' ELSE age_source END,date_confidence=CASE WHEN incorporated_on IS NULL THEN 'exact' ELSE date_confidence END WHERE id=?", (created, cid))
                    observe(db, cid, 'incorporated_on', created, source_key='companies_house', source_type='registry', source_url=registry_url)
                observe(db, cid, 'verified_company_registration', {'number': number, 'statement': statement, 'registry_url': registry_url, 'unavailable_legal_pages': unavailable},
                        source_key='company_site', source_type='company_site', source_url=proof_url)
            reason = 'verified'
            result['verified'] += 1
            result['verified_company_ids'].append(cid)
        except Exception as exc:
            # Sanitized reasons: never persist authentication or response bodies.
            reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            result['review'] += 1
        observe(db, cid, 'website_identity_result', {'outcome': reason}, source_key='company_site',
                source_type='company_site', source_url=proof_url or (links[0]['url'] if links else None))
        result['outcomes'].append({'company_id': cid, 'outcome': reason})
    return result
