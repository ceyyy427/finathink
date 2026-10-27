"""Discoverable methods are references, not fabricated runtime capabilities."""

import json
import re
from dataclasses import asdict

import pytest

from finahinking.research.asset_catalog import ResearchAssetCatalog

EXPECTED = {
    "factor-mining-workflow", "alpha-gpt-factor-mining", "alpha-gpt-researcher",
    "backtest-acceptance", "niu-san-distillation", "niu-san-linyuan",
    "niu-san-chenxiaoqun", "niu-san-zhangmengzhu", "niu-san-zhaolaoge",
    "niu-san-chaoguyangjia", "niu-san-fengliu", "github-maintainer",
}


def test_twelve_method_identities_are_unique_and_resolve_actual_resources():
    catalog = ResearchAssetCatalog.packaged()
    assert {skill.skill_id for skill in catalog.skills()} == EXPECTED
    assert len(catalog.skills()) == 12
    for skill in catalog.skills():
        assert skill.version == "1.0.0"
        assert skill.tags and skill.required_inputs and skill.output_types
        refs = {ref.resource_id: ref for ref in catalog.resources_for(skill.skill_id)}
        for resource_id in skill.required_resources:
            assert refs[resource_id].availability == "PACKAGED"
            assert catalog.read_text(resource_id)
        body = catalog.read_text(skill.main_resource)
        assert re.search(rf"^name: {re.escape(skill.skill_id)}$", body, re.MULTILINE)
        assert re.search(r"^description: Use when ", body, re.MULTILINE)
        assert not re.search(r"[A-Z]:\\|Scripts\\python|^## .*自我介绍", body, re.MULTILINE)
        assert refs[skill.main_resource].derivation == "FINATHINK_ADAPTATION"
        assert refs[skill.main_resource].source_sha256


def test_methods_share_one_policy_without_execution_or_permission_flags():
    catalog = ResearchAssetCatalog.packaged()
    assert catalog.skills(), "adapted methods are missing"
    assert {skill.policy_ref for skill in catalog.skills()} == {"factor-validation-v1"}
    policy = json.loads(catalog.read_text("factor-validation-v1"))
    assert policy["schema_version"] == "factor-validation.v1"
    assert len({stage["stage_id"] for stage in policy["stages"]}) == 9
    assert policy["tool_grants"] == []
    assert policy["automatic_admission"] is False
    assert policy["runtime_execution_status"] == "NOT_RUN"
    assert policy["deduplication"]["correlation_measure"] == "max_absolute_spearman"
    for skill in catalog.skills():
        record = asdict(skill)
        assert not ({"tool_names", "permissions", "computed", "validated", "profit"} & record.keys())
        assert "no-tool-grant" in skill.boundaries


@pytest.mark.parametrize("skill_id", sorted(name for name in EXPECTED if name.startswith("niu-san-")))
def test_behaviour_methods_require_provenance_and_return_hypotheses(skill_id):
    catalog = ResearchAssetCatalog.packaged()
    skill = catalog.get_skill(skill_id)
    assert {"public_evidence_refs", "as_of", "identity_confidence"} <= set(skill.required_inputs)
    assert skill.output_types == ("BehaviorEvidence", "FactorHypothesis")
    assert {"public-evidence-only", "not-an-endorsement"} <= set(skill.boundaries)
    assert any(ref.availability in {"UPSTREAM_ONLY", "MISSING"}
               for ref in catalog.resources_for(skill_id))


def test_missing_original_handbook_remains_a_dependency_not_a_fake_file():
    catalog = ResearchAssetCatalog.packaged()
    skill = catalog.get_skill("factor-mining-workflow")
    refs = catalog.resources_for(skill.skill_id)
    gap = next(ref for ref in refs if ref.resource_id == "upstream-factor-handbook")
    assert gap.availability == "MISSING" and gap.path is None and gap.sha256 is None
