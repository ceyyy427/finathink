"""Deterministic reference routing; never a tool or data-access grant."""

from dataclasses import asdict

from .asset_catalog import ResearchAssetCatalog
from .asset_contracts import MethodRouteRequest, MethodSelection
from .contracts import stable_digest

_PURPOSES = {
    "factor-mining": ("factor-mining-workflow", "alpha-gpt-factor-mining"),
    "backtest": ("backtest-acceptance",),
    "research": ("alpha-gpt-researcher",),
    "public-behavior": ("niu-san-distillation",),
    "maintenance": ("github-maintainer",),
}
_ALIASES = {
    "factor_mining": "factor-mining", "挖因子": "factor-mining",
    "因子挖掘": "factor-mining", "formula-generation": "factor-mining",
    "validation": "backtest", "回测": "backtest", "验收": "backtest",
    "研究": "research", "公开行为": "public-behavior",
    "public_behavior": "public-behavior", "维护": "maintenance",
}
_PROFILES = frozenset({"niu-san-linyuan", "niu-san-chenxiaoqun", "niu-san-zhangmengzhu",
                       "niu-san-zhaolaoge", "niu-san-chaoguyangjia", "niu-san-fengliu"})
_FAMILY_FIELDS = {"momentum": ("close",), "mean_reversion": ("close",),
                  "volatility": ("close",), "turnover": ("turnover_rate",)}


def select_methods(request: MethodRouteRequest, catalog: ResearchAssetCatalog) -> MethodSelection:
    if not isinstance(request, MethodRouteRequest):
        raise TypeError("request must be a MethodRouteRequest")
    reasons = {}
    unsupported = []
    missing = []
    for raw in request.purposes:
        purpose = _ALIASES.get(raw, raw)
        if purpose not in _PURPOSES:
            unsupported.append(f"purpose:{raw}")
            continue
        for skill_id in _PURPOSES[purpose]:
            reasons.setdefault(skill_id, set()).add(f"目的标签：{purpose}")
    for profile in request.public_profiles:
        if profile not in _PROFILES:
            unsupported.append(f"profile:{profile}")
            continue
        reasons.setdefault("niu-san-distillation", set()).add("显式请求公开行为视角")
        reasons.setdefault(profile, set()).add(f"仅加载指定视角：{profile}")
    for family in request.factor_families:
        for field in _FAMILY_FIELDS.get(family, ()):
            if field not in request.available_fields:
                missing.append(f"field:{field}")
    indexed = {skill.skill_id for skill in catalog.skills()}
    absent = set(reasons) - indexed
    missing.extend(f"method:{skill_id}" for skill_id in sorted(absent))
    selected = tuple(sorted(set(reasons) & indexed))
    required = {}
    optional = {}
    for skill_id in selected:
        descriptor = catalog.get_skill(skill_id)
        mandatory = {descriptor.main_resource, descriptor.policy_ref, *descriptor.required_resources}
        for ref in catalog.resources_for(skill_id):
            (required if ref.resource_id in mandatory else optional)[ref.resource_id] = ref
    # A shared resource is loaded once and is mandatory if any method needs it.
    for resource_id in required:
        optional.pop(resource_id, None)
    status = "READY"
    if unsupported or not selected:
        status = "NEEDS_CLARIFICATION"
    elif absent:
        status = "BLOCKED_RESOURCE"
    return MethodSelection(
        request_digest=stable_digest({"schema_version": "method-route.v1", **asdict(request)}),
        status=status, skill_ids=selected,
        reasons=tuple((skill_id, "；".join(sorted(reasons[skill_id]))) for skill_id in selected),
        required_resources=tuple(required[key] for key in sorted(required)),
        optional_resources=tuple(optional[key] for key in sorted(optional)),
        missing_inputs=tuple(sorted(set(missing))), unsupported=tuple(sorted(set(unsupported))),
    )
