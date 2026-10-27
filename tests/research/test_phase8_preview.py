import pytest

from finahinking.research.learning_manager import LearningManager
from tests.research.test_learning_application import settlement


def test_draft_preview_can_be_inspected_before_review_but_does_not_admit_or_apply():
    manager = LearningManager()
    proposal = manager.propose_update(settlement())
    baseline = {"version": 0, "factor_weights": {"value": 1}, "risk_rules": {"max_drawdown": 0.25}}
    preview = manager.preview_proposal(proposal, baseline)
    assert preview.next_version == 1
    assert preview.factor_weights["value"] == 0.7
    assert preview.risk_rules["max_drawdown"] == 0.18
    assert manager.admitted_updates == ()
    assert baseline["risk_rules"]["max_drawdown"] == 0.25
    with pytest.raises(ValueError, match="approved"):
        manager.apply(proposal, baseline)
