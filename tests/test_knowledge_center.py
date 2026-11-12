"""知识中心：正文抽取、入库质检、统计、分类字段、提案发布后可检索。使用独立数据目录。"""

from __future__ import annotations

import io
import os
import tempfile

os.environ["TECHINS_DATA_DIR"] = tempfile.mkdtemp(prefix="techins-knowledge-")
os.environ["TECHINS_DISABLE_LLM"] = "1"
os.environ["TECHINS_MONITOR_ENABLED"] = "0"
os.environ["TECHINS_DEMO_BOOTSTRAP"] = "0"

import docx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from techins.api.app import app
from techins.db import session_scope
from techins.extract import MAX_UPLOAD_BYTES, extract_text
from techins.knowledge.index import record_citations
from techins.models import IngestBatch, KnowledgeCitation, KnowledgeDoc


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _login(client: TestClient, username: str) -> dict:
    res = client.post("/api/auth/login", json={"username": username, "password": "demo1234"})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


def _pdf(text: str) -> bytes:
    content = f"BT /F1 12 Tf 50 100 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return bytes(out)


def _docx(text: str) -> bytes:
    document = docx.Document()
    document.add_paragraph(text)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def _batch_count() -> int:
    with session_scope() as s:
        return s.scalar(select(func.count(IngestBatch.id))) or 0


