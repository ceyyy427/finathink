from finahinking.research.atlas_graphics import observation_labels
from finahinking.research.atlas_workspace import _metrics, normalized_marks


def test_verified_equity_and_calendar_need_independent_opening_marks():
    marks = [{"timestamp": "2025-01-01", "equity": 200}, {"timestamp": "2025-01-02", "equity": 180}]
    bench = [{"timestamp": "2025-01-01", "equity": 50}, {"timestamp": "2025-01-02", "equity": 55}]
    result = {"ledger": {"marks": marks}, "benchmark": {"status": "COMPLETE", "marks": bench}}
    series = normalized_marks(result)
    assert series == [("2025-01-01", 1., 1.), ("2025-01-02", .9, 1.1)]
    assert _metrics(series) == [("累计收益", "-10.00%"), ("最大回撤", "-10.00%"), ("相对基准收益", "-20.00 个百分点")]
    bench[0]["timestamp"] = "2024-12-31"
    assert [row[2] for row in normalized_marks(result)] == [None, None]
    assert _metrics(normalized_marks(result))[-1][1] == "—"
    marks[1]["equity"] = float("nan")
    assert not normalized_marks(result)
    assert not normalized_marks({"ledger": None, "benchmark": None})


def test_two_marks_in_one_session_have_distinct_keyboard_and_table_labels():
    same_day = [("2025-05-02T09:30:00+00:00", 1., 1.), ("2025-05-02T15:30:00+00:00", .9, 1.)]
    assert observation_labels(same_day) == ["2025-05-02 09:30:00", "2025-05-02 15:30:00"]
    assert observation_labels([same_day[0], ("2025-05-03T09:30:00+00:00", 1., 1.)]) == ["2025-05-02", "2025-05-03"]
