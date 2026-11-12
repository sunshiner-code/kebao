"""企业数据连接器的实接配置、推送入库与运营通道拉取。"""

from __future__ import annotations

import hashlib
import ipaddress
import math
import secrets
import socket
from datetime import datetime, timedelta, timezone

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select

from ..config import settings
from ..db import session_scope, utcnow
from ..models import Connector, DataAuthorization, MonitorPlan, TelemetryPoint

METRICS = {
    "iot": {"vibration_rms", "bearing_temp", "motor_current", "water_leak"},
    "log": {"login_failures", "egress_mb", "unpatched_critical"},
    # 运营通道：研发进度 + 险种级 KRI（机器人本体、控制系统事故、临床试验不良事件等）
    "ops": {"milestone_delay_days", "rd_spend_index", "damage_events", "complaints", "ae_reports", "cash_months", "trial_stop", "pilot_yield"},
}
FIXED_ASSET = {"log": "核心业务系统", "ops": "研发项目"}


class ConnectorError(ValueError):
    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


def _fernet() -> Fernet:
    raw = settings.connector_key if isinstance(settings.connector_key, str) else ""
    if not raw:
        path = settings.data_dir / "connector.key"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(Fernet.generate_key())
        raw = path.read_text().strip()
    return Fernet(raw.encode())


