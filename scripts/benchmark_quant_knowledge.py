"""Fixed synthetic catalogue workload for the same search used by the workbench."""
import argparse
import json
import math
import platform
import statistics
import time

from finahinking.research.knowledge_workspace_page import WorkspaceCatalog


def _milliseconds(start):
    return (time.perf_counter() - start) * 1000


def benchmark():
    objects = [
        {"object_id": f"factor:benchmark-{i:05}", "dedup": f"formula-{i:05}", "kind": "factor",
         "title": f"分组因子 主题{i % 83:02} 编号{i:05}", "summary": "中文量价与风险研究条目",
         "aliases": ["波动", f"factor {i:05}"]}
        for i in range(10_000)
    ]
    queries = [f"主题{(i * 331) % 83:02} 编号{i * 331:05}" for i in range(30)]
    start = time.perf_counter()
    index = WorkspaceCatalog(objects)
    build = _milliseconds(start)
    measurements = []
    for i, query in enumerate(queries):
        start = time.perf_counter()
        result = index.search(query, "factor")
        measurements.append(_milliseconds(start))
        assert result["total"] == 1 and result["items"][0]["object_id"] == f"factor:benchmark-{i * 331:05}"
    warm = []
    for query in queries:
        start = time.perf_counter()
        index.search(query, "factor")
        warm.append(_milliseconds(start))
    return {"schema_version": "quant-catalog-benchmark.v1", "objects": 10_000, "queries": len(queries),
            "environment": {"machine": platform.machine(), "os": platform.system(), "python": platform.python_version()},
            "cold_index_ms": round(build, 3), "cold_first_query_ms": round(measurements[0], 3),
            "warm_p50_ms": round(statistics.median(warm), 3),
            "warm_p95_ms": round(sorted(warm)[math.ceil(.95 * len(warm)) - 1], 3)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-p95-ms", type=float, default=500)
    limit = parser.parse_args().max_p95_ms
    result = benchmark()
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["warm_p95_ms"] <= limit else 1


if __name__ == "__main__":
    raise SystemExit(main())
