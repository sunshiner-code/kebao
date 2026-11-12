"""企业连接器实接：配置锁定、推送入库、运营拉取、仿真隔离。"""

from __future__ import annotations

import json
import os
import socket
import ssl
import tempfile
import threading
import uuid
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

os.environ["TECHINS_DATA_DIR"] = tempfile.mkdtemp(prefix="techins-live-conn-")
os.environ["TECHINS_DISABLE_LLM"] = "1"
os.environ["TECHINS_MONITOR_ENABLED"] = "0"
os.environ["TECHINS_DEMO_BOOTSTRAP"] = "0"

import ipaddress

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from techins.api.app import app
from techins.config import settings
from techins.db import auto_migrate, engine, session_scope, utcnow
from techins.models import Connector, DataAuthorization, Enterprise, MonitorPlan, Policy, Product, TelemetryPoint
from techins.services import connectors, ops
from techins.services.monitoring import detect, generate


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _login(client: TestClient) -> dict:
    res = client.post("/api/auth/login", json={"username": "admin", "password": "demo1234"})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


def _scenario(**flags) -> dict:
    base = {"device": True, "logs": True, "ops": True, "assets": [{"serial": "EQ-1", "name": "测试设备", "value": 1}], "device_mode": "normal", "log_mode": "normal", "unpatched": 0}
    base.update(flags)
    return base


def _policy(sim_start, hour: int, scenario: dict) -> tuple[int, int]:
    suffix = uuid.uuid4().hex[:8]
    with session_scope() as s:
        ent = s.scalars(select(Enterprise)).first()
        product = Product(code=f"LIVE-{suffix}", name="实接测试产品", lines=["L02", "L08", "L01"], status="published")
        s.add(product)
        s.flush()
        policy = Policy(policy_no=f"PLIVE{suffix}", enterprise_id=ent.id, product_id=product.id, coverage={"lines": ["L02", "L08", "L01"], "data_authorizations": {"iot": True, "log": True, "ops": True}}, premium=1, sum_insured=1, start_date=utcnow(), end_date=utcnow() + timedelta(days=365), status="active")
        s.add(policy)
        s.flush()
        s.add(MonitorPlan(policy_id=policy.id, indicators=[], state={"hour": hour, "sim_start": sim_start.isoformat(), "scenario": scenario}, status="active"))
        s.add(DataAuthorization(auth_no=f"SQ{suffix}", enterprise_id=ent.id, policy_id=policy.id, scopes=["iot", "log", "ops"], purpose="测试", grantor="测试", valid_from=utcnow() - timedelta(days=1), valid_to=utcnow() + timedelta(days=30), status="active"))
        return ent.id, policy.id


def _kinds(policy_id: int) -> dict[str, Connector]:
    ops.sync_connectors()
    with session_scope() as s:
        rows = s.scalars(select(Connector).where(Connector.policy_id == policy_id)).all()
        for row in rows:
            s.expunge(row)
        return {row.kind: row for row in rows}


def test_auto_migrate_adds_connector_columns(client):
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE connectors RENAME TO connectors_backup_live"))
        conn.execute(text("""
            CREATE TABLE connectors (
                id INTEGER PRIMARY KEY,
                enterprise_id INTEGER,
                policy_id INTEGER,
                kind VARCHAR(16),
                name VARCHAR(128),
                endpoint VARCHAR(256),
                status VARCHAR(16),
                authorization_id INTEGER,
                last_heartbeat DATETIME,
                quality JSON,
                created_at DATETIME
            )
        """))
    try:
        added = auto_migrate()
        with engine.begin() as conn:
            cols = {row[1] for row in conn.execute(text('PRAGMA table_info("connectors")')).fetchall()}
        assert {"mode", "direction", "host", "port", "path", "allow_ips", "field_map", "asset_map", "pull_interval_minutes", "pull_secret", "token_hash", "configured", "last_error"} <= cols
        assert any(item.startswith("connectors.") for item in added)
    finally:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS connectors"))
            conn.execute(text("ALTER TABLE connectors_backup_live RENAME TO connectors"))


