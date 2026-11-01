import pytest

from tests.research.test_panel_operators import evaluate


@pytest.mark.parametrize('expression', [
    'rank(close)', 'ts_mean(close,253,1)', 'ts_mean(close,3,4)',
    '__import__(close)', 'close + volume', 'close.__class__', 'ts_lag(close,-1)',
    'ts_mean(close,3)', 'cs_rank(close,0)', 'cs_winsorize(close,.8,.2,3)',
    'ts_mean(close,3,3,3)', 'ts_lag(close,1.5)', 'ts_mean(close,True,1)',
    'ts_mean(' * 15 + 'close' + ',2,2)' * 15,
])
def test_operator_cost_limits_and_unsupported_syntax(expression):
    with pytest.raises(ValueError):
        evaluate(expression)


def test_legacy_fingerprint_and_output_unchanged():
    from finahinking.factors.dsl import evaluate_expression, parse_factor_expression
    from tests.research.test_factor_dsl import _frame
    assert parse_factor_expression('rank(rolling_mean(return(close,1),3))', ('close',)).fingerprint == '36b1bf92b33631c69b87e9539e9405972d2fbf4167d15de4b8740c30152b61c6'
    assert evaluate_expression('rolling_mean(close,3)', _frame()).iloc[-1] == pytest.approx(317/3)


def test_train_fit_boundaries_and_degenerate_neutralization():
    from finahinking.factors import dsl
    from tests.data.test_research_panel import fixture
    assert hasattr(dsl, 'evaluate_panel_expression'), 'Phase23 panel DSL is missing'
    from finahinking.factors.panel_operators import (
        fit_panel_standardizer,
        transform_panel_standardizer,
    )
    snap, rules, contract, build = fixture()
    panel = build(snap, [rules], contract)
    fitted = fit_panel_standardizer(panel, 'close', fit_start='2026-01-02T16:00Z', fit_end='2026-01-03T16:00Z')
    # Training values 1,10,4,3,20,4: mean=7, population variance=124/3.
    assert fitted['mean'] == 7
    assert fitted['std'] == pytest.approx((124/3) ** .5)
    values = transform_panel_standardizer(panel, fitted)
    assert values.iloc[:6].isna().all()  # fit cannot rewrite training history
    assert values.iloc[6] == pytest.approx((2-7)/(124/3)**.5)


def test_training_artifact_rejects_changes_but_accepts_only_future_changes():
    from finahinking.data.research_panel import ResearchPanel
    from finahinking.factors.panel_operators import (
        fit_panel_standardizer,
        transform_panel_standardizer,
    )
    from tests.data.test_research_panel import fixture
    snap, rules, contract, build = fixture()
    panel = build(snap, [rules], contract)
    fitted = fit_panel_standardizer(panel, 'close', fit_start=contract.observed_at[0], fit_end=contract.observed_at[1])
    frame = panel.frame; frame.iloc[-1, frame.columns.get_loc('close')] = 999
    future = ResearchPanel(frame, panel.audit, contract, panel.fingerprint)
    assert transform_panel_standardizer(future, fitted).iloc[-1] > 100
    frame.iloc[0, frame.columns.get_loc('close')] = 999
    with pytest.raises(ValueError, match='训练数据'):
        transform_panel_standardizer(ResearchPanel(frame, panel.audit, contract, panel.fingerprint), fitted)
    with pytest.raises(ValueError, match='参数'):
        transform_panel_standardizer(panel, {**fitted, 'mean': 99})
    with pytest.raises(ValueError, match='边界'):
        fit_panel_standardizer(panel, 'close', fit_start=contract.observed_at[0], fit_end='2026-01-06T16:00Z')
    constant = fit_panel_standardizer(panel, 'volume', fit_start=contract.observed_at[0], fit_end=contract.observed_at[1])
    assert constant['status'] == 'NEEDS_DATA'
    assert transform_panel_standardizer(panel, constant).isna().all()


def test_cost_limit_is_total_work_not_only_individual_window():
    from finahinking.factors.panel_operators import parse_panel_expression
    with pytest.raises(ValueError, match='预算'):
        parse_panel_expression('ts_mean(ts_mean(ts_mean(close,252,252),252,252),252,252)', ['close'], row_count=10000)
