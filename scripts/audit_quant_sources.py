"""Explicit offline source audit; never downloads or executes upstream projects."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from finahinking.research.source_intake import SourceSnapshot, audit_source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-root", type=Path, required=True)
    parser.add_argument("--lock", type=Path, default=(
        ROOT / "docs/research/2026-10-31-open-source-quant-lock.json"))
    parser.add_argument("--module-map", type=Path, default=(
        ROOT / "docs/research/quant-source-module-map.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    lock = json.loads(args.lock.read_text(encoding="utf-8"))
    mapping = json.loads(args.module_map.read_text(encoding="utf-8"))
    modules = {s["source_id"]: s for s in mapping["sources"]}
    if len(modules) != len(mapping["sources"]):
        raise ValueError("duplicate source in module map")
    results = []
    for record in lock["sources"]:
        module = modules[record["source_id"]]
        if module["commit"] != record["commit"] or module["repository"] != record["repository"]:
            raise ValueError("source map identity mismatch")
        snapshot = SourceSnapshot.from_record(record, module_map=module["modules"])
        results.append(audit_source(args.reference_root / snapshot.source_id, snapshot))
    output = {"schema_version": "quant-source-audits.v1", "capture_date": lock["capture_date"],
              "sources": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({"sources": len(results), "verified": sum(
        s["status"] == "VERIFIED" for s in results), "artifacts": sum(
        len(s["artifacts"]) for s in results), "errors": [
            {"source_id": s["source_id"], **e} for s in results for e in s["errors"]]},
        ensure_ascii=False))
    return int(any(s["status"] != "VERIFIED" for s in results))


if __name__ == "__main__":
    raise SystemExit(main())
