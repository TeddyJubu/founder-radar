#!/usr/bin/env python3
"""Offline structural/contract tests, not a substitute for n8n runtime tests."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import unittest
from urllib.parse import urlparse

from generate_workflows import build_workflow
from mock_actions_server import Fixture, create_server

HERE = Path(__file__).resolve().parent


def by_name(workflow):
    return {n['name']: n for n in workflow['nodes']}


def configuration(workflow):
    return {a['name']: a['value'] for a in by_name(workflow)['Configure']['parameters']['assignments']['assignments']}


def condition(workflow, name, data, *, run_index=0, config=None, deadline=200000):
    expression = by_name(workflow)[name]['parameters']['conditions']['conditions'][0]['leftValue']
    code = expression.removeprefix('={{ ').removesuffix(' }}')
    context = {'code': code, 'data': data, 'runIndex': run_index,
               'config': config or configuration(workflow), 'deadline': deadline}
    # Run the actual export's JavaScript expression in a small local Node context.
    script = """
const c = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const lookup = name => ({first: () => ({json: name === 'Configure' ? c.config : {deadline_ms: c.deadline}})});
const value = new Function('$json', '$runIndex', '$', 'Date', 'return (' + c.code + ')')(
  c.data, c.runIndex, lookup, {now: () => 100000});
