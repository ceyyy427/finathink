"""Pinned research background items and their original bibliography for offline replay."""

from .contracts import stable_digest
from .portable_inputs import PortableUnavailable


def knowledge_items(discovery, candidate_refs=()):
    rows = list(candidate_refs)
    if discovery:
        rows.extend(discovery["blueprint"]["knowledge_refs"])
        rows.extend(row for role in discovery["roles"] for row in role["knowledge"]["items"])
    items = {}
    for row in rows:
        identity, digest = row["item_id"], row["digest"]
        if identity in items and items[identity] != digest:
            raise ValueError("知识证据指纹冲突")
        items[identity] = digest
    return [{"item_id": key, "digest": value} for key, value in sorted(items.items())]


def knowledge_refs(items):
    if not items:
        return []
    from .asset_catalog import ResearchAssetCatalog
    from .discovery_planner import open_knowledge

    refs = []
    with open_knowledge(ResearchAssetCatalog.packaged()) as index:
        for row in items:
            try:
                item = index.resolve(row["item_id"])
            except KeyError as exc:
                raise PortableUnavailable("DISCOVERY_KNOWLEDGE_VERSION_UNAVAILABLE") from exc
            if stable_digest(item) != row["digest"]:
                raise PortableUnavailable("DISCOVERY_KNOWLEDGE_VERSION_UNAVAILABLE")
            refs.extend(item["source_refs"])
    return refs
