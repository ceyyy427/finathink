from dataclasses import replace

from finahinking.p8_2.data_sources import FixtureMarketDataSource
from finahinking.research.contracts import stable_digest
from finahinking.research.factor_workbench import compute_study, prepare_study


def test_frozen_test_comparability_tracks_actual_usable_dates_not_nominal_holdout():
    original = FixtureMarketDataSource(points=240).snapshot("AAA")
    snapshot = replace(original, observations=tuple(replace(row, volume=0) if index in {199, 211} else row for index, row in enumerate(original.observations)))
    spec = prepare_study({"instrument": "AAA", "as_of": "2026-10-01", "question": "Check actual held-out volume samples", "recipe_id": "volume_momentum", "windows": [3, 5], "n_splits": 2}, snapshot, policy_fingerprint="a" * 64)
    study = {"study_id": "sample-study", "spec": spec, "spec_ref": stable_digest(spec)}
    validation = compute_study(snapshot, study, phase="validation")
    results = []
    for record in validation["experiments"]:
        selected = dict(study, candidate_id=record["experiment_id"], candidate_fingerprint=record["digest"])
        results.append(compute_study(snapshot, selected, phase="test", validation=validation))
    assert results[0]["test_summary"]["metrics"]["sample_size"] == results[1]["test_summary"]["metrics"]["sample_size"]
    assert results[0]["test_summary"]["conditions"]["scoring_index"] != results[1]["test_summary"]["conditions"]["scoring_index"]
