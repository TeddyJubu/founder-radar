"""Network provenance checks, deliberately separate from deterministic scoring.

Only ``reachable`` is proof of a usable link. ``dead`` means 404/410;
blocked and transient failures mean unknown, never a fabricated dead page.
Run checks before QA; consumers can read cached_outcome without network I/O.
Historical Innovate UK citation repair is previewed and hash-approved explicitly.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
import socket
from pathlib import Path
from urllib.parse import urlsplit, urljoin, parse_qs

import httpx
from radar.fetch.http import HttpClient, RobotsDenied, Response, user_agent
from radar.sources.innovate_uk import PUBLICATION, citation_url
from radar.sources._common import header_noindex
from radar.store.db import Db


@dataclass(frozen=True)
class LinkOutcome:
    url: str
    state: str
    final_url: str
    status: int | None
    reason: str
    checked_at: str
    expires_at: str
    cached: bool = False


def _stamp(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat()


def cached_outcome(db, url: str, *, now: datetime | None = None) -> LinkOutcome | None:
    """No socket access. An expired or absent result is unknown, not approval."""
    url = url.split('#',1)[0]
    row = db.one('SELECT * FROM source_link_check WHERE url=?', (url,))
    if row is None or datetime.fromisoformat(row['expires_at']) <= (now or datetime.now(timezone.utc)):
        return None
    return LinkOutcome(**dict(row), cached=True)


def _public(url: str, *, resolve: bool) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('URL must be public HTTP(S), without credentials')
    if parsed.port not in (None, 80, 443):
        raise ValueError('non-web port')
    host = parsed.hostname
    if host.lower() == 'localhost' or host.lower().endswith(('.localhost', '.local')):
        raise ValueError('private host')
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        addresses = [ipaddress.ip_address(info[4][0]) for info in socket.getaddrinfo(host, parsed.port or 443)] if resolve else []
    if any(not address.is_global for address in addresses):
        raise ValueError('private or reserved address')


def verify_source(db, url: str, *, http=None, now: datetime | None = None,
                  force: bool = False, resolve: bool = True, max_redirects: int = 4) -> LinkOutcome:
    """Bounded GET, respecting robots and the shared client rate limiter.

    At most five page GETs, no retries, eight-second request timeout by default.
    Each redirect is checked before following it. ``resolve=False`` is only
    for injected offline fixture clients. Production callers use the default.
    """
    now = now or datetime.now(timezone.utc)
    url = url.split('#',1)[0]  # Different workbook row fragments share one GET.
    if not force:
        previous = cached_outcome(db, url, now=now)
        if previous:
            return previous
    owned = http is None
    http = http or HttpClient(timeout=8, max_retries=0)
    current, status, state, reason = url, None, 'error', 'redirect limit exceeded'
    # HttpClient normally follows redirects itself. Here every destination must
    # pass the public-URL/robots checks; restore its setting for other callers.
    session = getattr(http, '_client', None)
    old_redirects = session.follow_redirects if session else None
    if session:
        session.follow_redirects = False
    try:
        for _ in range(max_redirects + 1):
            _public(current, resolve=resolve)
            if session:
                if http.obey_robots:
                    if not http.robots.allowed(current, user_agent()):
                        raise RobotsDenied(current)
                    delay = http.robots.crawl_delay(current, user_agent())
                    if delay:
                        http.limiter.set_delay(urlsplit(current).netloc, delay)
                http.limiter.acquire(urlsplit(current).netloc)
                http.request_count += 1
                # Read only headers; do not download a large workbook/body.
                with session.stream('GET', current, follow_redirects=False) as raw:
                    response = Response(str(raw.url),raw.status_code,'',dict(raw.headers))
            else:
                response = http.get(current)
            status = response.status
            if status in (301, 302, 303, 307, 308):
                location = response.headers.get('location')
                if not location:
                    reason = 'redirect has no destination'
                    break
                current = urljoin(current, location)
                continue
            if header_noindex(response.headers):
                state, reason = 'blocked', 'source requests noindex; verification incomplete'
            elif 200 <= status < 300:
                state, reason = 'reachable', 'GET succeeded'
            elif status in (404, 410):
                state, reason = 'dead', f'HTTP {status}'
            elif status in (401, 403, 429, 451):
                state, reason = 'blocked', f'HTTP {status}; reachability unknown'
            else:
                state, reason = 'error', f'HTTP {status}; reachability unknown'
            break
    except RobotsDenied:
        state, reason = 'blocked', 'robots policy prevents verification'
    except httpx.TimeoutException:
        state, reason = 'timeout', 'GET timed out; reachability unknown'
    except ValueError as exc:
        state, reason = 'invalid', str(exc)
    except (httpx.HTTPError, OSError) as exc:
        state, reason = 'error', f'{type(exc).__name__}; reachability unknown'
    finally:
        if session:
            session.follow_redirects = old_redirects
        if owned:
            http.close()
    ttl = timedelta(hours=24 if state == 'reachable' else 1)
    outcome = LinkOutcome(url, state, current, status, reason, _stamp(now), _stamp(now + ttl))
    db.execute('''INSERT OR REPLACE INTO source_link_check
        (url,state,final_url,status,reason,checked_at,expires_at) VALUES (?,?,?,?,?,?,?)''',
        (url,state,current,status,reason,outcome.checked_at,outcome.expires_at))
    return outcome


def repair_plan(db) -> dict:
    """Preview only fabricated Innovate UK GtR citations, never real GtR records.

    The permanent official publication links to current and historical workbooks.
    The fragment identifies the project and company to find in those files.
    All original source/external IDs and evidence values are retained.
    """
    changes = []
    for table in ('company_source', 'signal', 'observation'):
        rows = db.query(f'''SELECT rowid AS rid, company_id, source_url
            FROM {table} WHERE source_key='innovate_uk' ORDER BY rowid''')
        for row in rows:
            parsed = urlsplit(row['source_url'] or '')
            if parsed.hostname != 'gtr.ukri.org' or parsed.path != '/projects':
                continue
            reference = parse_qs(parsed.query).get('ref', [''])[0]
            if not reference:
                continue
            name = db.scalar('SELECT canonical_name FROM company WHERE id=?', (row['company_id'],)) or ''
            changes.append(dict(table=table, rowid=row['rid'], company_id=row['company_id'],
                                old=row['source_url'], new=citation_url(PUBLICATION, reference, name)))
    encoded = json.dumps(changes, sort_keys=True, separators=(',', ':')).encode()
    return {'hash': hashlib.sha256(encoded).hexdigest(), 'changes': changes}


def apply_repair(db, plan: dict, *, expected_hash: str) -> int:
    """Atomic, compare-and-review repair; changed data requires a new review."""
    with db.tx():
        actual = repair_plan(db)
        if actual != plan or actual['hash'] != expected_hash:
            raise ValueError('Repair plan changed or approval hash does not match')
        affected = set()
        for change in actual['changes']:
            table = change['table']
            db.execute(f'UPDATE {table} SET source_url=? WHERE rowid=? AND source_url=?',
                       (change['new'],change['rowid'],change['old']))
            affected.add(change['company_id'])
        for cid in affected:
            # Keep the audit trail. Changing the citation changes the card hash,
            # so an old approval cannot approve the repaired card.
            db.execute('UPDATE score_snapshot SET approved_snapshot_hash=NULL WHERE company_id=?', (cid,))
    return len(actual['changes'])


def main():
    parser = argparse.ArgumentParser(description='Preview or approve Innovate UK citation repair')
    parser.add_argument('--db', required=True, help='Explicit SQLite database path (back up before apply)')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--plan-hash')
    args = parser.parse_args()
    if not Path(args.db).is_file():
        parser.error('--db must name an existing database; no database is created')
    db = Db(args.db)
    try:
        plan = repair_plan(db)
        if args.apply:
            if not args.plan_hash:
                parser.error('--apply requires the reviewed --plan-hash')
            print(json.dumps({'updated': apply_repair(db,plan,expected_hash=args.plan_hash)}))
        else:
            print(json.dumps(plan, indent=2))
    finally:
        db.close()


if __name__ == '__main__':
    main()
