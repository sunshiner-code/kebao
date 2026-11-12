"""新建账号校验与个人中心改密。"""

from __future__ import annotations

import os
import tempfile

os.environ["TECHINS_DATA_DIR"] = tempfile.mkdtemp(prefix="techins-account-")
os.environ["TECHINS_DISABLE_LLM"] = "1"
os.environ["TECHINS_MONITOR_ENABLED"] = "0"
os.environ["TECHINS_DEMO_BOOTSTRAP"] = "0"
os.environ["TECHINS_SHOW_DEMO_ACCOUNTS"] = "0"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from techins.api.app import app
from techins.db import session_scope
from techins.models import User


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _login(client: TestClient, username: str, password: str = "demo1234") -> dict:
    res = client.post("/api/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, res.text
    body = res.json()
    return {"Authorization": f"Bearer {body['token']}"}


def _count_users() -> int:
    with session_scope() as s:
        return int(s.scalar(select(func.count(User.id))) or 0)


def test_demo_accounts_hidden_unless_configured(client: TestClient):
    res = client.get("/api/auth/options")
    assert res.status_code == 200
    assert res.json() == {"show_demo_accounts": False}


def test_blank_account_is_rejected_and_not_created(client: TestClient):
    headers = _login(client, "admin")
    before = _count_users()
    res = client.post("/api/admin/users", headers=headers, json={"username": "  ", "display_name": "", "password": "", "password_confirm": "", "role": "service"})
    assert res.status_code == 422, res.text
    message = res.json()["error"]["message"]
    assert "请填写用户名" in message
    assert "请填写姓名" in message
    assert "请填写初始密码" in message
    assert _count_users() == before


def test_username_password_and_confirm_rules(client: TestClient):
    headers = _login(client, "admin")
    bad_name = client.post(
        "/api/admin/users",
        headers=headers,
        json={"username": "1bad", "display_name": "格式错误", "password": "Demo1234", "password_confirm": "Demo1234", "role": "service"},
    )
    assert bad_name.status_code == 422
    assert "例如 zhangsan" in bad_name.json()["error"]["message"]

    weak = client.post(
        "/api/admin/users",
        headers=headers,
        json={"username": "zhangsan", "display_name": "张三", "password": "password", "password_confirm": "password", "role": "service"},
    )
    assert weak.status_code == 422
    assert "字母与数字" in weak.json()["error"]["message"]

    mismatch = client.post(
        "/api/admin/users",
        headers=headers,
        json={"username": "zhangsan", "display_name": "张三", "password": "Demo1234", "password_confirm": "Demo1235", "role": "service"},
    )
    assert mismatch.status_code == 422
    assert "不一致" in mismatch.json()["error"]["message"]

    same = client.post(
        "/api/admin/users",
        headers=headers,
        json={"username": "Demo1234", "display_name": "张三", "password": "Demo1234", "password_confirm": "Demo1234", "role": "designer"},
    )
    assert same.status_code == 422
    assert "不能与用户名相同" in same.json()["error"]["message"]


def test_valid_account_can_login(client: TestClient):
    headers = _login(client, "admin")
    res = client.post(
        "/api/admin/users",
        headers=headers,
        json={"username": " zhangsan ", "display_name": " 张三 ", "password": "Demo1234", "password_confirm": "Demo1234", "role": "service"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["username"] == "zhangsan"
    assert body["display_name"] == "张三"
    login = client.post("/api/auth/login", json={"username": "zhangsan", "password": "Demo1234"})
    assert login.status_code == 200, login.text


def test_password_change_requires_current_match_and_complexity(client: TestClient):
    headers = _login(client, "service")
    empty = client.post("/api/me/password", headers=headers, json={"current_password": "", "new_password": "", "confirm_password": ""})
    assert empty.status_code == 422
    assert "请填写当前密码" in empty.json()["error"]["message"]
    assert "请填写新密码" in empty.json()["error"]["message"]

    wrong = client.post("/api/me/password", headers=headers, json={"current_password": "nope1234", "new_password": "Demo5678", "confirm_password": "Demo5678"})
    assert wrong.status_code == 422
    assert "当前密码不正确" in wrong.json()["error"]["message"]

    mismatch = client.post("/api/me/password", headers=headers, json={"current_password": "demo1234", "new_password": "Demo5678", "confirm_password": "Demo5679"})
    assert mismatch.status_code == 422
    assert "不一致" in mismatch.json()["error"]["message"]

    weak = client.post("/api/me/password", headers=headers, json={"current_password": "demo1234", "new_password": "onlyletters", "confirm_password": "onlyletters"})
    assert weak.status_code == 422
    assert "字母与数字" in weak.json()["error"]["message"]

    same = client.post("/api/me/password", headers=headers, json={"current_password": "demo1234", "new_password": "demo1234", "confirm_password": "demo1234"})
    assert same.status_code == 422
    assert "不能与当前密码相同" in same.json()["error"]["message"]

    ok = client.post("/api/me/password", headers=headers, json={"current_password": "demo1234", "new_password": "Demo5678", "confirm_password": "Demo5678"})
    assert ok.status_code == 200, ok.text
    old = client.post("/api/auth/login", json={"username": "service", "password": "demo1234"})
    assert old.status_code == 401
    new_headers = _login(client, "service", "Demo5678")
    me = client.get("/api/me", headers=new_headers)
    assert me.status_code == 200
    assert me.json()["username"] == "service"
    # 还原演示账号密码，避免后续用例无法登录
    restored = client.post("/api/me/password", headers=new_headers, json={"current_password": "Demo5678", "new_password": "demo1234", "confirm_password": "demo1234"})
    assert restored.status_code == 200, restored.text
