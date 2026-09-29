from types import SimpleNamespace

import pytest

from radar.extract.grounding import ground
from radar.extract.schema import blank
from radar.sources import fetch_all
from radar.sources.base import FetchContext, RawItem


def test_unrelated_verbatim_quote_cannot_support_invented_fields():
    text = 'Acme develops packaging. The industry is growing rapidly. OtherCo raised £2m in a seed round.'
    record = blank(is_about_single_company=True, company_name='InventedCo',
                   evidence_quote_company='Acme develops packaging.',
                   amount_raised_gbp=9_000_000,
                   evidence_quote_amount='The industry is growing rapidly.',
                   stage='series_a', evidence_quote_stage='The industry is growing rapidly.',
                   founders=[{'name':'Invented Person', 'evidence_quote':'Acme develops packaging.'}])
    got = ground(record, text).extraction
    assert got.company_name is None
    assert got.amount_raised_gbp is None
    assert got.stage is None
    assert got.founders == []


def test_other_company_amount_quote_does_not_support_subject():
    record = blank(is_about_single_company=True, company_name='Acme',
                   evidence_quote_company='Acme develops packaging.', amount_raised_gbp=2_000_000,
                   evidence_quote_amount='OtherCo raised £2m in a seed round.')
    assert ground(record, 'Acme develops packaging. OtherCo raised £2m in a seed round.').extraction.amount_raised_gbp is None


def test_partial_portfolio_failure_is_visible(db, config):
    item = RawItem('test','https://example.org','1',None,'Acme')
    adapter = SimpleNamespace(key='test', last_failures=['dsw: HTTP 503'], fetch=lambda ctx: [item])
    result = fetch_all(FetchContext(http=None, config=config, db=db), [adapter])
    assert result.sources[0].status == 'degraded'
    assert 'dsw' in result.sources[0].error
    assert result.items == [item]


def test_changed_fingerprint_is_reported(db, config):
    item = RawItem('test','https://example.org','1',None,'Acme')
    adapter = SimpleNamespace(key='test', last_fingerprint='first', fetch=lambda ctx: [item])
    ctx = FetchContext(http=None, config=config, db=db)
    assert fetch_all(ctx, [adapter]).sources[0].status == 'ok'
    adapter.last_fingerprint = 'changed'
    got = fetch_all(ctx, [adapter])
    assert got.sources[0].status == 'layout_changed'
    assert got.items == []


def test_noindex_article_never_reaches_reader():
    from radar.extract import extract_html, ExtractContext
    got = extract_html(url='https://example.org/article', title='Acme raises £2m',
                       html='<html><head><meta name="robots" content="noindex"></head><body>Acme raises £2m in seed funding.</body></html>',
                       ctx=ExtractContext(use_llm=False))
    assert not got.is_usable
    assert got.prefilter_reason == 'noindex'


def test_dry_run_uses_isolated_database(db, config, monkeypatch):
    import radar.pipeline as pipeline
    seen = []
    def fetch(target, *args, **kwargs):
        seen.append(target)
        target.set_meta('dry-run-write', 'should not survive')
        raise RuntimeError('stop after fetch write')
    monkeypatch.setattr(pipeline, 'fetch_stage', fetch)
    pipeline.run_pipeline(db, config=config, dry_run=True, http=object(), use_llm=False)
    assert db.get_meta('dry-run-write') is None
    assert seen[0] is not db


def test_noindex_response_header_blocks_fetch():
    import httpx
    from radar.fetch.http import HttpClient, RobotsDenied
    client = HttpClient(transport=httpx.MockTransport(lambda request: httpx.Response(
        200, headers={"X-Robots-Tag":"noindex"}, text="private page")),
        obey_robots=False, sleep=lambda _: None)
    with client:
        with pytest.raises(RobotsDenied, match="noindex"):
            client.get("https://example.org/private")


def test_noindex_source_html_blocks_parsing():
    from radar.sources._common import html_doc
    from radar.sources.base import SourceBlocked
    with pytest.raises(SourceBlocked, match="noindex"):
        html_doc('<meta name="robots" content="noindex"><p>Acme</p>', 'test')


def test_founder_of_another_company_is_not_attached():
    record = blank(is_about_single_company=True, company_name="Acme",
        evidence_quote_company="Acme develops packaging.",
        founders=[{"name":"Jane Smith", "evidence_quote":"Jane Smith founded Beta yesterday."}])
    assert ground(record, "Acme develops packaging. Jane Smith founded Beta yesterday.").extraction.founders == []


def test_pre_seed_passage_does_not_support_seed_stage():
    text = "Acme raised a pre-seed round yesterday."
    record = blank(is_about_single_company=True, company_name="Acme", stage="seed",
        evidence_quote_company=text, evidence_quote_stage=text)
    assert ground(record, text).extraction.stage is None