def test_sync_keeps_configured_host_and_demo_stays_simulated(client):
    _, policy_id = _policy(utcnow(), 1, _scenario())
    rows = _kinds(policy_id)
    assert rows["iot"].mode == "sim"
    assert "techins-edge.cn" in rows["iot"].endpoint
    headers = _login(client)
    saved = client.patch(f"/api/admin/connectors/{rows['ops'].id}", headers=headers, json={"mode": "sim", "direction": "push", "host": "10.8.8.8", "port": 443, "path": "/ops"})
    assert saved.status_code == 200, saved.text
    ops.sync_connectors()
    with session_scope() as s:
        again = s.get(Connector, rows["ops"].id)
        assert again.host == "10.8.8.8"
        assert "10.8.8.8" not in (again.endpoint or "") or again.configured
        assert again.host == "10.8.8.8"
    with session_scope() as s:
        plan = s.scalars(select(MonitorPlan).where(MonitorPlan.policy_id == policy_id)).first()
        before = s.scalar(select(func.count(TelemetryPoint.id)).where(TelemetryPoint.policy_id == policy_id, TelemetryPoint.source == "iot")) or 0
        generate(plan, 0, 2, s)
        s.flush()
        after = s.scalar(select(func.count(TelemetryPoint.id)).where(TelemetryPoint.policy_id == policy_id, TelemetryPoint.source == "iot")) or 0
    assert after > before


def test_patch_rejects_pull_on_iot_and_hides_secret(client):
    _, policy_id = _policy(utcnow(), 1, _scenario())
    rows = _kinds(policy_id)
    headers = _login(client)
    denied = client.patch(f"/api/admin/connectors/{rows['iot'].id}", headers=headers, json={"mode": "live", "direction": "pull", "host": "10.9.9.9", "port": 443, "path": "/iot", "secret": "should-not-save"})
    assert denied.status_code == 422
    assert "拉取只用于" in denied.json()["error"]["message"]
    saved = client.patch(f"/api/admin/connectors/{rows['ops'].id}", headers=headers, json={"mode": "live", "direction": "pull", "host": "10.9.9.9", "port": 8443, "path": "/ops", "secret": "pull-password-9"})
    assert saved.status_code == 200, saved.text
    assert "pull-password-9" not in saved.text
    listed = client.get("/api/admin/connectors", headers=headers)
    body = next(item for item in listed.json() if item["id"] == rows["ops"].id)
    assert body["has_secret"] is True
    assert "pull-password-9" not in listed.text
    assert "pull_secret" not in body
    rotated = client.post(f"/api/admin/connectors/{rows['iot'].id}/token", headers=headers)
    assert rotated.status_code == 200, rotated.text
    token = rotated.json()["token"]
    again = client.get("/api/admin/connectors", headers=headers)
    assert token not in again.text
    assert (settings.data_dir / "connector.key").exists()
    assert "data/" in Path("gitignore").read_text() if False else "data/" in (Path(__file__).resolve().parents[1] / ".gitignore").read_text()


def test_push_ingest_updates_and_rejects_revoked_auth(client):
    _, policy_id = _policy(utcnow(), 1, _scenario())
    rows = _kinds(policy_id)
    headers = _login(client)
    iot = rows["iot"].id
    patched = client.patch(f"/api/admin/connectors/{iot}", headers=headers, json={"mode": "live", "direction": "push", "field_map": {"vib": "vibration_rms"}, "asset_map": {"DEV": "EQ-1"}, "allow_ips": ["testclient"]})
    assert patched.status_code == 200, patched.text
    token = client.post(f"/api/admin/connectors/{iot}/token", headers=headers).json()["token"]
    ts = utcnow().replace(microsecond=0).isoformat()
    point = {"asset": "DEV", "metric": "vib", "ts": ts, "value": 1.25}
    bad_ip = client.post(f"/api/ingest/connectors/{iot}", headers={"Authorization": "Bearer other"}, json={"points": [point]})
    assert bad_ip.status_code == 401
    blocked = client.post(f"/api/ingest/connectors/{iot}", headers={"Authorization": f"Bearer {token}"}, json={"points": [{"asset": "NOPE", "metric": "vib", "ts": ts, "value": 1}]})
    assert blocked.status_code == 422
    ok = client.post(f"/api/ingest/connectors/{iot}", headers={"Authorization": f"Bearer {token}"}, json={"points": [point]})
    assert ok.status_code == 200, ok.text
    again = client.post(f"/api/ingest/connectors/{iot}", headers={"Authorization": f"Bearer {token}"}, json={"points": [{**point, "value": 2.5}]})
    assert again.status_code == 200, again.text
    with session_scope() as s:
        found = s.scalars(select(TelemetryPoint).where(TelemetryPoint.policy_id == policy_id, TelemetryPoint.metric == "vibration_rms")).all()
        assert len(found) == 1 and found[0].value == 2.5 and found[0].asset == "EQ-1"
        auth = s.scalars(select(DataAuthorization).where(DataAuthorization.policy_id == policy_id)).first()
        auth.status = "revoked"
    denied = client.post(f"/api/ingest/connectors/{iot}", headers={"Authorization": f"Bearer {token}"}, json={"points": [point]})
    assert denied.status_code == 403


