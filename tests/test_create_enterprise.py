"""新建企业客户：权限、信用代码去重、客户经理与审计、唯一索引。"""

from __future__ import annotations

import os
import tempfile

os.environ["TECHINS_DATA_DIR"] = tempfile.mkdtemp(prefix="techins-create-ent-")
os.environ["TECHINS_DISABLE_LLM"] = "1"
os.environ["TECHINS_MONITOR_ENABLED"] = "0"
os.environ["TECHINS_DEMO_BOOTSTRAP"] = "0"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select

from techins.api.app import app
from techins.db import engine, session_scope
from techins.models import AuditLog, Enterprise


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _login(client: TestClient, username: str) -> tuple[dict, dict]:
    res = client.post("/api/auth/login", json={"username": username, "password": "demo1234"})
    assert res.status_code == 200, res.text
    body = res.json()
    return body["user"], {"Authorization": f"Bearer {body['token']}"}


def _audit_username(enterprise_id: int) -> str | None:
    with session_scope() as s:
        row = s.scalars(select(AuditLog).where(AuditLog.action == "新建企业客户", AuditLog.object_id == str(enterprise_id))).first()
        return row.username if row else None


def test_service_creates_customer_and_is_manager(client: TestClient):
    user, headers = _login(client, "service")
    res = client.post("/api/enterprises", headers=headers, json={"name": "新建测试机器人有限公司", "credit_code": "91320594MA1NEW0001"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["manager_id"] == user["id"]
    with session_scope() as s:
        row = s.get(Enterprise, body["id"])
        assert row is not None and row.manager_id == user["id"] and row.profile == {}
    assert _audit_username(body["id"]) == "service"
    detail = client.get(f"/api/enterprises/{body['id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["profile"] == {}


def test_invalid_code_and_blank_name_rejected(client: TestClient):
    _, headers = _login(client, "service")
    bad_code = client.post("/api/enterprises", headers=headers, json={"name": "格式错误企业", "credit_code": "ABC"})
    assert bad_code.status_code == 422
    assert "18" in bad_code.json()["error"]["message"]
    blank = client.post("/api/enterprises", headers=headers, json={"name": "  ", "credit_code": "91320594MA1NEW0002"})
    assert blank.status_code == 422


def test_duplicate_credit_code_returns_existing_without_insert(client: TestClient):
    _, headers = _login(client, "service")
    with session_scope() as s:
        existing = s.scalars(select(Enterprise).where(Enterprise.name == "星衡智能装备股份有限公司")).first()
        existing_id, existing_name, code = existing.id, existing.name, existing.credit_code
        count_before = len(s.scalars(select(Enterprise)).all())
    res = client.post("/api/enterprises", headers=headers, json={"name": "另一个不应该被创建的名字", "credit_code": code.lower()})
    assert res.status_code == 409, res.text
    err = res.json()["error"]
    assert err["object"]["id"] == existing_id
    assert err["object"]["name"] == existing_name
    with session_scope() as s:
        count_after = len(s.scalars(select(Enterprise)).all())
    assert count_after == count_before


def test_duplicate_name_does_not_offer_existing_id(client: TestClient):
    _, headers = _login(client, "service")
    res = client.post("/api/enterprises", headers=headers, json={"name": "星衡智能装备股份有限公司", "credit_code": "91320594MA1NEW0003"})
    assert res.status_code == 409, res.text
    assert res.json()["error"].get("object") is None


def test_designer_creates_without_manager(client: TestClient):
    _, headers = _login(client, "designer")
    res = client.post("/api/enterprises", headers=headers, json={"name": "设计岗新建材料公司", "credit_code": "91320594MA1DES0001"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["manager_id"] is None
    with session_scope() as s:
        row = s.get(Enterprise, body["id"])
        assert row.manager_id is None
    assert _audit_username(body["id"]) == "designer"


@pytest.mark.parametrize("username", ["admin", "bizadmin"])
def test_admins_cannot_create(client: TestClient, username: str):
    _, headers = _login(client, username)
    res = client.post("/api/enterprises", headers=headers, json={"name": f"{username}不该建成的公司", "credit_code": "91320594MA1ADM0001"})
    assert res.status_code == 403, res.text


def test_credit_code_unique_index_exists(client: TestClient):
    assert client.get("/api/health").status_code == 200
    indexes = inspect(engine).get_indexes("enterprises")
    assert any(idx.get("unique") and idx.get("column_names") == ["credit_code"] for idx in indexes)
