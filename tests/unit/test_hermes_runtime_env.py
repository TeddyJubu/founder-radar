"""QA uses the operator's installed Hermes without trying to update its files."""
from types import SimpleNamespace

import pytest

from radar.qa.today import HermesSubagent, TodayCard


@pytest.mark.parametrize('surface', ['today', 'publish'])
def test_qa_subprocess_disables_lazy_install_in_foreign_owned_runtime(monkeypatch, surface):
    from radar.qa import publish
    monkeypatch.setenv('HERMES_DISABLE_LAZY_INSTALLS', '0')
    monkeypatch.setenv('TEST_HERMES_SENTINEL', 'inherited-value')
    monkeypatch.setattr(publish, '_ensure_hermes_acl', lambda: None)
    captured = []

    def installed_runtime(argv, **kwargs):
        env = kwargs['env']
        captured.append(env)
        # The service account cannot update the operator-owned installation.
        assert env.get('HERMES_DISABLE_LAZY_INSTALLS') == '1'
        assert env['TEST_HERMES_SENTINEL'] == 'inherited-value'
        return SimpleNamespace(returncode=0, stdout='VERDICT: PASS\nSUMMARY: checked\nACTIONS: none\n', stderr='')

    if surface == 'today':
        result = HermesSubagent(binary='/fake/hermes', runner=installed_runtime).review(
            TodayCard(company_id='c1', name='Acme'))
        assert result.verdict == 'pass'
    else:
        monkeypatch.setattr('radar.qa.today.resolve_hermes_binary', lambda: '/fake/hermes')
        monkeypatch.setattr(publish.subprocess, 'run', installed_runtime)
        assert publish._run_hermes_publish_check({})[0] == 'pass'
    assert len(captured) == 1
    # Only the subprocess is changed; operator launch behavior stays unchanged.
    import os
    assert os.environ['HERMES_DISABLE_LAZY_INSTALLS'] == '0'
