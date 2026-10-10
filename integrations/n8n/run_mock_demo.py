#!/usr/bin/env python3
"""Import and execute the real n8n engine against loopback-only fixtures.

Requires an already installed n8n executable. Does not install software, create
API credentials, launch Radar, or connect to production. Runtime state and logs
are written only under the explicit output directory.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
import uuid

from mock_actions_server import SCENARIOS, create_server

HERE = Path(__file__).resolve().parent
EXPECTED_NODE = {
    'done': 'Result and dashboard',
    'partial': 'Scan partial - review',
    'failed': 'Scan failed - review',
    'interrupted': 'Scan failed - review',
    'unknown': 'Scan polling stopped',
    'stuck': 'Scan polling stopped',
    'publish-failed': 'Publish failed - review',
    'publish-stuck': 'Publish polling stopped',
    'start-conflict': 'Start scan job',
}


def process_tree_rss_kib(pid: int) -> int:
    """Sample current RSS, including n8n child runners (Linux only)."""
    seen, total, pending = set(), 0, [pid]
    while pending:
        child = pending.pop()
        if child in seen:
            continue
        seen.add(child)
        try:
            status = Path(f'/proc/{child}/status').read_text()
            total += next((int(line.split()[1]) for line in status.splitlines()
                           if line.startswith('VmRSS:')), 0)
            pending.extend(map(int, Path(f'/proc/{child}/task/{child}/children').read_text().split()))
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            pass
    return total


def run_command(argv: list[str], env: dict[str, str], log: Path, timeout=180):
    start, peak = time.monotonic(), 0
    with log.open('w', encoding='utf-8') as output:
        process = subprocess.Popen(argv, env=env, stdout=output, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        try:
            while process.poll() is None:
                peak = max(peak, process_tree_rss_kib(process.pid))
                if time.monotonic() - start > timeout:
                    raise TimeoutError(f'Local n8n command exceeded {timeout}s; see {log}')
                time.sleep(0.2)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
    return dict(exit_code=process.returncode, seconds=round(time.monotonic() - start, 2),
                sampled_peak_process_tree_rss_mib=round(peak / 1024, 1))


def execution_result(text: str) -> dict:
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character == '{' and (index == 0 or text[index - 1] == '\n'):
            try:
                value, _ = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and isinstance(value.get('data'), dict):
                if 'resultData' in value['data']:
                    return value
    raise ValueError('n8n did not return a complete execution result')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--n8n', type=Path, required=True, help='Installed n8n executable')
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--scenarios', nargs='+', choices=SCENARIOS, default=list(SCENARIOS))
    args = parser.parse_args()
    n8n = args.n8n.resolve()
    if not n8n.is_file():
        parser.error('n8n executable not found; install the official pinned package first')
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    home = output_dir / 'n8n-home'
    home.mkdir(exist_ok=True)
    env = {
        'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': str(home),
        'N8N_USER_FOLDER': str(home), 'N8N_DIAGNOSTICS_ENABLED': 'false',
        'N8N_VERSION_NOTIFICATIONS_ENABLED': 'false', 'N8N_TEMPLATES_ENABLED': 'false',
        'N8N_PERSONALIZATION_ENABLED': 'false', 'N8N_COMMUNITY_PACKAGES_ENABLED': 'false',
        'N8N_PUBLIC_API_DISABLED': 'true', 'N8N_HOST': '127.0.0.1',
        'N8N_LISTEN_ADDRESS': '127.0.0.1', 'N8N_LOG_LEVEL': 'info',
        'N8N_RUNNERS_BROKER_LISTEN_ADDRESS': '127.0.0.1',
    }
    workflow = json.loads((HERE / 'founder-radar.mock.workflow.json').read_text())
    http_nodes = [node for node in workflow['nodes'] if node['type'] == 'n8n-nodes-base.httpRequest']
    if workflow.get('active') or any(node['parameters'].get('authentication') != 'none' for node in http_nodes):
        raise ValueError('Only the inactive credential-free mock workflow is allowed')
    reports = []
    for scenario in args.scenarios:
        case = output_dir / (scenario + '-' + uuid.uuid4().hex[:8])
        case.mkdir()
        server = create_server(0, scenario, case / 'requests.jsonl')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            document = json.loads(json.dumps(workflow))
            workflow_id = 'frMock' + uuid.uuid4().hex[:14]
            document['id'] = workflow_id
            document['name'] += ' - ' + scenario
            config = next(node for node in document['nodes'] if node['name'] == 'Configure')
            for assignment in config['parameters']['assignments']['assignments']:
                if assignment['name'] == 'api_base':
                    assignment['value'] = f'http://127.0.0.1:{server.server_port}'
            source = case / 'workflow.json'
            source.write_text(json.dumps(document, indent=2) + '\n')
            imported = run_command([str(n8n), 'import:workflow', '--input=' + str(source)],
                                   env, case / 'import.log')
            if imported['exit_code'] != 0:
                raise RuntimeError(f'Workflow import failed; see {case / "import.log"}')
            runtime = run_command([str(n8n), 'execute', '--id=' + workflow_id, '--rawOutput'],
                                  env, case / 'execution.log')
            execution = execution_result((case / 'execution.log').read_text())
            result = execution['data']['resultData']
            run_data = result.get('runData', {})
            expected = EXPECTED_NODE[scenario]
            assert expected in run_data, f'{scenario}: expected node {expected!r}; got {list(run_data)}'
            assert bool(result.get('error')) == (scenario != 'done'), f'{scenario}: wrong execution outcome'
            requests = list(server.fixture.trace)
            posts = [entry for entry in requests if entry['method'] == 'POST']
            publish = [entry for entry in posts if entry['path'] == '/v1/jobs/publish']
            expected_publish = scenario in ('done', 'publish-failed', 'publish-stuck')
            assert bool(publish) == expected_publish, f'{scenario}: unexpected publish request count'
            assert all(entry['body'].get('send') is False for entry in publish)
            assert all(entry['idempotency_key'] for entry in posts)
            keys = [entry['idempotency_key'] for entry in posts if entry['path'] == '/v1/jobs/search']
            assert len(set(keys)) == 1, f'{scenario}: HTTP retries changed the scan key'
            report = dict(scenario=scenario, passed=True, workflow_id=workflow_id,
                          expected_node=expected, last_node=result.get('lastNodeExecuted'),
                          scan_posts=len(keys), publish_posts=len(publish),
                          requests=len(requests), runtime=runtime, logs=str(case))
            reports.append(report)
            print(json.dumps(report), flush=True)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    summary = {'synthetic_only': True, 'scenarios': reports,
               'limitations': 'Sampled CLI execution RSS is not sustained production capacity. No real Radar scan, credentials, Sheet, paid service, or Telegram was used.'}
    (output_dir / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(f'All {len(reports)} mock scenarios passed; summary: {output_dir / "summary.json"}', flush=True)


if __name__ == '__main__':
    main()