def test_pull_rejects_loopback_and_accepts_private_https(client):
    _, policy_id = _policy(utcnow(), 1, _scenario())
    rows = _kinds(policy_id)
    headers = _login(client)
    cid = rows["ops"].id
    denied = client.patch(f"/api/admin/connectors/{cid}", headers=headers, json={"mode": "live", "direction": "pull", "host": "127.0.0.1", "port": 443, "path": "/ops", "secret": "x"})
    assert denied.status_code == 422
    with session_scope() as s:
        row = s.get(Connector, cid)
        row.mode, row.direction, row.kind = "live", "pull", "ops"
        row.host, row.port, row.path, row.pull_secret = "127.0.0.1", 9, "/ops", connectors.encrypt_secret("x")
        row.configured = True
    before = _count(policy_id, "ops")
    failed = connectors.pull_one(cid)
    assert failed["ok"] is False
    assert _count(policy_id, "ops") == before
    with session_scope() as s:
        assert "本机" in (s.get(Connector, cid).last_error or "")

    ip = _outbound_ip()
    if ipaddress.ip_address(ip).is_loopback:
        pytest.skip("没有可用于拉取测试的非本机地址")
    payload = {"points": [{"asset": "proj", "metric": "milestone_delay_days", "ts": utcnow().replace(microsecond=0).isoformat(), "value": 4}]}
    server, port = _https_server(ip, payload)
    try:
        saved = client.patch(f"/api/admin/connectors/{cid}", headers=headers, json={"mode": "live", "direction": "pull", "host": ip, "port": port, "path": "/ops", "secret": "pull-secret", "pull_interval_minutes": 60})
        assert saved.status_code == 200, saved.text
        with session_scope() as s:
            quality = dict(s.get(Connector, cid).quality or {})
            quality.pop("last_pull_at", None)
            s.get(Connector, cid).quality = quality
        pulled = connectors.pull_due()
        assert any(item.get("id") == cid and item.get("ok") for item in pulled)
        assert _count(policy_id, "ops") == before + 1
        pulled_again = connectors.pull_due()
        assert all(item.get("id") != cid for item in pulled_again)
        assert _count(policy_id, "ops") == before + 1
    finally:
        server.shutdown()
        server.server_close()


def test_live_channel_skips_simulation_and_uses_wall_clock(client):
    sim_start = utcnow() - timedelta(hours=10)
    _, policy_id = _policy(sim_start, 400, _scenario())
    rows = _kinds(policy_id)
    with session_scope() as s:
        s.get(Connector, rows["log"].id).mode = "live"
        plan = s.scalars(select(MonitorPlan).where(MonitorPlan.policy_id == policy_id)).first()
        iot_before = s.scalar(select(func.count(TelemetryPoint.id)).where(TelemetryPoint.policy_id == policy_id, TelemetryPoint.source == "iot")) or 0
        log_before = s.scalar(select(func.count(TelemetryPoint.id)).where(TelemetryPoint.policy_id == policy_id, TelemetryPoint.source == "log")) or 0
        generate(plan, 0, 3, s)
    assert _count(policy_id, "iot") > iot_before
    assert _count(policy_id, "log") == log_before
    sim_now = sim_start + timedelta(hours=400)
    with session_scope() as s:
        for i in range(80):
            s.add(TelemetryPoint(policy_id=policy_id, source="iot", asset="EQ-1", metric="vibration_rms", ts=sim_now - timedelta(hours=10 + i), value=2.0))
            s.add(TelemetryPoint(policy_id=policy_id, source="log", asset="核心业务系统", metric="login_failures", ts=utcnow() - timedelta(hours=i), value=1.0))
            s.add(TelemetryPoint(policy_id=policy_id, source="log", asset="核心业务系统", metric="egress_mb", ts=utcnow() - timedelta(hours=i), value=1.0))
        plan = s.scalars(select(MonitorPlan).where(MonitorPlan.policy_id == policy_id)).first()
        s.expunge(plan)
    signals = detect(plan)
    keys = {sig["key"] for sig in signals}
    assert "feed-EQ-1" in keys
    assert "feed-log" not in keys

    _, short_id = _policy(utcnow(), 1, _scenario(logs=False, ops=False))
    short = _kinds(short_id)
    with session_scope() as s:
        s.get(Connector, short["iot"].id).mode = "live"
        for i in range(5):
            s.add(TelemetryPoint(policy_id=short_id, source="iot", asset="EQ-1", metric="vibration_rms", ts=utcnow() - timedelta(hours=i), value=2.0))
        plan = s.scalars(select(MonitorPlan).where(MonitorPlan.policy_id == short_id)).first()
        s.expunge(plan)
    assert detect(plan) == []


