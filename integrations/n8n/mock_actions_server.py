#!/usr/bin/env python3
"""Loopback-only synthetic Actions API. Never imports or launches Founder Radar.

This fixture deliberately has no authentication and is not the production API.
No network client, subprocess, database, Google Sheet, Telegram or AI calls exist.
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import threading
import time
import uuid

SCENARIOS = ('done', 'partial', 'failed', 'interrupted', 'stuck', 'unknown',
             'publish-failed', 'publish-stuck', 'start-conflict')


class Fixture:
    def __init__(self, scenario='done', trace_path=None):
        if scenario not in SCENARIOS:
            raise ValueError('unknown fixture scenario')
        self.scenario = scenario
        self.trace_path = Path(trace_path) if trace_path else None
        self.trace = []
        self.jobs = {}
        self.keys = {}
        self.lock = threading.RLock()
        self.origin = 'http://127.0.0.1:18790'

    def record(self, method, path, key, body):
        record = dict(method=method, path=path, idempotency_key=key, body=body)
        self.trace.append(record)
        if self.trace_path:
            with self.trace_path.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(record) + '\n')

    def handle(self, method, path, key='', body=None):
        body = {} if body is None else body
        with self.lock:
            self.record(method, path, key, body)
            if method == 'GET' and path == '/health':
                return 200, dict(ok=True, service='n8n-mock-actions', scenario=self.scenario, synthetic=True)
            if method == 'GET' and path == '/demo/dashboard':
                return 200, dict(synthetic=True, message='MOCK dashboard. No real companies were scanned or published.')
            if method == 'GET' and path == '/_mock/trace':
                return 200, dict(synthetic=True, requests=list(self.trace))
            if method == 'POST' and path in ('/v1/jobs/search', '/v1/jobs/publish'):
                if not isinstance(body, dict):
                    return 400, dict(ok=False, error='JSON object required')
                command = path.rsplit('/', 1)[1]
                allowed = {'no_llm', 'fund', 'source', 'since'} if command == 'search' else {'send'}
                if set(body) - allowed:
                    return 400, dict(ok=False, error='Unexpected fixture request fields')
                if command == 'publish' and body.get('send') is not False:
                    return 400, dict(ok=False, error='Mock requires explicit send:false')
                if not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', key):
                    return 400, dict(ok=False, error='Idempotency-Key required')
                fingerprint = (path, json.dumps(body, sort_keys=True))
                if key in self.keys:
                    old_fingerprint, job_id = self.keys[key]
                    if old_fingerprint != fingerprint:
                        return 409, dict(ok=False, error='idempotency_conflict')
                    job = self.jobs[job_id]
                    return (202 if job['status'] in ('queued', 'running') else 200), self.accepted(job, reused=True)
                if self.scenario == 'start-conflict':
                    return 409, dict(ok=False, error='mutation_in_progress', message='Synthetic existing mutation; review instead of starting a new execution.')
                job = dict(id=uuid.uuid4().hex, command=command, status='queued',
                           exit_code=None, created_at=time.time(), updated_at=time.time(),
                           dashboard_url=self.origin + '/demo/dashboard', error=None,
                           polls=0, meta={'synthetic': True, 'kind': command})
                self.jobs[job['id']] = job
                self.keys[key] = (fingerprint, job['id'])
                return 202, self.accepted(job, reused=False)
            if method == 'GET' and path.startswith('/v1/jobs/'):
                job = self.jobs.get(path.rsplit('/', 1)[1])
                if job is None:
                    return 404, dict(ok=False, error='job not found')
                job['polls'] += 1
                job['updated_at'] = time.time()
                command = job['command']
                terminal = 'done'
                if command == 'search' and self.scenario in ('partial', 'failed', 'interrupted', 'unknown'):
                    terminal = self.scenario
                if command == 'publish' and self.scenario == 'publish-failed':
                    terminal = 'failed'
                stuck = self.scenario == 'stuck' or (command == 'publish' and self.scenario == 'publish-stuck')
                job['status'] = 'running' if stuck or job['polls'] < 2 else terminal
                if job['status'] not in ('queued', 'running'):
                    job['exit_code'] = {'done': 0, 'partial': 1, 'failed': 2}.get(job['status'])
                    job['stdout'] = 'SYNTHETIC FIXTURE: ' + command + ' ' + job['status']
                    job['stderr'] = 'SYNTHETIC publish gate BLOCK' if command == 'publish' and terminal == 'failed' else ''
                    job['error'] = None if terminal in ('done', 'partial') else 'Synthetic ' + terminal
                    job['result'] = {'synthetic': True, 'message': 'No real scan, Sheet update, paid API call, or Telegram message occurred.'}
                public = {k: v for k, v in job.items() if k != 'polls'}
                return 200, dict(ok=True, job=public)
            return 404, dict(ok=False, error='not found')

    def accepted(self, job, *, reused):
        return dict(ok=True, accepted=True, job_id=job['id'], status=job['status'],
                    poll='/v1/jobs/' + job['id'], reused=reused,
                    dashboard_url=job['dashboard_url'], synthetic=True)


def create_server(port=18790, scenario='done', trace_path=None):
    fixture = Fixture(scenario, trace_path)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def dispatch(self, method):
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if length < 0 or length > 65536:
                    raise ValueError('body too large')
                raw = self.rfile.read(length) if length else b'{}'
                body = json.loads(raw) if method == 'POST' else {}
                status, payload = fixture.handle(method, self.path,
                                                 self.headers.get('Idempotency-Key', ''), body)
            except (ValueError, json.JSONDecodeError) as exc:
                status, payload = 400, {'ok': False, 'error': str(exc)}
            raw = (json.dumps(payload) + '\n').encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            self.dispatch('GET')

        def do_POST(self):
            self.dispatch('POST')

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    fixture.origin = f'http://127.0.0.1:{server.server_port}'
    server.fixture = fixture
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=18790)
    parser.add_argument('--scenario', choices=SCENARIOS, default='done')
    parser.add_argument('--trace', type=Path, help='Optional JSONL request trace, never includes auth headers')
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('use an unprivileged loopback port, 1024..65535')
    server = create_server(args.port, args.scenario, args.trace)
    print(f'MOCK ONLY listening at http://127.0.0.1:{server.server_port}; scenario={args.scenario}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
