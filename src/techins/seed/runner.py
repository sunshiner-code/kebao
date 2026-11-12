"""演示环境初始化：用户、企业与私有材料、知识库、模型注册、价格库、承保历史、经验数据。"""

from __future__ import annotations

import hashlib
import os

from sqlalchemy import func, select

from ..config import settings
from ..db import Base, engine, session_scope
from ..knowledge.seed import seed_knowledge
from ..llm.registry import seed_registry
from ..models import Enterprise, HistoricalApplication, Material, PriceItem, User
from ..privacy.classifier import known_entities
from .datasets import PRICE_ITEMS, generate_all_experience, generate_historical_applications
from .enterprises import ENTERPRISES
from .materials import dd_report, interview_notes

DEMO_USERS = [
    ("designer", "张明远", "designer", ["产品经理", "条款岗"], 1, "department"),
    ("actuary", "陈语微", "designer", ["精算岗", "产品负责人"], 2, "department"),
    ("service", "李若彤", "service", ["核保岗", "预警岗", "理赔岗", "客户经理"], 1, "customers"),
    ("senior", "周景行", "service", ["核保岗", "理赔岗"], 2, "department"),
    ("admin", "王立衡", "admin", ["系统管理员"], 3, "all"),
    ("bizadmin", "赵恺文", "admin", ["业务管理员"], 3, "all"),
]


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or os.urandom(8).hex()
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000).hex()
    return f"{salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    salt, _ = stored.split("$", 1)
    return hash_password(password, salt) == stored


def seed_users() -> None:
    with session_scope() as s:
        if s.scalar(select(func.count(User.id))):
            return
        for username, name, role, positions, level, scope in DEMO_USERS:
            s.add(User(username=username, display_name=name, role=role, positions=positions, approval_level=level, data_scope=scope, password_hash=hash_password("demo1234")))


def seed_enterprises() -> None:
    with session_scope() as s:
        if s.scalar(select(func.count(Enterprise.id))):
            return
        manager = s.scalars(select(User).where(User.username == "service")).first()
        for i, ent in enumerate(ENTERPRISES):
            profile = {k: ent[k] for k in ("legal_rep", "phone", "address", "financials", "products", "equipment", "suppliers", "team", "security", "milestones")}
            row = Enterprise(
                name=ent["name"],
                credit_code=ent["credit_code"],
                industry=ent["industry"],
                track=ent["track"],
                tech_field=ent["tech_field"],
                region=ent["region"],
                founded_year=ent["founded_year"],
                employees=ent["employees"],
                listed=ent["listed"],
                profile=profile,
                manager_id=manager.id if manager else None,
            )
            s.add(row)
            s.flush()
            folder = settings.materials_dir / f"ent-{row.id}"
            folder.mkdir(parents=True, exist_ok=True)
            for kind, title, text in (("尽调报告", f"{ent['name']}尽职调查报告.txt", dd_report(ent, 100 + i)), ("访谈纪要", f"{ent['name']}管理层访谈纪要.txt", interview_notes(ent, 100 + i))):
                path = folder / title
                path.write_text(text, encoding="utf-8")
                s.add(Material(enterprise_id=row.id, owner_type="enterprise", owner_id=row.id, name=title, material_type=kind, data_level=2, detected_level=3, file_path=str(path), mime="text/plain", text=text, status="parsed", parsed={"chars": len(text), "paragraphs": text.count("\n") + 1}))


def seed_reference_data() -> None:
    with session_scope() as s:
        if not s.scalar(select(func.count(PriceItem.id))):
            for cat, name, unit, price, life, kws in PRICE_ITEMS:
                s.add(PriceItem(category=cat, name=name, unit=unit, reference_price=price, useful_life_years=life, keywords=kws))
        if not s.scalar(select(func.count(HistoricalApplication.id))):
            for row in generate_historical_applications():
                s.add(HistoricalApplication(**row))
    generate_all_experience()


def load_known_entities() -> None:
    with session_scope() as s:
        for e in s.scalars(select(Enterprise)).all():
            known_entities.add_company(e.name)
            p = e.profile or {}
            if p.get("legal_rep"):
                known_entities.add_person(p["legal_rep"])
            for m in (p.get("team") or {}).get("core_roles", []):
                known_entities.add_person(m["name"])
            for sup in p.get("suppliers", []):
                if sup["name"].endswith("公司"):
                    known_entities.add_company(sup["name"])


def seed_all() -> None:
    from ..db import auto_migrate
    from ..services.seed_ops import seed_operations

    Base.metadata.create_all(engine)
    auto_migrate()
    seed_users()
    seed_operations()
    seed_registry()
    seed_knowledge()
    seed_enterprises()
    seed_reference_data()
    load_known_entities()