def test_extract_pdf_and_docx_directly_and_via_material_upload(client: TestClient):
    pdf_text = extract_text("sample.pdf", _pdf("LabFireEquipmentLoss"))
    assert pdf_text and "LabFireEquipmentLoss" in pdf_text
    word = "实验室火灾导致研发设备物质损失，关键检测设备需要修复。"
    assert word in (extract_text("sample.docx", _docx(word)) or "")
    assert extract_text("photo.png", b"not-a-document") is None

    headers = _login(client, "admin")
    enterprises = client.get("/api/enterprises", headers=headers)
    assert enterprises.status_code == 200 and enterprises.json()
    eid = enterprises.json()[0]["id"]
    uploaded = client.post(
        "/api/materials",
        headers=headers,
        data={"owner_type": "enterprise", "owner_id": str(eid), "enterprise_id": str(eid), "material_type": "其他材料"},
        files={"file": ("lab.docx", _docx(word), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert uploaded.status_code == 200, uploaded.text
    detail = client.get(f"/api/materials/{uploaded.json()['id']}", headers=headers)
    assert word in (detail.json().get("text") or "")
    pdf_upload = client.post(
        "/api/materials",
        headers=headers,
        data={"owner_type": "enterprise", "owner_id": str(eid), "enterprise_id": str(eid), "material_type": "其他材料"},
        files={"file": ("lab.pdf", _pdf("LabFireEquipmentLoss"), "application/pdf")},
    )
    assert pdf_upload.status_code == 200, pdf_upload.text
    pdf_detail = client.get(f"/api/materials/{pdf_upload.json()['id']}", headers=headers)
    assert "LabFireEquipmentLoss" in (pdf_detail.json().get("text") or "")


def test_ingest_text_creates_batch_and_rejects_bad_files(client: TestClient):
    admin = _login(client, "admin")
    designer = _login(client, "designer")
    before = _batch_count()
    denied = client.post("/api/knowledge/ingest", headers=designer, data={"kb": "K04", "source": "设计岗"}, files={"files": ("note.txt", "设计人员不能入库的一段足够长的说明文字。".encode(), "text/plain")})
    assert denied.status_code == 403
    assert _batch_count() == before

    bad = client.post("/api/knowledge/ingest", headers=admin, data={"kb": "K04", "source": "手工"}, files={"files": ("photo.png", b"\x89PNG\r\n", "image/png")})
    assert bad.status_code == 422
    assert "不支持" in bad.json()["error"]["message"]
    assert _batch_count() == before

    short = client.post("/api/knowledge/ingest", headers=admin, data={"kb": "K04", "source": "手工"}, files={"files": ("short.txt", "太短".encode(), "text/plain")})
    assert short.status_code == 409
    assert _batch_count() == before

    huge = client.post("/api/knowledge/ingest", headers=admin, data={"kb": "K04", "source": "手工"}, files={"files": ("big.txt", b"a" * (MAX_UPLOAD_BYTES + 1), "text/plain")})
    assert huge.status_code == 413
    assert _batch_count() == before

    ok = client.post(
        "/api/knowledge/ingest",
        headers=admin,
        data={"kb": "K04", "source": "手工导入"},
        files={"files": ("risk.txt", "青岚轴承过热停机时，研发设备因火灾或机器损坏造成物质损失，属于可保的设备风险。".encode(), "text/plain")},
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["status"] == "qc"
    assert body["sample_ids"]
    assert _batch_count() == before + 1


def test_publish_is_searchable_and_return_is_not(client: TestClient):
    admin = _login(client, "admin")
    created = client.post(
        "/api/knowledge/ingest",
        headers=admin,
        data={"kb": "K04", "source": "发布测试"},
        files={"files": ("keep.txt", "青岚轴承过热停机说明：研发实验室轴承过热后停机，设备火灾会造成研发中断。".encode(), "text/plain")},
    )
    assert created.status_code == 200, created.text
    batch = created.json()
    for idx in batch["sample_ids"]:
        reviewed = client.post(f"/api/knowledge/batches/{batch['id']}/items/{idx}/qc", headers=admin, json={"verdict": "pass", "comment": "抽检通过"})
        assert reviewed.status_code == 200, reviewed.text
    early = client.post(
        "/api/knowledge/ingest",
        headers=admin,
        data={"kb": "K04", "source": "未检"},
        files={"files": ("early.txt", "未完成质检就发布的青岚对照文档，正文足够长，用于确认发布会被拒绝。".encode(), "text/plain")},
    )
    assert early.status_code == 200
    refused = client.post(f"/api/knowledge/batches/{early.json()['id']}/publish", headers=admin)
    assert refused.status_code == 409
    assert "未质检" in refused.json()["error"]["message"]

    published = client.post(f"/api/knowledge/batches/{batch['id']}/publish", headers=admin)
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "published"
    found = client.get("/api/knowledge/search", headers=admin, params={"q": "青岚轴承过热停机"})
    assert found.status_code == 200
    assert any("青岚" in hit["title"] or "青岚" in hit["text"] for hit in found.json())

    returned = client.post(
        "/api/knowledge/ingest",
        headers=admin,
        data={"kb": "K04", "source": "退回测试"},
        files={"files": ("drop.txt", "紫苏阀门不应出现在知识库中，这段说明只用于退回批次，不进入检索。".encode(), "text/plain")},
    )
    assert returned.status_code == 200
    back = client.post(f"/api/knowledge/batches/{returned.json()['id']}/return", headers=admin, json={"reason": "内容不采用"})
    assert back.status_code == 200
    assert back.json()["status"] == "returned"
    missed = client.get("/api/knowledge/search", headers=admin, params={"q": "紫苏阀门"})
    assert all("紫苏阀门" not in hit["title"] and "紫苏阀门" not in hit["text"] for hit in missed.json())

    sensitive = client.post(
        "/api/knowledge/ingest",
        headers=admin,
        data={"kb": "K04", "source": "敏感"},
        files={"files": ("phone.txt", "紫电敏感电话 13800138000 不应进入公域风险知识库，这段正文足够长。".encode(), "text/plain")},
    )
    assert sensitive.status_code == 200, sensitive.text
    sensitive_batch = sensitive.json()
    assert any("不允许入库" in issue for item in sensitive_batch["items"] for issue in item["issues"])
    for idx in sensitive_batch["sample_ids"]:
        assert client.post(f"/api/knowledge/batches/{sensitive_batch['id']}/items/{idx}/qc", headers=admin, json={"verdict": "pass", "comment": "确认敏感"}).status_code == 200
    client.post(f"/api/knowledge/batches/{sensitive_batch['id']}/publish", headers=admin)
    with session_scope() as s:
        leaked = s.scalars(select(KnowledgeDoc).where(KnowledgeDoc.title.contains("紫电敏感"))).first()
    assert leaked is None


def test_citation_rows_and_library_stats(client: TestClient):
    headers = _login(client, "designer")
    stats = client.get("/api/knowledge/stats", headers=headers)
    assert stats.status_code == 200, stats.text
    by_kb = {row["kb"]: row for row in stats.json()["kbs"]}
    assert by_kb["K04"]["domain"] == "public" and by_kb["K04"]["domain_label"] == "公域"
    assert by_kb["K08"]["domain"] == "private" and by_kb["K08"]["domain_label"] == "私域"
    for row in by_kb.values():
        for key in ("docs", "chunks", "updated_at", "parse_rate", "annotation_rate", "inactive_rate", "citations_30d", "citations"):
            assert key in row
    before = by_kb["K04"]["citations_30d"]
    with session_scope() as s:
        doc = s.scalars(select(KnowledgeDoc).where(KnowledgeDoc.kb == "K04")).first()
        doc_id, cites = doc.id, doc.cite_count or 0
    record_citations([doc_id])
    with session_scope() as s:
        doc = s.get(KnowledgeDoc, doc_id)
        assert doc.cite_count == cites + 1
        assert s.scalar(select(func.count(KnowledgeCitation.id)).where(KnowledgeCitation.doc_id == doc_id)) >= 1
    again = client.get("/api/knowledge/stats", headers=headers).json()
    updated = next(row for row in again["kbs"] if row["kb"] == "K04")
    assert updated["citations_30d"] == before + 1


def test_catalog_factors_and_fraud_fields(client: TestClient):
    headers = _login(client, "designer")
    catalog = client.get("/api/catalog", headers=headers).json()
    taxonomy = catalog["risk_taxonomy"]
    subtype = taxonomy["R02"]["subtypes"]["R02-1"]
    assert subtype["name"] and subtype["insurable"]
    assert "火灾" in subtype["patterns"]
    assert subtype["stages"] and subtype["lines"] and subtype["consequence"]
    assert len(taxonomy) == 14
    assert sum(len(rt["subtypes"]) for rt in taxonomy.values()) == 65
    for code, name in (("R12", "关键人员与核心团队"), ("R13", "供应链与关键外协"), ("R14", "政策准入与合规监管")):
        assert taxonomy[code]["name"] == name
        assert taxonomy[code]["subtypes"]
    assert "R07-6" in taxonomy["R07"]["subtypes"] and "R03-3" in taxonomy["R03"]["subtypes"]
    assert taxonomy["R12"]["subtypes"]["R12-1"]["insurable"] == "yes"
    assert "RD01" in taxonomy["R12"]["subtypes"]["R12-1"]["lines"]

    factors = client.get("/api/knowledge/factors", headers=headers)
    assert factors.status_code == 200
    factor = next(item for item in factors.json() if item["key"] == "trl")
    assert factor["definition"] and factor["kind"] and factor["source"]
    assert "direction" in factor and "artifacts" in factor["usage"]
    factor_keys = {item["key"] for item in factors.json()}
    assert {"export_control_exposure", "compliance_maturity"} <= factor_keys

    fraud = client.get("/api/knowledge/fraud-patterns", headers=headers)
    assert fraud.status_code == 200 and fraud.json()
    pattern = fraud.json()[0]
    assert pattern["name"] and isinstance(pattern["rules"], list)
    assert "history_cases" in pattern and "hits" in pattern


def test_proposal_approval_becomes_searchable(client: TestClient):
    designer = _login(client, "designer")
    short = client.post(
        "/api/knowledge/proposals",
        headers=designer,
        json={"kb": "K04", "action": "add", "title": "过短提案", "content": "不足三十字", "rationale": "长度校验", "meta": {}},
    )
    assert short.status_code == 409
    assert "30" in short.json()["error"]["message"] or "不少于" in short.json()["error"]["message"]

    created = client.post(
        "/api/knowledge/proposals",
        headers=designer,
        json={
            "kb": "K04",
            "action": "add",
            "title": "霜桥实验室水浸说明",
            "content": "霜桥实验室发生水浸后，关键研发设备物质损失会导致研发中断，这段提案正文用于确认审批通过后可以检索。",
            "rationale": "补充实验室水浸案例口径",
            "meta": {},
        },
    )
    assert created.status_code == 200, created.text
    proposal = created.json()
    assert proposal["status"] == "pending" and proposal["approval_id"]
    listed = client.get("/api/knowledge/proposals", headers=designer).json()
    assert any(item["id"] == proposal["id"] and item["status"] == "pending" for item in listed)

    biz = _login(client, "bizadmin")
    approved = client.post(f"/api/approvals/{proposal['approval_id']}/act", headers=biz, json={"action": "approve", "comment": "同意入库"})
    assert approved.status_code == 200, approved.text
    found = client.get("/api/knowledge/search", headers=designer, params={"q": "霜桥实验室水浸"})
    assert any("霜桥实验室" in hit["title"] or "霜桥实验室" in hit["text"] for hit in found.json())
