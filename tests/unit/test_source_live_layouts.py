from radar.sources import entrepreneur_first, startups_magazine, uktn


def test_ef_real_metadata_excludes_old_funded_portfolio_and_person_links():
    page='''<div class="tile--company"><div class="tile__link" data-companyslug="oldco"></div><h4>OldCo</h4><div class="locationtag">London</div><a href="https://linkedin.com/in/person">Founder</a><div class="meta__row"><div class="meta__row__name">Founded</div><div class="meta__row__name">2014</div></div><div class="meta__row"><div class="meta__row__name">Funded by</div><div class="meta__row__name">Example Ventures</div></div></div>'''
    item=entrepreneur_first.ADAPTER.parse(page)[0]
    assert item.structured['founded_year']==2014
    assert entrepreneur_first.ADAPTER._wanted(item,2023)  # Old investment evidence still reaches denylist.
    assert item.structured['company_website'] is None
    assert item.kind_hint=='vc_portfolio_listing'
    assert 'linkedin' not in item.source_url
    assert item.structured.get('stage') is None


def test_ef_no_inferred_preseed_or_social_identity_for_undated_card():
    page='''<div class="tile--company" data-companyslug="newco"><h4>NewCo</h4><div class="locationtag">London</div><a href="https://linkedin.com/in/person">Founder</a></div>'''
    item=entrepreneur_first.ADAPTER.parse(page)[0]
    assert item.structured.get('stage') is None
    assert item.structured['company_website'] is None
    assert item.source_url.startswith(entrepreneur_first.PORTFOLIO)


def test_startups_public_article_layout_preserves_date_and_requires_body_fetch():
    page='''<div class="post"><div class="post-title"><h5><a href="https://startupsmagazine.co.uk/acme-raises">Acme raises £2m</a></h5></div><div class="post-excerpt">UK startup building cyber security tools.</div><li class="post-date">September 25, 2026</li></div>'''
    item=startups_magazine.ADAPTER.parse(page)[0]
    assert item.source_url.endswith('/acme-raises')
    assert str(item.published_at)=='2026-09-25'
    assert not item.structured['full_text_in_feed']
    assert item.kind_hint=='funding_round'


def test_uktn_public_article_layout_slug_date_and_no_query():
    page='''<article><h3><a href="https://www.uktech.news/fintech/acme-raises-20260929">Acme raises £2m</a></h3></article>'''
    item=uktn.ADAPTER.parse(page)[0]
    assert str(item.published_at)=='2026-09-29'
    assert item.structured['needs_article_fetch']
    assert '?' not in uktn.INDEX


def test_converge_public_feed_does_not_claim_full_article():
    from radar.sources import converge
    item=converge.ADAPTER.parse('''<rss><channel><item><title>Acme wins Converge challenge prize</title><link>https://www.convergechallenge.com/updates/acme/</link><guid>acme</guid><pubDate>Tue, 29 Sep 2026 09:00:00 GMT</pubDate><description>A new university venture.</description></item></channel></rss>''')[0]
    assert item.kind_hint=='accelerator_cohort'
    assert item.structured['full_text_in_feed'] is False


def test_bgv_active_portfolio_is_investment_evidence_not_discovery():
    from radar.sources import bethnal_green
    item=bethnal_green.ADAPTER.parse('''<div class="grid_item"><h4>Acme</h4><a href="https://acme.example">Site</a><p>Software for businesses.</p><span class="theme-tag">Healthy Lives</span></div>''')[0]
    assert item.kind_hint=='vc_portfolio_listing'
    assert item.structured['on_vc_portfolio']
    assert not item.structured['exited']
    assert item.structured.get('stage') is None


def test_public_news_fingerprint_ignores_post_ids_and_topic_classes():
    first='''<div class="post post-123 category-fintech"><div class="post-title"><h5><a href="/acme">Acme raises</a></h5></div></div>'''
    second='''<div class="post post-456 category-climate"><div class="post-title"><h5><a href="/beta">Beta raises</a></h5></div></div>'''
    startups_magazine.ADAPTER.parse(first)
    original=startups_magazine.ADAPTER.last_fingerprint
    startups_magazine.ADAPTER.parse(second)
    assert startups_magazine.ADAPTER.last_fingerprint==original
