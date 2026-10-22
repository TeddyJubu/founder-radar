#!/usr/bin/env python3
"""Generate reviewable, deterministic n8n exports. No network or credentials."""
from __future__ import annotations

import json
from pathlib import Path
import uuid

HERE = Path(__file__).resolve().parent
NAMESPACE = uuid.UUID('68663ac0-605a-4b9c-86c2-69ac669bc3e8')
VERSIONS = {'manualTrigger': 1, 'scheduleTrigger': 1.2, 'set': 3.4,
            'if': 2.2, 'httpRequest': 4.2, 'wait': 1.1,
            'stopAndError': 1, 'stickyNote': 1}


def uid(name: str) -> str:
    return str(uuid.uuid5(NAMESPACE, name))


def expr(code: str) -> str:
    return '={{ ' + code + ' }}'


def build_workflow(*, mock: bool = False) -> dict:
    nodes, connections = [], {}

    def node(name, kind, parameters, x, y, **extra):
        n = dict(id=uid(name), name=name, type=f'n8n-nodes-base.{kind}',
                 typeVersion=VERSIONS[kind], position=[x, y], parameters=parameters)
        n.update(extra)
        nodes.append(n)
        return n

    def link(source, target, output=0):
        outputs = connections.setdefault(source, {'main': []})['main']
        while len(outputs) <= output:
            outputs.append([])
        outputs[output].append({'node': target, 'type': 'main', 'index': 0})

    def fields(name, values, x, y, **extra):
        assignments = []
        for key, value in values.items():
            if isinstance(value, tuple):
                value, field_type = value
            else:
                field_type = ('boolean' if isinstance(value, bool) else
                              'number' if isinstance(value, (int, float)) else 'string')
            assignments.append(dict(id=uid(name + ':' + key), name=key,
                                    value=value, type=field_type))
        return node(name, 'set', {'assignments': {'assignments': assignments}, 'options': {}}, x, y, **extra)

    def condition(name, code, x, y):
        return node(name, 'if', {'conditions': {
            'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'strict', 'version': 2},
            'conditions': [{'id': uid(name + ':condition'), 'leftValue': expr(code),
                            'rightValue': '', 'operator': {'type': 'boolean', 'operation': 'true', 'singleValue': True}}],
            'combinator': 'and'}, 'options': {}}, x, y)

    def http(name, method, url, x, y, body=None, key=None):
        parameters = dict(method=method, url=expr(url), options={
            'timeout': 15000,
            'redirect': {'redirect': {'followRedirects': False}},
            'response': {'response': {'responseFormat': 'json'}}})
        if mock:
            parameters['authentication'] = 'none'
        else:
            parameters.update(authentication='genericCredentialType', genericAuthType='httpBearerAuth')
        if body is not None:
            parameters.update(sendBody=True, specifyBody='json', jsonBody=expr(body))
        if key:
            parameters.update(sendHeaders=True, headerParameters={'parameters': [
                {'name': 'Idempotency-Key', 'value': expr(key)}]})
        n = node(name, 'httpRequest', parameters, x, y,
                 retryOnFail=True, maxTries=3, waitBetweenTries=2000)
        if not mock:
            n['credentials'] = {'httpBearerAuth': {
                'id': 'REPLACE_WITH_LOCAL_BEARER_CREDENTIAL_ID',
                'name': 'Founder Radar Actions - select your Bearer credential'}}
        return n

    def result(name, outcome, message, x, y, stage=None):
        if stage:
            job = '$json.job || {}'
            code = ('({outcome: ' + json.dumps(outcome) + ', message: ' + json.dumps(message) +
                    ", job: " + job + ", dashboard_url: $json.job?.dashboard_url || null," +
                    " telegram_requested: " + ("$('Configure').first().json.send_telegram" if stage == 'Publish' else 'false') +
                    ", scan_job_id: $('Scan poll clock').first().json.job_id})")
        else:
            code = '({outcome: ' + json.dumps(outcome) + ', message: ' + json.dumps(message) + '})'
        return node(name, 'set', {'mode': 'raw', 'jsonOutput': expr('JSON.stringify(' + code + ')'), 'options': {}}, x, y)

    node('Read first', 'stickyNote', {'content': (
        '## Founder Radar: ' + ('LOOPBACK MOCK DEMO' if mock else 'authenticated API orchestration') + '\n'
        'Inactive export. Schedule is disabled. Manual execution only until explicitly configured.\n'
        + ('This variant calls only the local fixture server. All job output is synthetic. No Radar CLI, API key, paid services, Sheet, or Telegram.\n' if mock else
           'Set your HTTPS API origin and select a Bearer credential on all four HTTP nodes. Placeholder host cannot run. Never paste a token into this workflow.\n') +
        'send_telegram=false. A successful publish with send=false means checks completed, NOT a Telegram notification.\n'
        'Search already writes SQLite, runs Today QA, and syncs the Sheet; it is NOT a dry run. Publish may heal/rescore, rerun QA, and sync changes even with send=false.\n'
        'Only a done/exit-0 scan reaches optional gated publish. Partial, failed, interrupted, unknown, and timed-out states stop for review.\n'
        'Never enable this schedule beside founder-radar.timer. Scheduling/cutover is a separate approved operation.'),
        'height': 410, 'width': 840, 'color': 5}, -600, -600)
    node('Pipeline stays in Python', 'stickyNote', {'content': (
        '## Real boundary, not extra executable stages\n'
        'n8n = trigger, configuration, authenticated start, Wait/poll, status branching, result.\n'
        'Python search = source discovery → company extraction/identity → verification → deterministic fund gates/scoring → Today QA → SQLite/Sheet sync.\n'
        'Python publish = pre-publish gate/heal → Today QA → gate again → optional Telegram dashboard ping.\n'
        'These internals are shown here only as an explanation. n8n does not recreate scoring, source adapters, QA, or state stores.\n'
        'The checked-out base is 60f2067 (2026-09-30). An October 3 production QA-cache fix may not be in this branch. Reconcile production code before any deployment.'),
        'height': 320, 'width': 840, 'color': 4}, 320, -600)
    node('Retry and timeout safety', 'stickyNote', {'content': (
        '## Check the same job before retrying\n'
        'HTTP retries use the same execution_key plus :search / :publish.\n'
        'A fresh Manual Trigger run recomputes its execution key and can start NEW work. n8n 2.42 retries normally retain completed Configure output; inspect that key and the original job instead of assuming every retry is equivalent.\n'
        'First inspect/poll the original job ID. For an approved retry of the same logical operation, replace Configure.execution_key with its exact original value and keep the same request body.\n'
        'The polling budget and overall workflow timeout stop n8n waiting; they do not cancel the server-side job. Never assume timeout means no side effects.'),
        'height': 290, 'width': 840, 'color': 3}, 1240, -600)
    node('Manual start', 'manualTrigger', {}, -620, 0)
    node('06:30 London - DISABLED', 'scheduleTrigger',
         {'rule': {'interval': [{'field': 'cronExpression', 'expression': '0 30 6 * * *'}]}},
         -620, 200, disabled=True, notes='Keep disabled while systemd founder-radar.timer owns the morning scan.', notesInFlow=True)
    config = dict(api_base='http://127.0.0.1:18790' if mock else 'https://configure-your-actions-host.invalid',
                  publish_enabled=True, send_telegram=False, no_llm=False,
                  scan_poll_seconds=1 if mock else 30, scan_max_polls=5 if mock else 140,
                  scan_timeout_seconds=30 if mock else 3900,
                  publish_poll_seconds=1 if mock else 15, publish_max_polls=5 if mock else 60,
                  publish_timeout_seconds=30 if mock else 900,
                  execution_key=(expr("'founder-radar:' + $workflow.id + ':' + $execution.id"), 'string'))
    fields('Configure', config, -360, 0, notes='No API token here. send_telegram=false is the default. publish_enabled=false stops after a successful scan. To reconcile an approved retry, replace execution_key with the original execution value; new executions otherwise get new keys.', notesInFlow=True)
    cfg = "$('Configure').first().json"
    if mock:
        origin_check = "/^http:\\/\\/127\\.0\\.0\\.1:([1-9][0-9]{3,4})$/.test($json.api_base) && Number($json.api_base.split(':').pop()) <= 65535 && $json.send_telegram === false"
    else:
        origin_check = "/^https:\\/\\/[A-Za-z0-9.-]+(?::[0-9]{1,5})?$/.test($json.api_base) && !$json.api_base.includes('.invalid')"
    condition('Configuration is safe?', origin_check +
              " && typeof $json.execution_key === 'string' && /^[A-Za-z0-9._:-]{1,120}$/.test($json.execution_key)" +
              " && typeof $json.send_telegram === 'boolean' && typeof $json.publish_enabled === 'boolean'" +
              " && $json.scan_max_polls >= 1 && $json.scan_max_polls <= 240" +
              " && $json.publish_max_polls >= 1 && $json.publish_max_polls <= 120" +
              " && $json.scan_poll_seconds >= 1 && $json.scan_poll_seconds <= 60" +
              " && $json.publish_poll_seconds >= 1 && $json.publish_poll_seconds <= 60" +
              " && $json.scan_timeout_seconds >= 1 && $json.scan_timeout_seconds <= 7200" +
              " && $json.publish_timeout_seconds >= 1 && $json.publish_timeout_seconds <= 1800", -100, 0)
    result('Configuration blocked', 'configuration_blocked',
           'Configure a valid API origin and bounded polling settings before running. The mock permits only literal 127.0.0.1 and send=false.', -100, 360)
    http('Start scan job', 'POST', cfg + ".api_base + '/v1/jobs/search'", 180, 0,
         body="JSON.stringify({no_llm: " + cfg + ".no_llm})", key=cfg + ".execution_key + ':search'")
    link('Manual start', 'Configure')
    link('06:30 London - DISABLED', 'Configure')
    link('Configure', 'Configuration is safe?')
    link('Configuration is safe?', 'Start scan job')
    link('Configuration is safe?', 'Configuration blocked', 1)

    def stage(label, start_name, x, y):
        lower = label.lower()
        accepted = f'{label} accepted?'
        condition(accepted, "$json.ok === true && $json.accepted === true && /^[a-f0-9]{32}$/.test($json.job_id || '')", x, y)
        fields(f'{label} poll clock', {
            'job_id': (expr('$json.job_id'), 'string'),
            'deadline_ms': (expr(f'Date.now() + {cfg}.{lower}_timeout_seconds * 1000'), 'number')}, x+260, y)
        node(f'Wait for {lower}', 'wait', {'resume': 'timeInterval',
             'amount': expr(f'{cfg}.{lower}_poll_seconds'), 'unit': 'seconds'}, x+520, y,
             webhookId=uid(f'{label} wait'))
        http(f'Poll {lower} job', 'GET', cfg + ".api_base + '/v1/jobs/' + $('" + label + " poll clock').first().json.job_id", x+780, y)
        condition(f'{label} done?', "$json.ok === true && $json.job?.status === 'done' && $json.job?.exit_code === 0", x+1040, y)
        condition(f'{label} partial?', "$json.job?.status === 'partial'", x+1040, y+220)
        condition(f'{label} failed or interrupted?', "['failed','interrupted'].includes($json.job?.status)", x+1040, y+440)
        condition(f'{label} pending within limits?', "$json.ok === true && ['queued','running'].includes($json.job?.status)" +
                  f" && $runIndex + 1 < {cfg}.{lower}_max_polls && Date.now() < $('{label} poll clock').first().json.deadline_ms", x+780, y+660)
        result(f'{label} partial - review', lower+'_partial',
               f'{label} finished partially. Stop and inspect the returned job output/dashboard; no automatic continuation or resubmission.', x+1340, y+220, label)
        result(f'{label} failed - review', lower+'_failed_or_interrupted',
               f'{label} failed or was interrupted. Inspect job.error/stdout/stderr. An interrupted mutation may have had side effects; do not automatically submit a new job.', x+1340, y+440, label)
        result(f'{label} polling stopped', lower+'_polling_stopped',
               'Polling limit reached or the response/status is invalid. The server job may still be running; stopping this workflow does not cancel it. Check this same job before taking any further action.', x+500, y+880, label)
        result(f'{label} response invalid', lower+'_acceptance_invalid',
               'The start response did not contain a valid accepted job. Inspect the start HTTP output; do not infer completion or automatically create a fresh job.', x, y+880)
        link(start_name, accepted)
        link(accepted, f'{label} poll clock')
        link(accepted, f'{label} response invalid', 1)
        link(f'{label} poll clock', f'Wait for {lower}')
        link(f'Wait for {lower}', f'Poll {lower} job')
        link(f'Poll {lower} job', f'{label} done?')
        link(f'{label} done?', f'{label} partial?', 1)
        link(f'{label} partial?', f'{label} partial - review')
        link(f'{label} partial?', f'{label} failed or interrupted?', 1)
        link(f'{label} failed or interrupted?', f'{label} failed - review')
        link(f'{label} failed or interrupted?', f'{label} pending within limits?', 1)
        link(f'{label} pending within limits?', f'Wait for {lower}')
        link(f'{label} pending within limits?', f'{label} polling stopped', 1)

    stage('Scan', 'Start scan job', 440, 0)
    condition('Run publish checks?', cfg + '.publish_enabled === true', 1780, 0)
    result('Scan only - dashboard', 'scan_done_publish_skipped',
           'Scan finished successfully. Publish checks were disabled. No Telegram send was requested.', 2050, -220, 'Scan')
    http('Start gated publish job', 'POST', cfg + ".api_base + '/v1/jobs/publish'", 2050, 0,
         body='JSON.stringify({send: ' + cfg + '.send_telegram})', key=cfg + ".execution_key + ':publish'")
    link('Scan done?', 'Run publish checks?')
    link('Run publish checks?', 'Start gated publish job')
    link('Run publish checks?', 'Scan only - dashboard', 1)
    stage('Publish', 'Start gated publish job', 2310, 0)
    node('Result and dashboard', 'set', {'mode': 'raw', 'jsonOutput': expr("JSON.stringify({" +
         "outcome: $('Configure').first().json.send_telegram ? 'publish_completed_send_requested' : 'publish_checks_passed_no_telegram'," +
         "message: $('Configure').first().json.send_telegram ? 'Publish completed with send requested. See the returned CLI output for delivery confirmation.' : 'Publish checks completed successfully. No Telegram notification was requested.'," +
         "scan_job_id: $('Scan poll clock').first().json.job_id, publish_job_id: $json.job.id," +
         "dashboard_url: $json.job.dashboard_url || null, telegram_requested: $('Configure').first().json.send_telegram," +
         "job: $json.job})"), 'options': {}}, 3650, 0)
    link('Publish done?', 'Result and dashboard')
    node('Stop - operator review', 'stopAndError', {'errorType': 'errorMessage', 'errorMessage': expr('JSON.stringify($json)')}, 2050, 1180)
    for n in nodes:
        if n['name'].endswith((' - review', ' polling stopped', ' response invalid')) or n['name'] == 'Configuration blocked':
            link(n['name'], 'Stop - operator review')
    return {
        'name': 'Founder Radar - ' + ('LOOPBACK MOCK DEMO' if mock else 'scan and gated publish'),
        'nodes': nodes, 'connections': connections, 'active': False,
        'settings': {'executionOrder': 'v1', 'timezone': 'Europe/London', 'executionTimeout': 9000,
                     'saveDataErrorExecution': 'all', 'saveDataSuccessExecution': 'all',
                     'saveExecutionProgress': True, 'saveManualExecutions': True},
        'pinData': {}, 'tags': [],
    }


def main():
    for mock, filename in [(False, 'founder-radar.workflow.json'), (True, 'founder-radar.mock.workflow.json')]:
        path = HERE / filename
        path.write_text(json.dumps(build_workflow(mock=mock), indent=2) + '\n', encoding='utf-8')
        print(path)


if __name__ == '__main__':
    main()
