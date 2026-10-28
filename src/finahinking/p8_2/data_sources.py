"""Deterministic market-data providers used by core and offline tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol

from .contracts import DatasetSnapshot, MarketObservation


def snapshot_from_batch(batch, *, as_of=None, adjustment: str = "UNKNOWN", calendar: str = "UNKNOWN", units=None) -> DatasetSnapshot:
    """Capture normalized user prices without inventing missing OHLCV or PIT."""

    from finahinking.data.units import snapshot_units
    from finahinking.data.user_api_contracts import DataBatch, fingerprint_records

    units = snapshot_units(units)

    if not isinstance(batch, DataBatch):
        raise TypeError("batch must be a DataBatch")
    if batch.data_fingerprint != fingerprint_records(batch.records):
        raise ValueError("data batch fingerprint does not match records")
    if not batch.records:
        raise ValueError("snapshot has no price records")
    required = {"instrument", "timestamp", "open", "high", "low", "close", "volume"}
    observations = []
    for index, record in enumerate(batch.records):
        missing = sorted(required - record.keys())
        if missing:
            raise ValueError(f"record {index}: missing required fields: {', '.join(missing)}")
        observations.append(MarketObservation(
            **{key: record[key] for key in required},
            amount=record.get("amount"), available_at=record.get("available_at"),
            industry=record.get("industry"), suspended=record.get("suspended"),
            source=batch.connection_id, provider="user-api",
            retrieved_at=batch.retrieved_at, adapter_version="user-snapshot.v1",
        ))
    observations.sort(key=lambda item: (item.instrument, item.timestamp))
    return DatasetSnapshot(
        dataset_id=f"user-{batch.data_fingerprint[:32]}",
        observations=tuple(observations), mode="CAPTURED", as_of=as_of,
        provenance={
            "connection_id": batch.connection_id, "source": batch.connection_id,
            "provider": "user-api", "source_declaration": batch.source_declaration,
            "data_fingerprint": batch.data_fingerprint,
            "retrieved_at": batch.retrieved_at.isoformat(),
            "quality_issues": list(batch.quality_issues), "pit_status": batch.pit_available,
            "timezone": "UTC", "adjustment": adjustment, "calendar": calendar,
            **({"units": units} if units else {}),
        },
        limitations=("User-declared source and market conventions; paper-only research.",),
    )


class MarketDataSource(Protocol):
    def snapshot(self, instrument: str = "DEMO", *, limit: int | None = None) -> DatasetSnapshot:
        """Return a normalized, fingerprinted snapshot."""


class FixtureMarketDataSource:
    """A tiny deterministic OHLCV source with explicit ``SAMPLE`` mode."""

    def __init__(self, *, source_id: str = "fixture-market", points: int = 12) -> None:
        if not isinstance(points, int) or isinstance(points, bool) or points < 1 or points > 256:
            raise ValueError("fixture points must be between 1 and 256")
        self.source_id = source_id
        self.points = points

    def _records(self, instrument: str) -> tuple[MarketObservation, ...]:
        base = datetime(2026, 1, 2, tzinfo=UTC)
        records: list[MarketObservation] = []
        for index in range(self.points):
            timestamp = base + timedelta(days=index)
            open_price = 100.0 + index * 0.8
            close = open_price + (0.55 if index % 3 else -0.25)
            high = max(open_price, close) + 0.75
            low = min(open_price, close) - 0.65
            records.append(
                MarketObservation(
                    instrument=instrument,
                    timestamp=timestamp,
                    open=open_price,
                    high=high,
                    low=low,
                    close=close,
                    volume=1_000.0 + index * 37.0,
                    amount=(1_000.0 + index * 37.0) * close,
                    source=self.source_id,
                    provider="finathink.fixture",
                    retrieved_at=timestamp + timedelta(minutes=1),
                    available_at=timestamp,
                    adapter_version="p8.2-fixture-1",
                )
            )
        return tuple(records)

    def snapshot(
        self,
        instrument: str = "DEMO",
        *,
        limit: int | None = None,
        symbol: str | None = None,
    ) -> DatasetSnapshot:
        if not isinstance(instrument, str) or not instrument.strip():
            raise ValueError("instrument is required")
        if symbol is not None:
            if not isinstance(symbol, str) or not symbol.strip():
                raise ValueError("symbol is required")
            instrument = symbol
        records = self._records(instrument.strip())
        if limit is not None:
            if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 256:
                raise ValueError("fixture limit is invalid")
            records = records[: min(limit, len(records))]
        as_of = records[-1].available_at
        return DatasetSnapshot(
            dataset_id=f"{self.source_id}-{instrument.strip()}",
            observations=records,
            mode="SAMPLE",
            as_of=as_of,
            provenance={
                "provider": "finathink.fixture",
                "source": self.source_id,
                "retrieved_at": "2026-01-01T00:00:00+00:00",
                "license": "synthetic fixture; not market data",
            },
            limitations=("Synthetic SAMPLE data; it is not evidence of market performance.",),
        )


__all__ = ["FixtureMarketDataSource", "MarketDataSource", "snapshot_from_batch"]
