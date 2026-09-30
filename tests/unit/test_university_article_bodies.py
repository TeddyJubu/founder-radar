import pytest
from pathlib import Path

from radar.sources._article import article_links, article_text


@pytest.mark.parametrize('key,fixture,link_count', [
    ('ucl_ventures', 'ucl_article_captured_structure.html', 2),
    # Six body anchors are retained; their fictional labels/URLs are unique.
    ('sheffield', 'sheffield_article_captured_structure.html', 6),
    ('edinburgh_innovations', 'edinburgh_article_captured_structure.html', 3),
])
def test_captured_official_article_structure(key, fixture, link_count):
    payload = (Path(__file__).parents[1] / 'fixtures' / 'sources' / fixture).read_text()
    body = article_text(payload, key)
    assert 'ARTICLE_BODY_EVIDENCE' in body
    assert 'OUTSIDE_ARTICLE_DECOY' not in body
    links = article_links(payload, key, 'https://example.test/news')
    assert len(links) == link_count
    assert all('ARTICLE_BODY_EVIDENCE' in link['label'] for link in links)
    assert all(link['url'].startswith('https://example.test/fixture-company-') for link in links)


@pytest.mark.parametrize('key,body_class', [
    ('ucl_ventures', 'basic-content__column'),
    ('sheffield', 'block-field-blocknodenews-articlebody'),
])
def test_article_body_excludes_navigation_and_sidebar_company_links(key, body_class):
    article = 'Example Instruments develops a new sensing system for factories. ' * 5
    body = f'<div class="{body_class}"><p>{article}</p><a href="https://example.org/company">Example Instruments</a></div>'
    if key == 'ucl_ventures':
        body = f'<div class="sidebar-content-page__left-content">{body}</div>'
    payload = f'<nav>{"Navigation " * 40}<a href="https://wrong.test">Wrong Company</a></nav><main>{body}<aside>{"Related news " * 40}<a href="https://wrong.test/related">Other Company</a></aside></main>'
    text = article_text(payload, key)
    assert article.strip() in text
    assert 'Navigation' not in text and 'Related news' not in text
    assert article_links(payload, key, 'https://source.test/news') == [
        {'label': 'Example Instruments', 'url': 'https://example.org/company'}
    ]


@pytest.mark.parametrize('key', ['ucl_ventures', 'sheffield', 'edinburgh_innovations'])
def test_unrecognised_layout_with_long_page_text_stays_withheld(key):
    payload = '<main><div class="unreviewed">' + ('Example Company news. ' * 30) + '</div></main>'
    with pytest.raises(ValueError, match='no substantive article'):
        article_text(payload, key)
    assert article_links(payload, key, 'https://source.test/news') == []


@pytest.mark.parametrize('key', ['ucl_ventures', 'sheffield', 'edinburgh_innovations'])
def test_body_selector_is_scoped_to_main_content(key):
    payload = '<footer><div class="sidebar-content-page__left-content"><div class="basic-content__column block-field-blocknodenews-articlebody">' + ('Footer Company ' * 30) + '</div></div></footer>'
    with pytest.raises(ValueError, match='no substantive article'):
        article_text(payload, key)
