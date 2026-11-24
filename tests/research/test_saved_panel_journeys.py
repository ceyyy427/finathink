"""Saved panels exercised through HTTP, a controlled source and real workers."""

import json
import math
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

import pandas as pd
import pytest

from finahinking.data.connection_settings import InMemoryDataCredentialStore
from finahinking.data.user_api import TransportResponse
from finahinking.local_app import LocalAppConfig, LocalApplication, create_server
from finahinking.maintenance import backup_state, restore_state
from tests.research.test_strategy_worker import completed

PANEL = "/api/research/panels/"
CAPTURE = "/api/research/snapshots/capture"
STOCKS = ("STKA", "STKB", "STKC")
ETFS = ("ETFA", "ETFB", "ETFC")
TIMES = tuple(pd.date_range("2026-01-02", periods=112, freq="B", tz="UTC"))
TRADE = {"is_session": "boolean", "in_universe": "boolean", "split_factor": "ratio",
         "trade_allowed": "boolean", "lot_size": "shares", "settlement_days": "sessions",
         "cash_dividend": "CNY_per_share", "limit_rule_version": "rule_version"}
UNITS = {"rules": TRADE, "financial": {"book_equity": "CNY", "net_profit": "CNY"},
         "nav": {"nav": "CNY_per_share"}}
IDENTITY = {"instrument": "code", "timestamp": "stamp", "available_at": "known"}
PRICE_MAP = {**IDENTITY, "open": "o", "high": "h", "low": "l", "close": "c", "volume": "v",
             "suspended": "paused", "amount": "value"}
METHODS = {
    "stock": ("factor", "lib-trend-roc"),
    "etf": ("strategy", "etf-relative-strength"),
    "financial": ("factor", "lib-quality-roe"),
}


def utc_day(stamp):
    return stamp.date().isoformat()


def source_rows():
    result = {name: [] for name in ("prices", *UNITS)}
    for asset_index, code in enumerate((*STOCKS, *ETFS)):
        for index, stamp in enumerate(TIMES):
            price = 25 + asset_index * 3 + index * 0.08 + math.sin(index * 1.7 + asset_index)
            address = {"code": code, "stamp": stamp.isoformat(), "known": stamp.isoformat()}
            result["prices"].append({**address, "o": price - 0.1, "h": price + 0.2, "l": price - 0.2, "c": price,
                                     "v": 120_000, "paused": False, "value": price * 120_000})
            result["rules"].append({**address, "is_session": True, "in_universe": True, "split_factor": 1,
                                    "trade_allowed": True, "lot_size": 1, "settlement_days": 0,
                                    "cash_dividend": 0, "limit_rule_version": "UNRESTRICTED"})
            if code in ETFS:
                result["nav"].append({**address, "nav": price * (1 + asset_index / 100)})
        if code in STOCKS:
            report_end = (TIMES[0] - pd.Timedelta(days=40)).isoformat()
            first_release = TIMES[10].isoformat()
            result["financial"].extend([
                {"code": code, "stamp": report_end, "known": first_release, "announced": first_release,
                 "book_equity": 100 + 50 * asset_index, "net_profit": 12 - asset_index},
                {"code": code, "stamp": report_end, "known": first_release, "announced": first_release,
                 "revised": TIMES[53].isoformat(), "book_equity": 100 + 50 * asset_index, "net_profit": 18 - asset_index},
            ])
    return result


