"""Read original bookings from a frozen window and invalidate approvals on a changed cutoff."""
import pytest

from finahinking.research.strategy_learning import StrategyLearningService
from tests.p7_5.test_phase4_browser import browser, http_app, observed_page
from tests.research.test_strategy_worker import completed

__all__ = ["browser", "http_app", "observed_page"]


@pytest.mark.parametrize("http_app", [False], indirect=True)
def test_receipt_draws_verified_fills_and_a_changed_cutoff_discards_late_actions(http_app, observed_page):
    from playwright.sync_api import expect

    app, base = http_app
    page, _, diagnostics = observed_page
    service = app.research_service
    sample = service.create_strategy_sample()
    inputs = {key: sample[key] for key in ("contract", "attachment_refs")}
    creates = service.run_strategy_template({**inputs, "template_id": "equal-weight", "idempotency_key": "receipt-inspect"})
    job = completed(service, creates["job_id"])
    learning = StrategyLearningService(service)
    receipt = learning.settlements.observe(job["run_id"], job["as_of"])
    audit = learning.audit(receipt["observation_ref"])
    proposal = learning.propose(audit["audit_id"])
    page.add_init_script("""(() => {
      const original = window.fetch;
      window.fetch = async function (url, options) {
        const response = await original.call(window, url, options);
        if (window.delayAudit && String(url).endsWith('/learning/strategy/audits') && options?.method === 'POST') {
          document.documentElement.dataset.auditHeld = 'true';
          await new Promise(resolve => { window.releaseAudit = resolve; });
        }
        return response;
      };
    })()""")
    page.set_viewport_size({"width": 375, "height": 812})
    section = page.locator('[data-strategy-learning]')
    page.goto(base + '/research/learning?observation_ref=' + receipt['observation_ref'] + '#strategy-learning')
    expect(section.locator('[data-sl-decision]')).to_be_visible()
    expect(page.locator('[data-learning-workspace] [data-strategy-learning]')).to_have_count(0)
    expect(page.locator('[data-learning-workspace] [name="rationale"]')).to_have_count(1)
    expect(section.locator('[data-sl-receipt-binding]')).to_contain_text(receipt["run_id"])
    expect(section.locator('[data-sl-receipt-binding]')).to_contain_text(receipt["cutoff_at"])
    expect(section.locator('[data-sl-raw-receipt]')).to_have_attribute('href', '/api/research/learning/strategy/observations/' + receipt['observation_ref'])
    window = receipt["samples"][0]
    detail = section.locator('[data-sl-window-detail][data-start-at="' + window["start_at"] + '"]')
    summary = detail.locator('summary')
    summary.focus()
    summary.press('Enter')
    expect(detail.locator('[data-sl-sample-ref]')).to_contain_text(window["sample_ref"])
    order = window["fill_order_ids"][0]
    fill = next(f for f in job["strategy_study"]["result"]["ledger"]["fills"] if f["order_id"] == order)
    settled = next(f for f in window["settlements"] if f["order_id"] == order)
    fill_detail = detail.locator(f'[data-sl-fill="{order}"]')
    for value in (fill["instrument"], fill["decision_at"], fill["timestamp"], settled["settled_at"]):
        expect(fill_detail).to_contain_text(value)
    for field, key in (("成交量", "quantity"), ("成交价", "price"), ("手续费", "fees"), ("滑点成本", "slippage")):
        term = fill_detail.get_by_text(field, exact=True)
        actual = term.locator("xpath=following-sibling::dd[1]").inner_text()
        assert float(actual) == pytest.approx(fill[key])
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    for dropped in receipt["excluded_windows"]:
        excluded = section.locator('[data-sl-window-detail][data-start-at="' + dropped["start_at"] + '"]')
        excluded.locator('summary').click()
        reasons = excluded.locator('[data-sl-exclusion]').all_text_contents()
        assert all(any(code in reason for reason in reasons) for code in dropped["reasons"])
    page.evaluate("window.delayAudit = true")
    section.locator('[data-sl-audit]').click()
    expect(page.locator("html")).to_have_attribute("data-audit-held", "true")
    cutoff = section.locator('[data-sl-observe] [name=as_of]')
    cutoff.fill('2000-01-01')
    expect(section.locator('[data-sl-proposal]')).to_be_hidden()
    expect(section.get_by_role('button', name='批准纸面政策', include_hidden=True)).to_be_disabled()
    page.evaluate("window.releaseAudit()")
    page.wait_for_load_state('networkidle')
    for selector in ('[data-sl-receipt]', '[data-sl-audit-detail]', '[data-sl-proposal]', '[data-sl-comparison]'):
        expect(section.locator(selector)).to_be_hidden()
    expect(cutoff).to_have_value("2000-01-01")
    expect(section.locator('[data-sl-history]')).to_have_value('')
    section.locator('[data-sl-history]').select_option(receipt["observation_ref"])
    expect(section.locator('[data-sl-decision]')).to_be_visible()
    section.locator('[data-sl-observe] select').select_option('')
    expect(section.locator('[data-sl-proposal]')).to_be_hidden()
    section.locator('[data-sl-history]').select_option(receipt["observation_ref"])
    expect(section.locator('[data-sl-observe] select')).to_have_value(job["run_id"])
    expect(section.locator('[data-sl-audit]')).to_be_enabled()
    assert learning.proposal(proposal["proposal_id"])["status"] == "PENDING_REVIEW"
    assert not diagnostics["errors"]
