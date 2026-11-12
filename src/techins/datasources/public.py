"""外部公开数据源适配器。

PublicDataSource 定义了专利、新闻、财报、工商、司法、行业报告、政策七类接口。默认实现读取本地演示数据集
与平台外部事件表；接入商业数据源时实现同名方法并在 get_source() 中替换即可。
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from sqlalchemy import select

from ..db import session_scope
from ..knowledge.index import knowledge_index
from ..models import Enterprise, ExternalEvent
from ..seed.enterprises import ENTERPRISES, INDUSTRY_REPORTS, PUBLIC_DATA


class PublicDataSource(Protocol):
    name: str

    def patents(self, enterprise: str) -> list[dict]: ...

    def news(self, enterprise: str, since: str | None = None) -> list[dict]: ...

    def financial_report(self, enterprise: str) -> dict | None: ...

    def registry(self, enterprise: str) -> dict | None: ...

    def court(self, enterprise: str) -> list[dict]: ...

    def industry_report(self, track: str) -> dict | None: ...

    def policies(self, query: str) -> list[dict]: ...


class DemoDatasetSource:
    name = "演示数据集"

    def _ent(self, enterprise: str) -> dict | None:
        return next((e for e in ENTERPRISES if e["name"] == enterprise), None)

    def patents(self, enterprise: str) -> list[dict]:
        return [dict(p, source_ref=f"patent:{p['no']}") for p in PUBLIC_DATA.get(enterprise, {}).get("patents", [])]

    def news(self, enterprise: str, since: str | None = None) -> list[dict]:
        items = [dict(n, source_ref=f"news:{enterprise}:{i}", channel="新闻") for i, n in enumerate(PUBLIC_DATA.get(enterprise, {}).get("news", []))]
        with session_scope() as s:
            events = s.scalars(select(ExternalEvent).where(ExternalEvent.enterprise_name == enterprise, ExternalEvent.source == "news")).all()
            items += [{"date": e.published_at.strftime("%Y-%m-%d"), "title": e.title, "text": e.content, "source_ref": f"event:{e.id}", "channel": "舆情订阅"} for e in events]
        if since:
            items = [n for n in items if n["date"] >= since]
        return sorted(items, key=lambda n: n["date"])

    def financial_report(self, enterprise: str) -> dict | None:
        text = PUBLIC_DATA.get(enterprise, {}).get("financial_report")
        ent = self._ent(enterprise)
        if not text or not ent or not ent.get("listed"):
            return None
        return {"text": text, "source_ref": f"report:{enterprise}:2025", "period": "2025年度"}

    def registry(self, enterprise: str) -> dict | None:
        ent = self._ent(enterprise)
        if ent is None:
            with session_scope() as s:
                row = s.scalars(select(Enterprise).where(Enterprise.name == enterprise)).first()
                if row is None:
                    return None
                return {"name": row.name, "credit_code": row.credit_code, "founded_year": row.founded_year, "region": row.region, "industry": row.industry, "source_ref": f"registry:{row.credit_code}"}
        return {
            "name": ent["name"],
            "credit_code": ent["credit_code"],
            "founded_year": ent["founded_year"],
            "region": ent["region"],
            "industry": ent["industry"],
            "legal_rep": ent["legal_rep"],
            "registered_address": ent["address"],
            "listed": ent["listed"],
            "source_ref": f"registry:{ent['credit_code']}",
        }

    def court(self, enterprise: str) -> list[dict]:
        out = []
        for p in PUBLIC_DATA.get(enterprise, {}).get("patents", []):
            for ev in p.get("events", []):
                out.append({"date": ev[:10], "title": f"专利 {p['no']} 法律事件", "text": ev[11:], "source_ref": f"court:{p['no']}"})
        for i, n in enumerate(PUBLIC_DATA.get(enterprise, {}).get("news", [])):
            if any(k in n["text"] for k in ("起诉", "诉讼", "审理", "337调查")):
                out.append({"date": n["date"], "title": n["title"], "text": n["text"], "source_ref": f"news:{enterprise}:{i}"})
        with session_scope() as s:
            events = s.scalars(select(ExternalEvent).where(ExternalEvent.enterprise_name == enterprise, ExternalEvent.source == "court")).all()
            out += [{"date": e.published_at.strftime("%Y-%m-%d"), "title": e.title, "text": e.content, "source_ref": f"event:{e.id}"} for e in events]
        return out

    def industry_report(self, track: str) -> dict | None:
        text = INDUSTRY_REPORTS.get(track)
        return {"text": text, "source_ref": f"industry:{track}", "title": f"{track}行业研究报告（2025）"} if text else None

    def policies(self, query: str) -> list[dict]:
        hits = knowledge_index.search(query, kbs=["K02"], top_k=4)
        return [{"title": h["title"], "text": h["text"], "source_ref": f"kb:{h['doc_id']}", "doc_id": h["doc_id"]} for h in hits]

    def tax_social_security(self, enterprise: str) -> dict | None:
        """纳税与社保规模核验（需企业授权的数据源）。"""
        ent = self._ent(enterprise)
        if ent is None:
            return None
        f = ent["financials"]
        return {"tax_paid": f["tax_paid"], "social_security_count": f["social_security_count"], "revenue_band": f["revenue"], "as_of": datetime(2025, 12, 31).date().isoformat()}


_source: PublicDataSource = DemoDatasetSource()


def get_source() -> DemoDatasetSource:
    return _source  # type: ignore[return-value]
