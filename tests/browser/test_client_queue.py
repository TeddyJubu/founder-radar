"""Refresh the real Today UI without disturbing an active review."""
import pytest
from tests.browser.conftest import tid

pytestmark = pytest.mark.browser


def test_refresh_updates_backlog_without_moving_current_card(page, server):
    page.goto(server + '/')
    page.wait_for_selector(tid('card'))
    before = page.locator(tid('card')).get_attribute('data-company-id')
    page.evaluate("""async () => {
      const payload = await (await fetch('/api/today')).json();
      payload.totals.remaining = 71;
      payload.totals.ready_to_review = 71;
      payload.totals.awaiting_final_check = 23;
      payload.totals.remaining_total = 94;
      // Also change the incoming first card so this tests queue preservation.
      payload.companies.reverse();
      window.fetch = async () => ({json: async () => payload});
      await refresh();
    }""")
    assert page.locator(tid('card')).get_attribute('data-company-id') == before
    assert page.locator('#progress').inner_text() == '71 ready to review · 23 awaiting final check'


def test_refresh_totals_does_not_double_subtract_saved_decision(page, server):
    page.goto(server + '/')
    page.wait_for_selector(tid('card'))
    page.evaluate("""async () => {
      sessionReviewed.add('previously-saved-company');
      const payload = await (await fetch('/api/today')).json();
      payload.totals.remaining = 9;
      payload.totals.awaiting_final_check = 2;
      window.fetch = async () => ({json: async () => payload});
      await refresh();
      sessionReviewed.add('next-decision');
      renderProgress();
    }""")
    assert page.locator('#progress').inner_text() == '8 ready to review · 2 awaiting final check'


def test_innovate_citation_is_labelled_as_project_data(page, server):
    page.goto(server + '/')
    page.wait_for_selector(tid('card'))
    labels = page.evaluate("""() => articleLinks({
      source_key: 'innovate_uk',
      source_url: 'https://www.gov.uk/government/publications/innovate-uk-funded-projects#project=REF123&participant=Example',
      source_external_id: 'wrong-fallback',
      sources: [{source_key:'innovate_uk',source_url:'https://www.gov.uk/download.xlsx',external_id:'REF456'}]
    }).map(x => x.label)""")
    assert labels == ['Found at Innovate UK funded-projects data · project REF123', 'Innovate UK funded-projects data · project REF456']


def test_refresh_removes_card_that_is_no_longer_approved(page, server):
    page.goto(server + '/')
    page.wait_for_selector(tid('card'))
    before = page.locator(tid('card')).get_attribute('data-company-id')
    page.evaluate("""async () => {
      const payload = await (await fetch('/api/today')).json();
      const withdrawn = data.companies[i].company_id;
      payload.ready_company_ids = payload.ready_company_ids.filter(id => id !== withdrawn);
      payload.companies = payload.companies.filter(c => c.company_id !== withdrawn);
      window.fetch = async () => ({json: async () => payload});
      await refresh();
    }""")
    assert page.locator(tid('card')).get_attribute('data-company-id') != before
