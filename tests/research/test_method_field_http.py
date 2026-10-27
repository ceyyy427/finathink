"""The real HTTP framing limit agrees with the attachment contract."""

import http.client
import json
from urllib.parse import urlsplit

import pandas as pd
import pytest

from finahinking.data.research_fields import MAX_FIELD_BYTES, ResearchFieldAttachment
from finahinking.p8_2.data_sources import FixtureMarketDataSource
from tests.p7_5.test_phase4_browser import http_app

__all__ = ["http_app"]


def test_valid_attachment_larger_than_legacy_http_limit_is_accepted(http_app):
    app, base = http_app
    service = app.research_service
    reference = service.snapshots.save(FixtureMarketDataSource(points=20).snapshot("AAA"))
    job = service.submit({"idempotency_key": "large-http-fields", "snapshot_ref": reference,
        "instrument": "AAA", "as_of": "2026-10-01", "question": "检查明确字段的可用性", "model_config_ref": "offline"})
    times = pd.date_range("2000-01-01", periods=8400, tz="UTC")
    attachment = ResearchFieldAttachment(reference, "CN", {"turnover_rate": "ratio_free_float"},
        [{"instrument": "AAA", "timestamp": stamp.isoformat(), "available_at": stamp.isoformat(), "turnover_rate": .02} for stamp in times])
    payload = json.dumps({"attachment": attachment.to_dict(), "_csrf": app.csrf_token}).encode()
    assert 1_000_000 < len(payload) < MAX_FIELD_BYTES
    address = urlsplit(base)
    connection = http.client.HTTPConnection(address.hostname, address.port, timeout=15)
    try:
        connection.putrequest("POST", f'/api/research/jobs/{job["job_id"]}/research-fields')
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(len(payload)))
        connection.endheaders()
        try:
            connection.send(payload)
        except BrokenPipeError:
            pass  # A length rejection can close while the client is sending.
        response = connection.getresponse()
        assert response.status == 201
        assert json.loads(response.read())["attachment_ref"] == attachment.fingerprint
    finally:
        connection.close()


@pytest.mark.parametrize("path,length", [("/api/research/jobs/job-example/research-fields", MAX_FIELD_BYTES + 65537),
                                          ("/api/research/jobs", 1_000_001)])
def test_attachment_and_other_endpoints_keep_bounded_length_before_reading(http_app, path, length):
    _, base = http_app
    address = urlsplit(base)
    connection = http.client.HTTPConnection(address.hostname, address.port, timeout=5)
    try:
        connection.putrequest("POST", path)
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(length))
        connection.endheaders()
        assert connection.getresponse().status == 413
    finally:
        connection.close()