def test_live_connector_rejects_simulated_outage(client):
    _, policy_id = _policy(utcnow(), 1, _scenario())
    rows = _kinds(policy_id)
    headers = _login(client)
    with session_scope() as s:
        s.get(Connector, rows["iot"].id).mode = "live"
    denied = client.post(f"/api/admin/connectors/{rows['iot'].id}/outage?outage=true", headers=headers)
    assert denied.status_code == 409
    assert "不能模拟中断" in denied.json()["error"]["message"]
    allowed = client.post(f"/api/admin/connectors/{rows['log'].id}/outage?outage=true", headers=headers)
    assert allowed.status_code == 200, allowed.text
    with session_scope() as s:
        plan = s.scalars(select(MonitorPlan).where(MonitorPlan.policy_id == policy_id)).first()
        assert plan.state["outage"]["log"] is True
        assert "iot" not in (plan.state.get("outage") or {})


def _count(policy_id: int, source: str) -> int:
    with session_scope() as s:
        return s.scalar(select(func.count(TelemetryPoint.id)).where(TelemetryPoint.policy_id == policy_id, TelemetryPoint.source == source)) or 0


def _outbound_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))
        return sock.getsockname()[0]
    finally:
        sock.close()


def _https_server(ip: str, payload: dict) -> tuple[HTTPServer, int]:
    cert = Path(tempfile.mkdtemp()) / "cert.pem"
    key = cert.with_name("key.pem")
    _write_cert(ip, cert, key)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            return

    httpd = HTTPServer((ip, 0), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _write_cert(ip: str, cert_path: Path, key_path: Path) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, ip)])
    now = utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(ip))]), critical=False)
        .sign(key, hashes.SHA256())
    )
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


# ---------------------------------------------------------------- 外部数据源健康探测
class _StubSource:
    """探测用的假数据源：正常时返回空列表，失败时抛出指定异常。"""

    def __init__(self, fail: bool = False):
        self.fail = fail

    def __getattr__(self, name):
        def call(*_args, **_kwargs):
            if self.fail:
                raise RuntimeError("synthetic probe failure")
            return []

        return call


def _policy_source():
    from techins.models import DataSource

    with session_scope() as s:
        ds = s.scalars(select(DataSource).where(DataSource.code == "policy")).first()
        if ds is None:
            ds = DataSource(code="policy", name="政策法规库", category="政策法规", provider="测试")
            s.add(ds)
            s.flush()
        ds.last_error = "stale 1064"
        s.expunge(ds)


def test_data_source_error_is_cleared_after_successful_call(client):
    from techins.models import DataSource

    _policy_source()
    ops.record_source_call("policy", ok=False, latency_ms=12, error="(1064) near 'lines'")
    with session_scope() as s:
        ds = s.scalars(select(DataSource).where(DataSource.code == "policy")).first()
        assert ds.last_error and "1064" in ds.last_error
    ops.record_source_call("policy", ok=True, latency_ms=8)
    with session_scope() as s:
        ds = s.scalars(select(DataSource).where(DataSource.code == "policy")).first()
        assert not ds.last_error


def test_probe_clears_stale_error_and_counts_failures(monkeypatch, client):
    from techins.models import DataSource
    from techins.datasources import public

    _policy_source()
    monkeypatch.setattr(public, "get_source", lambda: _StubSource(fail=False))
    ops.probe_sources()
    with session_scope() as s:
        ds = s.scalars(select(DataSource).where(DataSource.code == "policy")).first()
        assert not ds.last_error, "探测成功后不应保留历史错误"
        calls_before, failures_before = ds.calls, ds.failures

    monkeypatch.setattr(public, "get_source", lambda: _StubSource(fail=True))
    ops.probe_sources()
    with session_scope() as s:
        ds = s.scalars(select(DataSource).where(DataSource.code == "policy")).first()
        assert ds.last_error, "探测失败时应记录错误"
        assert ds.calls > calls_before and ds.failures > failures_before
        assert ds.failure_rate > 0