def encrypt_secret(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_secret(stored: str) -> str:
    if not stored:
        return ""
    try:
        return _fernet().decrypt(stored.encode()).decode()
    except InvalidToken as exc:
        raise ConnectorError("拉取凭证无法解密", 422) from exc


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def ingest_url(connector_id: int) -> str:
    return f"/api/ingest/connectors/{connector_id}"


def public_view(c: Connector) -> dict:
    return {
        "mode": c.mode or "sim",
        "direction": c.direction or "push",
        "host": c.host or "",
        "port": c.port or 443,
        "path": c.path or "",
        "allow_ips": list(c.allow_ips or []),
        "field_map": dict(c.field_map or {}),
        "asset_map": dict(c.asset_map or {}),
        "pull_interval_minutes": c.pull_interval_minutes or 60,
        "configured": bool(c.configured),
        "last_error": c.last_error or "",
        "has_token": bool(c.token_hash),
        "has_secret": bool(c.pull_secret),
        "ingest_url": ingest_url(c.id),
    }


def _parse_ts(raw: str) -> datetime:
    text = str(raw).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ConnectorError("时间格式不正确") from exc
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    now = utcnow()
    if dt > now + timedelta(minutes=10):
        raise ConnectorError("时间晚于当前超过 10 分钟")
    if dt < now - timedelta(days=400):
        raise ConnectorError("时间早于 400 天")
    return dt


def _plan_assets(s, policy_id: int) -> set[str]:
    plan = s.scalars(select(MonitorPlan).where(MonitorPlan.policy_id == policy_id)).first()
    assets = ((plan.state or {}).get("scenario") or {}).get("assets") if plan else []
    return {a.get("serial") for a in assets or [] if a.get("serial")}


def _authorized(s, c: Connector) -> bool:
    now = utcnow()
    rows = s.scalars(select(DataAuthorization).where(DataAuthorization.enterprise_id == c.enterprise_id, DataAuthorization.status == "active")).all()
    for a in rows:
        if a.policy_id not in (None, c.policy_id):
            continue
        if a.valid_to and a.valid_to < now:
            continue
        if c.kind in (a.scopes or []):
            return True
    return False


def _map_point(c: Connector, raw: dict, assets: set[str]) -> tuple[str, str, datetime, float]:
    metric_in = str(raw.get("metric") or "")
    metric = (c.field_map or {}).get(metric_in, metric_in)
    if metric not in METRICS[c.kind]:
        raise ConnectorError(f"指标无法映射：{metric_in or metric}")
    if c.kind == "iot":
        asset_in = str(raw.get("asset") or "")
        asset = (c.asset_map or {}).get(asset_in, asset_in)
        if asset not in assets:
            raise ConnectorError(f"设备编号不在监测方案中：{asset_in or asset}")
    else:
        asset = FIXED_ASSET[c.kind]
    try:
        value = float(raw.get("value"))
    except (TypeError, ValueError) as exc:
        raise ConnectorError("数值不是有限数字") from exc
    if not math.isfinite(value):
        raise ConnectorError("数值不是有限数字")
    return asset, metric, _parse_ts(raw.get("ts")), value


def _write_points(s, c: Connector, points: list[dict]) -> dict:
    if c.status == "suspended":
        raise ConnectorError("连接器已暂停", 403)
    if not _authorized(s, c):
        raise ConnectorError("数据接入授权无效", 403)
    assets = _plan_assets(s, c.policy_id) if c.kind == "iot" else set()
    accepted = 0
    errors: list[dict] = []
    for i, raw in enumerate(points):
        try:
            asset, metric, ts, value = _map_point(c, raw, assets)
        except ConnectorError as exc:
            errors.append({"index": i, "message": str(exc)})
            continue
        row = s.scalars(
            select(TelemetryPoint).where(
                TelemetryPoint.policy_id == c.policy_id,
                TelemetryPoint.source == c.kind,
                TelemetryPoint.asset == asset,
                TelemetryPoint.metric == metric,
                TelemetryPoint.ts == ts,
            )
        ).first()
        if row is None:
            s.add(TelemetryPoint(policy_id=c.policy_id, source=c.kind, asset=asset, metric=metric, ts=ts, value=value))
        else:
            row.value = value
        accepted += 1
    if accepted == 0:
        raise ConnectorError(errors[0]["message"] if errors else "没有可写入的数据点")
    c.last_error = ""
    return {"accepted": accepted, "errors": errors}


def ingest(connector_id: int, token: str, source_ip: str, points: list[dict]) -> dict:
    with session_scope() as s:
        c = s.get(Connector, connector_id)
        if c is None:
            raise ConnectorError("连接器不存在", 404)
        if c.mode != "live" or (c.direction or "push") != "push":
            raise ConnectorError("连接器不是实接推送", 409)
        if not token or not c.token_hash or not secrets.compare_digest(hash_token(token), c.token_hash):
            raise ConnectorError("推送凭证不正确", 401)
        allow = [x for x in (c.allow_ips or []) if x]
        if allow and source_ip not in allow:
            raise ConnectorError("来源地址不在允许列表中", 403)
        return _write_points(s, c, points)


def rejected_host(host: str) -> str | None:
    name = (host or "").strip().lower().strip("[]")
    if not name:
        return "缺少主机"
    if name in {"localhost", "localhost.localdomain"}:
        return "本机地址"
    try:
        ips = [ipaddress.ip_address(name)]
    except ValueError:
        try:
            infos = socket.getaddrinfo(name, None)
        except socket.gaierror:
            return "无法解析主机"
        ips = []
        for info in infos:
            try:
                ips.append(ipaddress.ip_address(info[4][0]))
            except ValueError:
                continue
        if not ips:
            return "无法解析主机"
    for ip in ips:
        if ip.is_loopback or ip.is_link_local:
            return "本机或链路本地地址"
    return None


def pull_url(c: Connector) -> str:
    path = c.path or "/"
    if not path.startswith("/"):
        path = "/" + path
    return f"https://{c.host}:{int(c.port or 443)}{path}"


def _fetch(url: str, secret: str) -> dict:
    headers = {"Authorization": f"Bearer {secret}"} if secret else {}
    with httpx.Client(timeout=15, verify=False, follow_redirects=False) as client:
        res = client.get(url, headers=headers)
        res.raise_for_status()
        data = res.json()
    if not isinstance(data, dict) or not isinstance(data.get("points"), list):
        raise ConnectorError("返回不是包含 points 的 JSON")
    return data


def pull_one(connector_id: int) -> dict:
    with session_scope() as s:
        c = s.get(Connector, connector_id)
        if c is None:
            raise ConnectorError("连接器不存在", 404)
        if c.mode != "live" or c.direction != "pull" or c.kind != "ops":
            raise ConnectorError("该连接器不是运营通道的实接拉取", 409)
        reason = rejected_host(c.host)
        if reason:
            c.last_error = reason
            _stamp_pull(c)
            return {"ok": False, "error": reason}
        try:
            secret = decrypt_secret(c.pull_secret)
            data = _fetch(pull_url(c), secret)
            out = _write_points(s, c, data["points"])
        except (ConnectorError, httpx.HTTPError, ValueError) as exc:
            c.last_error = str(exc)
            _stamp_pull(c)
            return {"ok": False, "error": str(exc)}
        _stamp_pull(c)
        return {"ok": True, **out}


def _stamp_pull(c: Connector) -> None:
    quality = dict(c.quality or {})
    quality["last_pull_at"] = utcnow().isoformat()
    c.quality = quality


def pull_due() -> list[dict]:
    now = utcnow()
    with session_scope() as s:
        rows = s.scalars(select(Connector).where(Connector.mode == "live", Connector.direction == "pull", Connector.kind == "ops")).all()
        due = []
        for c in rows:
            last_raw = (c.quality or {}).get("last_pull_at")
            if last_raw:
                try:
                    last = datetime.fromisoformat(last_raw)
                except ValueError:
                    last = None
            else:
                last = None
            interval = timedelta(minutes=c.pull_interval_minutes or 60)
            if last is None or now - last >= interval:
                due.append(c.id)
    return [pull_one(cid) | {"id": cid} for cid in due]


def _require_pull(kind: str, direction: str, host: str, port: int, path: str, has_secret: bool) -> None:
    if direction != "pull":
        return
    if kind != "ops":
        raise ConnectorError("拉取只用于研发与运营通道")
    if not host or not path or not port or not has_secret:
        raise ConnectorError("拉取需要主机、端口、路径和凭证")
    if port < 1 or port > 65535:
        raise ConnectorError("端口不正确")
    reason = rejected_host(host)
    if reason:
        raise ConnectorError(reason)


def configure(connector_id: int, patch: dict) -> dict:
    with session_scope() as s:
        c = s.get(Connector, connector_id)
        if c is None:
            raise ConnectorError("连接器不存在", 404)
        mode = patch.get("mode", c.mode or "sim")
        direction = patch.get("direction", c.direction or "push")
        if mode not in ("sim", "live") or direction not in ("push", "pull"):
            raise ConnectorError("接入方式不正确")
        host = patch["host"].strip() if "host" in patch and patch["host"] is not None else (c.host or "")
        port = int(patch["port"]) if patch.get("port") not in (None, "") else int(c.port or 443)
        path = patch["path"].strip() if "path" in patch and patch["path"] is not None else (c.path or "")
        secret = (patch.get("secret") or "").strip()
        has_secret = bool(secret or c.pull_secret)
        if direction == "pull":
            _require_pull(c.kind, direction, host, port, path, has_secret)
        c.mode = mode
        c.direction = direction
        c.host = host
        c.port = port
        c.path = path
        if "allow_ips" in patch and patch["allow_ips"] is not None:
            c.allow_ips = [str(x).strip() for x in patch["allow_ips"] if str(x).strip()]
        if "field_map" in patch and patch["field_map"] is not None:
            c.field_map = {str(k): str(v) for k, v in patch["field_map"].items()}
        if "asset_map" in patch and patch["asset_map"] is not None:
            c.asset_map = {str(k): str(v) for k, v in patch["asset_map"].items()}
        if patch.get("pull_interval_minutes"):
            c.pull_interval_minutes = max(1, int(patch["pull_interval_minutes"]))
        if secret:
            c.pull_secret = encrypt_secret(secret)
        c.configured = True
        c.last_error = ""
        if mode == "live" and direction == "pull":
            c.endpoint = pull_url(c)
        elif mode == "live" and direction == "push":
            c.endpoint = ingest_url(c.id)
        view = public_view(c)
    from .ops import sync_connectors

    sync_connectors()
    return view


def rotate_token(connector_id: int) -> dict:
    token = secrets.token_urlsafe(32)
    with session_scope() as s:
        c = s.get(Connector, connector_id)
        if c is None:
            raise ConnectorError("连接器不存在", 404)
        c.token_hash = hash_token(token)
        c.configured = True
        if c.mode == "live" and (c.direction or "push") == "push":
            c.endpoint = ingest_url(c.id)
    return {"token": token, "ingest_url": ingest_url(connector_id)}


def live_kinds(policy_id: int) -> set[str]:
    with session_scope() as s:
        return {c.kind for c in s.scalars(select(Connector).where(Connector.policy_id == policy_id, Connector.mode == "live")).all()}