process.stdout.write(JSON.stringify(value));
"""
    result = subprocess.run(['node', '-e', script], input=json.dumps(context), text=True,
                            check=True, capture_output=True, timeout=5)
    return json.loads(result.stdout)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.production = build_workflow()
        self.mock = build_workflow(mock=True)

    def test_committed_exports_are_reproducible(self):
        for filename, expected in [('founder-radar.workflow.json', self.production),
                                   ('founder-radar.mock.workflow.json', self.mock)]:
            self.assertEqual((HERE / filename).read_text(), json.dumps(expected, indent=2) + '\n')

    def test_inactive_manual_defaults_and_single_scheduler(self):
        for workflow in (self.production, self.mock):
            nodes = by_name(workflow)
            self.assertFalse(workflow['active'])
            self.assertEqual(workflow['settings']['timezone'], 'Europe/London')
            self.assertEqual(workflow['settings']['executionOrder'], 'v1')
            self.assertLessEqual(workflow['settings']['executionTimeout'], 9000)
            self.assertTrue(nodes['06:30 London - DISABLED']['disabled'])
            self.assertEqual(nodes['06:30 London - DISABLED']['parameters']['rule']['interval'][0]['expression'], '0 30 6 * * *')
            self.assertIs(configuration(workflow)['send_telegram'], False)
            self.assertEqual(workflow['pinData'], {})

    def test_graph_integrity_and_only_visible_builtin_nodes(self):
        allowed = {'manualTrigger', 'scheduleTrigger', 'set', 'if', 'httpRequest', 'wait', 'stopAndError', 'stickyNote'}
        for workflow in (self.production, self.mock):
            nodes = by_name(workflow)
            self.assertEqual(len(nodes), len(workflow['nodes']))
            self.assertEqual(len({n['id'] for n in workflow['nodes']}), len(nodes))
            for n in workflow['nodes']:
                self.assertTrue(n['type'].startswith('n8n-nodes-base.'))
                self.assertIn(n['type'].split('.')[-1], allowed)
                self.assertNotIn('continueOnFail', n)
                self.assertNotIn('onError', n)
            for source, connections in workflow['connections'].items():
                self.assertIn(source, nodes)
                for output in connections['main']:
                    for edge in output:
                        self.assertIn(edge['node'], nodes)
                        self.assertEqual(edge['type'], 'main')
                        self.assertEqual(edge['index'], 0)
            self.assertNotIn('Pipeline stays in Python', workflow['connections'])

    def test_auth_is_credentials_only_and_mock_is_separate(self):
        for workflow, is_mock in ((self.production, False), (self.mock, True)):
            http_nodes = [n for n in workflow['nodes'] if n['type'].endswith('.httpRequest')]
            self.assertEqual(len(http_nodes), 4)
            for node in http_nodes:
                p = node['parameters']
                self.assertFalse(p['options']['redirect']['redirect']['followRedirects'])
                self.assertLessEqual(p['options']['timeout'], 15000)
                self.assertLessEqual(node['maxTries'], 3)
                if is_mock:
                    self.assertEqual(p['authentication'], 'none')
                    self.assertNotIn('credentials', node)
                else:
                    self.assertEqual(p['authentication'], 'genericCredentialType')
                    self.assertEqual(p['genericAuthType'], 'httpBearerAuth')
                    self.assertEqual(node['credentials']['httpBearerAuth']['id'], 'REPLACE_WITH_LOCAL_BEARER_CREDENTIAL_ID')
                headers = p.get('headerParameters', {}).get('parameters', [])
                self.assertFalse(any(h['name'].lower() == 'authorization' for h in headers))
                if p['method'] == 'POST':
                    self.assertEqual(headers[0]['name'], 'Idempotency-Key')
                    self.assertIn('.execution_key', headers[0]['value'])
                self.assertNotIn('allowUnauthorizedCerts', p['options'])

    def test_mock_origin_and_send_guard(self):
        config = configuration(self.mock)
        config['execution_key'] = 'test:execution'
        self.assertTrue(condition(self.mock, 'Configuration is safe?', config))
        for origin in ('https://example.com', 'http://localhost:18790', 'http://127.0.0.1.evil.test:18790',
                       'http://127.0.0.1:18790/path', 'http://127.0.0.1:99999', 'http://127.0.0.1:18790@evil.test'):
            self.assertFalse(condition(self.mock, 'Configuration is safe?', dict(config, api_base=origin)))
        self.assertFalse(condition(self.mock, 'Configuration is safe?', dict(config, send_telegram=True)))
        self.assertFalse(condition(self.mock, 'Configuration is safe?', dict(config, scan_max_polls=0)))
        config = configuration(self.production)
        config['execution_key'] = 'test:execution'
        self.assertFalse(condition(self.production, 'Configuration is safe?', config))
        self.assertTrue(condition(self.production, 'Configuration is safe?', dict(config, api_base='https://actions.example.com')))

    def test_only_successful_done_jobs_pass(self):
        for label in ('Scan', 'Publish'):
            for status in ('queued', 'running', 'done', 'partial', 'failed', 'interrupted', 'mystery', None):
                for exit_code in (None, 0, 1, 2):
                    data = {'ok': True, 'job': {'status': status, 'exit_code': exit_code}}
                    self.assertEqual(condition(self.mock, label+' done?', data), status == 'done' and exit_code == 0)
            self.assertFalse(condition(self.mock, label+' done?', {'ok': False, 'job': {'status': 'done', 'exit_code': 0}}))

    def test_partial_failed_and_interrupted_are_explicit_stops(self):
        workflow = self.mock
        for label in ('Scan', 'Publish'):
            data = {'ok': True, 'job': {'status': 'partial', 'exit_code': 1}}
            self.assertTrue(condition(workflow, label+' partial?', data))
            for status in ('failed', 'interrupted'):
                self.assertTrue(condition(workflow, label+' failed or interrupted?', {'job': {'status': status}}))
            for terminal in (label+' partial - review', label+' failed - review', label+' polling stopped', label+' response invalid'):
                edges = workflow['connections'][terminal]['main'][0]
                self.assertEqual([e['node'] for e in edges], ['Stop - operator review'])
        self.assertNotIn('Stop - operator review', workflow['connections'])
        self.assertEqual(workflow['connections']['Scan done?']['main'][0][0]['node'], 'Run publish checks?')
        self.assertEqual(workflow['connections']['Run publish checks?']['main'][0][0]['node'], 'Start gated publish job')

    def test_polling_is_bounded_and_rejects_unknown_status(self):
        config = configuration(self.mock)
        for label in ('Scan', 'Publish'):
            name = label+' pending within limits?'
            self.assertTrue(condition(self.mock, name, {'ok': True, 'job': {'status': 'running'}}, run_index=3))
            self.assertFalse(condition(self.mock, name, {'ok': True, 'job': {'status': 'running'}}, run_index=4))
            self.assertFalse(condition(self.mock, name, {'ok': True, 'job': {'status': 'running'}}, deadline=99999))
            for status in ('done', 'partial', 'failed', 'interrupted', 'unknown', None):
                self.assertFalse(condition(self.mock, name, {'ok': True, 'job': {'status': status}}))

    def test_no_extra_executable_pipeline_or_telegram_nodes(self):
        text = json.dumps(self.production)
        self.assertNotIn('/v1/publish-check', text)
        self.assertNotIn('/v1/today-qa', text)
        self.assertNotIn('n8n-nodes-base.telegram', text)
        self.assertNotIn('n8n-nodes-base.executeCommand', text)
        result = by_name(self.production)['Result and dashboard']['parameters']['jsonOutput']
        self.assertIn('publish_checks_passed_no_telegram', result)
        self.assertIn('$json.job', result)
        self.assertIn('dashboard_url', result)
        self.assertNotIn('run_id:', result)


class MockContractTests(unittest.TestCase):
    def test_fixture_is_loopback_only(self):
        server = create_server(0)
        try:
            self.assertEqual(server.server_address[0], '127.0.0.1')
        finally:
            server.server_close()

    def test_idempotent_retry_and_conflict(self):
        fixture = Fixture()
        status, first = fixture.handle('POST', '/v1/jobs/search', 'execution:search', {'no_llm': False})
        self.assertEqual(status, 202)
        status, repeated = fixture.handle('POST', '/v1/jobs/search', 'execution:search', {'no_llm': False})
        self.assertTrue(repeated['reused'])
        self.assertEqual(first['job_id'], repeated['job_id'])
        self.assertEqual(len(fixture.jobs), 1)
        self.assertEqual(fixture.handle('POST', '/v1/jobs/search', 'execution:search', {'no_llm': True})[0], 409)

    def test_fixture_refuses_actual_send(self):
        for body in ({}, {'send': True}, {'send': 'false'}, {'send': 0}):
            self.assertEqual(Fixture().handle('POST', '/v1/jobs/publish', 'execution:publish', body)[0], 400)
        self.assertEqual(Fixture().handle('POST', '/v1/jobs/publish', 'execution:publish', {'send': False})[0], 202)

    def test_fixture_statuses_and_trace_are_honest(self):
        for scenario in ('done', 'partial', 'failed', 'interrupted', 'stuck', 'unknown'):
            fixture = Fixture(scenario)
            _, accepted = fixture.handle('POST', '/v1/jobs/search', 'execution:search', {'no_llm': False})
            self.assertEqual(fixture.handle('GET', accepted['poll'])[1]['job']['status'], 'running')
            job = fixture.handle('GET', accepted['poll'])[1]['job']
            self.assertEqual(job['status'], 'running' if scenario == 'stuck' else scenario)
            self.assertTrue(job['meta']['synthetic'])
            self.assertEqual(urlparse(job['dashboard_url']).hostname, '127.0.0.1')
            self.assertEqual(len(fixture.trace), 3)


if __name__ == '__main__':
    unittest.main(verbosity=2)