@pytest.fixture
def owned_feed():
    rows = source_rows()
    calls = []
    failures = {}
    # Fixture credential never leaves the local HTTP server.
    proof = "controlled-research-fixture"

    class Source(BaseHTTPRequestHandler):
        def do_GET(self):
            parts = urlsplit(self.path)
            source = parts.path.strip("/")
            query = {key: values[-1] for key, values in parse_qs(parts.query).items()}
            authenticated = self.headers.get("X-Fixture-Access") == proof
            calls.append((source, query, authenticated))
            status = failures.get(source, 200) if authenticated else 401
            reply = {}
            if status == 200:
                assets = query.get("instruments", "").split(",")
                start, end = query.get("start", "0001-01-01"), query.get("end", "9999-12-31")
                records = [row for row in rows[source] if row["code"] in assets and start <= row["stamp"][:10] <= end]
                mid = len(records) // 2
                cursor = query.get("cursor")
                reply = {"observations": records[mid:] if cursor else records[:mid]}
                if not cursor:
                    reply["next"] = "?cursor=second"
            raw = json.dumps(reply).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Source)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    class LocalFixtureTransport:
        def request(self, url, *, headers, timeout, allow_redirects=False):
            assert not allow_redirects
            parts = urlsplit(url)
            local = f"http://127.0.0.1:{server.server_port}{parts.path}?{parts.query}"
            try:
                response = urlopen(Request(local, headers=dict(headers)), timeout=timeout)
            except HTTPError as error:
                response = error
            with response:
                return TransportResponse(response.status, dict(response.headers), response.read())

    try:
        yield LocalFixtureTransport(), proof, calls, failures, rows
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@contextmanager
def running_panel_app(config, transport, credential_store):
    app = LocalApplication(config, data_transport=transport, data_credential_store=credential_store)
    server = create_server(app, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield app, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        app.close()
        thread.join(timeout=3)


def request(app, base, path, values=None, *, expected=200, authorized=True):
    kwargs = {}
    if values is not None:
        body = {**values, **({"_csrf": app.csrf_token} if authorized else {})}
        kwargs = {"data": json.dumps(body).encode(), "headers": {"Content-Type": "application/json"}}
    try:
        response = urlopen(Request(base + path, **kwargs), timeout=30)
    except HTTPError as error:
        response = error
    with response:
        payload = response.read()
        assert response.status == expected, payload
        return json.loads(payload) if response.headers["Content-Type"].startswith("application/json") else payload


def configure(app, base, proof, names):
    for name in names:
        mapping = PRICE_MAP if name == "prices" else {**IDENTITY, **dict(zip(UNITS[name], UNITS[name]))}
        if name == "financial":
            mapping.update(announcement_at="announced", revision_at="revised")
        request(app, base, "/api/data/connections", {
            "connection_id": name, "display_name": f"Controlled {name}", "base_url": f"https://8.8.8.8/{name}",
            "field_mapping": mapping, "records_path": "observations", "auth_mode": "api_key_header",
            "auth_header": "X-Fixture-Access", "api_key": proof,
        }, expected=201)


def capture_request(assets, end=TIMES[-1]):
    return {
        "connection_id": "prices", "instrument": ",".join(assets),
        "price_currency": "CNY", "volume_unit": "shares", "adjustment": "RAW",
        "asset_classes": dict.fromkeys(assets, "ETF" if assets == ETFS else "EQUITY"),
        "start": utc_day(TIMES[0]), "end": utc_day(end),
    }


def attach_request(ref, assets, source, units=None):
    return {"snapshot_ref": ref, "instrument_ids": list(assets),
            "connection_id": source, "units": UNITS[source] if units is None else units}


def draft_input(snap_ref, rule_ref, assets, kind, extra=()):
    target, path = METHODS[kind]
    return {
        "name": f"{kind} 研究", "snapshot_ref": snap_ref, "instrument_ids": list(assets),
        "start": utc_day(TIMES[0]), "end": utc_day(TIMES[-1]), "timezone": "UTC",
        "rules": dict.fromkeys(("calendar_ref", "universe_ref", "adjustment_ref", "trade_rule_ref"), rule_ref),
        "attachment_refs": list(extra), "target_kind": target, "target_id": path,
    }


@pytest.mark.parametrize("kind", ("stock", "etf", "financial"))
def test_owned_feed_to_saved_panel_to_spawned_report(tmp_path, owned_feed, kind):
    transport, proof, calls, failures, rows = owned_feed
    config = LocalAppConfig(db_path=str(tmp_path / "app.sqlite3"), offline=True)
    credentials = InMemoryDataCredentialStore()
    assets = ETFS if kind == "etf" else STOCKS
    names = ["prices", "rules"] + (["nav"] if kind == "etf" else ["financial"] if kind == "financial" else [])
    with running_panel_app(config, transport, credentials) as (app, base):
        configure(app, base, proof, names)
        assert calls == []  # Configuration and local reads never fetch remote data.
        snap = request(app, base, CAPTURE, capture_request(assets, TIMES[-2] if kind == "stock" else TIMES[-1]), expected=201)
        if kind == "stock":
            first = snap
            extension = {"base_ref": first["snapshot_ref"], "end": utc_day(TIMES[-1])}
            failures["prices"] = 429
            sent = len(calls)
            assert request(app, base, CAPTURE, extension, expected=429)["code"] == "rate_limited"
            assert len(calls) == sent + 1
            assert request(app, base, "/api/research/snapshots")["snapshots"] == [first]
            failures.pop("prices")
            snap = request(app, base, CAPTURE, extension, expected=201)
            assert calls[-1][1]["start"] == utc_day(TIMES[-2])
            assert snap["record_count"] == first["record_count"] + len(assets)
            assert request(app, base, CAPTURE, {**extension, "base_ref": snap["snapshot_ref"]}, expected=201) == snap
            # Revised overlaps require a new full capture.
            changed = next(row for row in rows["prices"] if row["code"] == assets[0] and row["stamp"] == TIMES[-2].isoformat())
            changed["v"] += 1
            rejected = request(app, base, CAPTURE, extension, expected=422)
            assert "历史修订" in rejected["error"]
            assert app.research_service.snapshots.load(first["snapshot_ref"]).fingerprint == first["snapshot_ref"]
        snap_ref = snap["snapshot_ref"]
        rule_ref = request(app, base, PANEL + "capture-fields", attach_request(snap_ref, assets, "rules"), expected=201)["attachment_ref"]
        extra = []
        if len(names) > 2:
            field_ref = request(app, base, PANEL + "capture-fields", attach_request(snap_ref, assets, names[-1]), expected=201)["attachment_ref"]
            extra.append(field_ref)
        draft = draft_input(snap_ref, rule_ref, assets, kind, extra)
        assert request(app, base, PANEL + "preview", draft, expected=403, authorized=False)
        if kind == "financial":
            missing = request(app, base, PANEL + "preview", {**draft, "rules": {}})
            assert missing["status"] == "NEEDS_DATA" and any("交易日历" in issue for issue in missing["issues"])
            bad_ref = request(app, base, PANEL + "capture-fields",
                              attach_request(snap_ref, assets, "financial", {"book_equity": "USD", "net_profit": "USD"}),
                              expected=201)["attachment_ref"]
            wrong = request(app, base, PANEL + "preview", {**draft, "attachment_refs": [bad_ref]})
            assert wrong["status"] == "NEEDS_DATA"
            assert any("币种" in issue for issue in wrong["issues"])
            removed = TIMES[6].isoformat()
            rows["rules"] = [row for row in rows["rules"] if row["stamp"] != removed]
            sparse_ref = request(app, base, PANEL + "capture-fields", attach_request(snap_ref, assets, "rules"),
                                 expected=201)["attachment_ref"]
            sparse = request(app, base, PANEL + "preview", {
                **draft, "rules": dict.fromkeys(draft["rules"], sparse_ref),
            })
            assert sparse["status"] == "NEEDS_DATA" and any("缺少当时可用的日历" in issue for issue in sparse["issues"])
        preview = request(app, base, PANEL + "preview", draft)
        assert preview["status"] == "READY" and not preview["target_issues"], preview
        if kind == "financial":
            evidence = next(row for row in preview["fields"] if row["field"] == "net_profit")
            assert evidence["available_at"] == [TIMES[10].isoformat(), TIMES[53].isoformat()]
            assert evidence["covered"] == (len(TIMES) - 10) * len(STOCKS)
        save = {**draft, "preview_fingerprint": preview["preview_fingerprint"]}
        stale = request(app, base, PANEL + "save", {**save, "start": utc_day(TIMES[1])}, expected=422)
        assert "重新预览" in stale["error"]
        saved = request(app, base, PANEL + "save", save, expected=201)
        assert request(app, base, PANEL + "save", save, expected=201) == saved
        assert app.research_service.list_jobs()["total"] == 0
        assert all(entry[2] for entry in calls) and any(entry[1].get("cursor") == "second" for entry in calls)

    wire_count = len(calls)
    if kind == "stock":
        archive = backup_state(config.db_path, tmp_path / "panel-state.zip")
        config = LocalAppConfig(db_path=str(restore_state(archive, tmp_path / "restored")), offline=True)
    with running_panel_app(config, transport, credentials) as (app, base):
        service = app.research_service
        assert request(app, base, PANEL + "saved") == {"panels": [saved]}
        binding = {"saved_panel_ref": saved["panel_ref"]}
        if kind == "stock":
            endpoint = "/api/research/discovery"
            question = {**binding, "question": "过去的价格动量能否解释下一观察点的收益？"}
            blueprint = request(app, base, endpoint + "/preview", question)
            assert blueprint["status"] == "READY", blueprint
            submission = {**question, "blueprint_fingerprint": blueprint["fingerprint"], "authorized": True,
                          "idempotency_key": "phase29-discovery"}
            payload = request(app, base, endpoint, submission, expected=202)["job"]
            assert request(app, base, endpoint, submission, expected=202)["job"]["job_id"] == payload["job_id"]
        elif kind == "etf":
            endpoint = "/api/research/strategies"
            submission = {**binding, "template_id": "etf-relative-strength", "idempotency_key": "phase29-etf"}
            payload = request(app, base, endpoint, submission, expected=202)
            assert request(app, base, endpoint, submission, expected=202)["job_id"] == payload["job_id"]
        else:
            endpoint = "/api/research/factor-library/studies"
            submission = {**binding, "factor_id": "lib-quality-roe", "question": "检验具有发布滞后的净资产收益因子",
                          "idempotency_key": "phase29-financial"}
            payload = request(app, base, endpoint, submission, expected=202)["validation_job"]
            assert request(app, base, endpoint, submission, expected=202)["validation_job"]["job_id"] == payload["job_id"]
            record = service.panels.load(saved["panel_ref"])
            frame = service.snapshots.build_panel(record["contract"], record["attachment_refs"]).frame
            assert pd.isna(frame.loc[(TIMES[9], STOCKS[0]), "net_profit"])
            assert frame.loc[(TIMES[10], STOCKS[0]), "net_profit"] == 12
            assert frame.loc[(TIMES[52], STOCKS[0]), "net_profit"] == 12
            assert frame.loc[(TIMES[53], STOCKS[0]), "net_profit"] == 18
        job = completed(service, payload["job_id"])
        report = request(app, base, job["report_url"])
        assert saved["panel_fingerprint"].encode() in report
        if kind == "etf":
            result = job["strategy_study"]["result"]
            assert result["status"] == "COMPLETE" and result["ledger"]["fills"]
        else:
            assert job["factor_study"]["library_validation"]["snapshot_ref"] == snap_ref
        assert service.list_jobs()["total"] == 1
        assert len(calls) == wire_count
