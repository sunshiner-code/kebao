from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, utcnow

JSONDict = dict[str, Any]


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(32))  # designer / service / admin
    positions: Mapped[list] = mapped_column(default=list)  # 岗位权限包
    org: Mapped[str] = mapped_column(String(64), default="总公司")
    approval_level: Mapped[int] = mapped_column(Integer, default=1)
    data_scope: Mapped[str] = mapped_column(String(32), default="self")
    password_hash: Mapped[str] = mapped_column(String(256))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    org_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    email: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prefs: Mapped[dict] = mapped_column(default=dict)  # 通知偏好、首页区块
    quota: Mapped[dict] = mapped_column(default=dict)  # 个人算力与费用额度（覆盖默认）
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Enterprise(Base):
    __tablename__ = "enterprises"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    credit_code: Mapped[str] = mapped_column(String(32))
    industry: Mapped[str] = mapped_column(String(64))
    track: Mapped[str] = mapped_column(String(64))
    tech_field: Mapped[str] = mapped_column(String(128))
    region: Mapped[str] = mapped_column(String(64))
    founded_year: Mapped[int] = mapped_column(Integer)
    employees: Mapped[int] = mapped_column(Integer)
    listed: Mapped[bool] = mapped_column(Boolean, default=False)
    profile: Mapped[dict] = mapped_column(default=dict)  # 结构化画像（含私域字段，按级别标注）
    risk_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    manager_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Material(Base):
    __tablename__ = "materials"
    id: Mapped[int] = mapped_column(primary_key=True)
    enterprise_id: Mapped[int | None] = mapped_column(ForeignKey("enterprises.id"), nullable=True)
    owner_type: Mapped[str] = mapped_column(String(32), default="enterprise")  # enterprise/application/claim/project
    owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    name: Mapped[str] = mapped_column(String(256))
    material_type: Mapped[str] = mapped_column(String(64))
    data_level: Mapped[int] = mapped_column(Integer, default=2)
    detected_level: Mapped[int] = mapped_column(Integer, default=2)
    file_path: Mapped[str] = mapped_column(String(512))
    mime: Mapped[str] = mapped_column(String(128), default="text/plain")
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    parsed: Mapped[dict] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(32), default="uploaded")
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)
    prev_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class DesignProject(Base):
    __tablename__ = "design_projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    project_type: Mapped[str] = mapped_column(String(32))  # track / enterprise
    track: Mapped[str | None] = mapped_column(String(64), nullable=True)
    enterprise_ids: Mapped[list] = mapped_column(default=list)
    target_lines: Mapped[list] = mapped_column(default=list)
    status: Mapped[str] = mapped_column(String(32), default="risk_assessment")
    config: Mapped[dict] = mapped_column(default=dict)
    pipeline: Mapped[dict] = mapped_column(default=dict)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (UniqueConstraint("artifact_key", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    artifact_key: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(48))
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft / frozen
    title: Mapped[str] = mapped_column(String(256), default="")
    project_id: Mapped[int | None] = mapped_column(ForeignKey("design_projects.id"), nullable=True)
    ref_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ref_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    data: Mapped[dict] = mapped_column(default=dict)
    upstream: Mapped[list] = mapped_column(default=list)  # [{"artifact_key":..,"version":..}]
    contract_version: Mapped[str] = mapped_column(String(16), default="1.0")
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    revisions: Mapped[list] = mapped_column(default=list)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ClauseModule(Base):
    __tablename__ = "clause_modules"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(48), unique=True)
    line: Mapped[str] = mapped_column(String(64))
    module_kind: Mapped[str] = mapped_column(String(32))  # general/subject/liability/exclusion/amount/period/obligation/settlement/dispute/definition
    title: Mapped[str] = mapped_column(String(256))
    text: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column(default=dict)
    source: Mapped[str] = mapped_column(String(64), default="示范条款")
    filing_status: Mapped[str] = mapped_column(String(32), default="已备案")
    version: Mapped[int] = mapped_column(Integer, default=1)


class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(48), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    lines: Mapped[list] = mapped_column(default=list)
    stages: Mapped[list] = mapped_column(default=list)
    status: Mapped[str] = mapped_column(String(24), default="draft")
    version: Mapped[int] = mapped_column(Integer, default=1)
    start_mode: Mapped[str] = mapped_column(String(16), default="warm")
    package: Mapped[dict] = mapped_column(default=dict)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("design_projects.id"), nullable=True)
    approvals: Mapped[list] = mapped_column(default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    publish: Mapped[dict] = mapped_column(default=dict)  # 上线时间、销售区域、停售条件
    approval_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Application(Base):
    __tablename__ = "applications"
    id: Mapped[int] = mapped_column(primary_key=True)
    app_no: Mapped[str] = mapped_column(String(32), unique=True)
    enterprise_id: Mapped[int] = mapped_column(ForeignKey("enterprises.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    tier: Mapped[str] = mapped_column(String(16), default="standard")
    sum_insured: Mapped[float] = mapped_column(Float)
    period_months: Mapped[int] = mapped_column(Integer, default=12)
    declared: Mapped[dict] = mapped_column(default=dict)
    contacts: Mapped[dict] = mapped_column(default=dict)
    intermediary: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="draft")
    uw_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    report_artifact_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decision: Mapped[dict] = mapped_column(default=dict)
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class HistoricalApplication(Base):
    """承保历史库：反欺诈监督模型、关系网络与同业比较的数据来源。"""

    __tablename__ = "historical_applications"
    id: Mapped[int] = mapped_column(primary_key=True)
    enterprise_name: Mapped[str] = mapped_column(String(128))
    line: Mapped[str] = mapped_column(String(64))
    year: Mapped[int] = mapped_column(Integer)
    features: Mapped[dict] = mapped_column(default=dict)
    contacts: Mapped[dict] = mapped_column(default=dict)
    intermediary: Mapped[str | None] = mapped_column(String(64), nullable=True)
    statement: Mapped[str] = mapped_column(Text, default="")
    fraud_label: Mapped[int] = mapped_column(Integer, default=0)
    claimed: Mapped[int] = mapped_column(Integer, default=0)
    fraud_pattern: Mapped[str | None] = mapped_column(String(16), nullable=True)


class Policy(Base):
    __tablename__ = "policies"
    id: Mapped[int] = mapped_column(primary_key=True)
    policy_no: Mapped[str] = mapped_column(String(32), unique=True)
    application_id: Mapped[int | None] = mapped_column(ForeignKey("applications.id"), nullable=True)
    enterprise_id: Mapped[int] = mapped_column(ForeignKey("enterprises.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    coverage: Mapped[dict] = mapped_column(default=dict)
    premium: Mapped[float] = mapped_column(Float)
    sum_insured: Mapped[float] = mapped_column(Float)
    start_date: Mapped[datetime] = mapped_column(DateTime)
    end_date: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(24), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class MonitorPlan(Base):
    __tablename__ = "monitor_plans"
    id: Mapped[int] = mapped_column(primary_key=True)
    policy_id: Mapped[int] = mapped_column(ForeignKey("policies.id"), unique=True)
    indicators: Mapped[list] = mapped_column(default=list)
    state: Mapped[dict] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(24), default="active")
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class TelemetryPoint(Base):
    __tablename__ = "telemetry"
    id: Mapped[int] = mapped_column(primary_key=True)
    policy_id: Mapped[int] = mapped_column(ForeignKey("policies.id"), index=True)
    source: Mapped[str] = mapped_column(String(32))  # iot / log / ops
    asset: Mapped[str] = mapped_column(String(64))
    metric: Mapped[str] = mapped_column(String(64), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    value: Mapped[float] = mapped_column(Float)


class ExternalEvent(Base):
    __tablename__ = "external_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    enterprise_name: Mapped[str] = mapped_column(String(128), index=True)
    source: Mapped[str] = mapped_column(String(32))  # news / court / patent / registry / vuln / disaster / supplier
    title: Mapped[str] = mapped_column(String(256))
    content: Mapped[str] = mapped_column(Text)
    published_at: Mapped[datetime] = mapped_column(DateTime)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(primary_key=True)
    alert_no: Mapped[str] = mapped_column(String(32), unique=True)
    policy_id: Mapped[int] = mapped_column(ForeignKey("policies.id"))
    enterprise_id: Mapped[int] = mapped_column(ForeignKey("enterprises.id"))
    level: Mapped[str] = mapped_column(String(8))  # red/orange/yellow/blue
    risk_type: Mapped[str] = mapped_column(String(64))
    methods: Mapped[list] = mapped_column(default=list)
    title: Mapped[str] = mapped_column(String(256))
    evidence: Mapped[dict] = mapped_column(default=dict)
    analysis: Mapped[dict] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(24), default="new")
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    handling: Mapped[list] = mapped_column(default=list)
    sla_due: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Incident(Base):
    __tablename__ = "incidents"
    id: Mapped[int] = mapped_column(primary_key=True)
    incident_no: Mapped[str] = mapped_column(String(32), unique=True)
    policy_id: Mapped[int] = mapped_column(ForeignKey("policies.id"))
    alert_id: Mapped[int | None] = mapped_column(ForeignKey("alerts.id"), nullable=True)
    incident_type: Mapped[str] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(DateTime)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default="started")
    response: Mapped[dict] = mapped_column(default=dict)
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    claim_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ClaimCase(Base):
    __tablename__ = "claim_cases"
    id: Mapped[int] = mapped_column(primary_key=True)
    claim_no: Mapped[str] = mapped_column(String(32), unique=True)
    policy_id: Mapped[int] = mapped_column(ForeignKey("policies.id"))
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="reported")
    report: Mapped[dict] = mapped_column(default=dict)
    parties: Mapped[list] = mapped_column(default=list)
    claimed_amount: Mapped[float] = mapped_column(Float, default=0.0)
    suggested_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    approved_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_artifact_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approvals: Mapped[list] = mapped_column(default=list)
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    followups: Mapped[list] = mapped_column(default=list)  # 结案回访记录
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PriceItem(Base):
    __tablename__ = "price_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    unit: Mapped[str] = mapped_column(String(16))
    reference_price: Mapped[float] = mapped_column(Float)
    useful_life_years: Mapped[float] = mapped_column(Float, default=8)
    keywords: Mapped[list] = mapped_column(default=list)


# ----------------------------------------------------------------- 智能体运行轨迹


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(16), index=True)
    agent_version: Mapped[str] = mapped_column(String(64))
    purpose: Mapped[str] = mapped_column(String(64), default="main")
    title: Mapped[str] = mapped_column(String(256), default="")
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    mode: Mapped[str] = mapped_column(String(16), default="auto")
    start_mode: Mapped[str] = mapped_column(String(16), default="warm")
    degrade_level: Mapped[str] = mapped_column(String(4), default="D0")
    business_ref: Mapped[dict] = mapped_column(default=dict)
    input: Mapped[dict] = mapped_column(default=dict)
    output: Mapped[dict] = mapped_column(default=dict)
    budget: Mapped[dict] = mapped_column(default=dict)
    usage: Mapped[dict] = mapped_column(default=dict)
    locked_versions: Mapped[dict] = mapped_column(default=dict)
    eval_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    eval_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    reflection_rounds: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    parent_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    checkpoint: Mapped[dict] = mapped_column(default=dict)
    control: Mapped[dict] = mapped_column(default=dict)
    waiting_for: Mapped[dict] = mapped_column(default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class RunStep(Base):
    __tablename__ = "run_steps"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    step_type: Mapped[str] = mapped_column(String(24))
    title: Mapped[str] = mapped_column(String(256))
    zone: Mapped[str] = mapped_column(String(16), default="platform")
    status: Mapped[str] = mapped_column(String(16), default="running")
    todo_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[dict] = mapped_column(default=dict)
    data_level: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class RunEvent(Base):
    __tablename__ = "run_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(Integer, index=True)
    seq: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(48))
    payload: Mapped[dict] = mapped_column(default=dict)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ModelCall(Base):
    __tablename__ = "model_calls"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    step_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    task: Mapped[str] = mapped_column(String(64))
    task_type: Mapped[str] = mapped_column(String(8))
    model_id: Mapped[str] = mapped_column(String(48))
    zone: Mapped[str] = mapped_column(String(16))
    rule_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    candidates: Mapped[list] = mapped_column(default=list)
    data_level: Mapped[int] = mapped_column(Integer, default=0)
    fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    egress_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="ok")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ToolCall(Base):
    __tablename__ = "tool_calls"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(Integer, index=True)
    step_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tool: Mapped[str] = mapped_column(String(64))
    permission: Mapped[str] = mapped_column(String(4))
    zone: Mapped[str] = mapped_column(String(16))
    args_summary: Mapped[str] = mapped_column(Text, default="")
    result_summary: Mapped[str] = mapped_column(Text, default="")
    archive_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    guard: Mapped[dict] = mapped_column(default=dict)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    alternative_used: Mapped[str | None] = mapped_column(String(64), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="ok")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EgressLog(Base):
    __tablename__ = "egress_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    step_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    agent_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    task: Mapped[str] = mapped_column(String(64))
    task_type: Mapped[str] = mapped_column(String(8))
    source_level: Mapped[int] = mapped_column(Integer)
    rules_hit: Mapped[dict] = mapped_column(default=dict)
    fields: Mapped[list] = mapped_column(default=list)
    content_digest: Mapped[str] = mapped_column(String(64))
    content_preview: Mapped[str] = mapped_column(Text)
    original_preview: Mapped[str] = mapped_column(Text, default="")
    placeholder_count: Mapped[int] = mapped_column(Integer, default=0)
    scan_result: Mapped[str] = mapped_column(String(16), default="pass")
    scan_findings: Mapped[list] = mapped_column(default=list)
    target_model: Mapped[str] = mapped_column(String(48))
    approval_card_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Evaluation(Base):
    __tablename__ = "evaluations"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(16))  # stage / final / offline / posthoc
    target: Mapped[str] = mapped_column(String(128))
    rubric_version: Mapped[str] = mapped_column(String(16))
    dimensions: Mapped[list] = mapped_column(default=list)
    score: Mapped[float] = mapped_column(Float)
    passed: Mapped[bool] = mapped_column(Boolean)
    failed_dims: Mapped[list] = mapped_column(default=list)
    reviewer_model: Mapped[str | None] = mapped_column(String(48), nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Reflection(Base):
    __tablename__ = "reflections"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(Integer, index=True)
    round: Mapped[int] = mapped_column(Integer)
    trigger: Mapped[str] = mapped_column(String(32))
    diagnosis: Mapped[str] = mapped_column(Text)
    categories: Mapped[list] = mapped_column(default=list)
    strategy: Mapped[str] = mapped_column(Text)
    plan: Mapped[list] = mapped_column(default=list)
    score_before: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_after: Mapped[float | None] = mapped_column(Float, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(24), nullable=True)
    experience_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class HitlCard(Base):
    __tablename__ = "hitl_cards"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    card_type: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(256))
    reason: Mapped[str] = mapped_column(Text)
    impact: Mapped[str] = mapped_column(Text, default="")
    context: Mapped[dict] = mapped_column(default=dict)
    options: Mapped[list] = mapped_column(default=list)
    mandatory: Mapped[bool] = mapped_column(Boolean, default=True)
    default_action: Mapped[str | None] = mapped_column(String(32), nullable=True)
    deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    assignee_role: Mapped[str] = mapped_column(String(32))
    assignee_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open")
    resolved_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resolution: Mapped[dict] = mapped_column(default=dict)
    business_ref: Mapped[dict] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    history: Mapped[list] = mapped_column(default=list)  # 领取、转办记录


class MailboxMessage(Base):
    __tablename__ = "mailbox"
    id: Mapped[int] = mapped_column(primary_key=True)
    to_run_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    to_agent: Mapped[str | None] = mapped_column(String(16), nullable=True)
    from_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    from_agent: Mapped[str | None] = mapped_column(String(16), nullable=True)
    kind: Mapped[str] = mapped_column(String(32))
    request_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payload: Mapped[dict] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(16), default="unread")
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class BackgroundTask(Base):
    __tablename__ = "background_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="running")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    result_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    notified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class MemoryEntry(Base):
    __tablename__ = "memory_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)  # entity / case / experience
    agent_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    skill_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    subject_key: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    title: Mapped[str] = mapped_column(String(256))
    content: Mapped[dict] = mapped_column(default=dict)
    text: Mapped[str] = mapped_column(Text)
    conditions: Mapped[list] = mapped_column(default=list)
    status: Mapped[str] = mapped_column(String(16), default="active")  # trial / active / retiring / retired
    data_level: Mapped[int] = mapped_column(Integer, default=1)
    hits: Mapped[int] = mapped_column(Integer, default=0)
    successes: Mapped[int] = mapped_column(Integer, default=0)
    source_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class KnowledgeDoc(Base):
    __tablename__ = "knowledge_docs"
    id: Mapped[int] = mapped_column(primary_key=True)
    kb: Mapped[str] = mapped_column(String(8), index=True)
    title: Mapped[str] = mapped_column(String(256))
    source: Mapped[str] = mapped_column(String(128))
    doc_type: Mapped[str] = mapped_column(String(48))
    status: Mapped[str] = mapped_column(String(16), default="effective")
    effective_date: Mapped[str | None] = mapped_column(String(16), nullable=True)
    lines: Mapped[list] = mapped_column(default=list)
    stages: Mapped[list] = mapped_column(default=list)
    risk_types: Mapped[list] = mapped_column(default=list)
    tags: Mapped[list] = mapped_column(default=list)
    data_level: Mapped[int] = mapped_column(Integer, default=0)
    enterprise_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    quality: Mapped[str] = mapped_column(String(16), default="auto")
    cite_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    id: Mapped[int] = mapped_column(primary_key=True)
    doc_id: Mapped[int] = mapped_column(ForeignKey("knowledge_docs.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    position: Mapped[str] = mapped_column(String(64), default="")


class KnowledgeCitation(Base):
    __tablename__ = "knowledge_citations"
    id: Mapped[int] = mapped_column(primary_key=True)
    doc_id: Mapped[int] = mapped_column(ForeignKey("knowledge_docs.id"), index=True)
    cited_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ModelRegistryEntry(Base):
    __tablename__ = "model_registry"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    zone: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(32))
    tier: Mapped[str] = mapped_column(String(8))
    capabilities: Mapped[list] = mapped_column(default=list)
    max_level: Mapped[int] = mapped_column(Integer)
    base_url: Mapped[str | None] = mapped_column(String(256), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    context_window: Mapped[int] = mapped_column(Integer, default=32000)
    price_per_1k: Mapped[float] = mapped_column(Float, default=0.0)
    params: Mapped[dict] = mapped_column(default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    configured: Mapped[bool] = mapped_column(Boolean, default=False)
    health: Mapped[str] = mapped_column(String(16), default="unknown")
    error_rate: Mapped[float] = mapped_column(Float, default=0.0)
    avg_latency_ms: Mapped[float] = mapped_column(Float, default=0.0)


class RoutingRule(Base):
    __tablename__ = "routing_rules"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    scope: Mapped[str] = mapped_column(String(32), default="global")
    task_types: Mapped[list] = mapped_column(default=list)
    data_levels: Mapped[list] = mapped_column(default=list)
    preferred: Mapped[list] = mapped_column(default=list)  # 能力标签或模型编号
    fallback: Mapped[list] = mapped_column(default=list)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    max_tokens: Mapped[int] = mapped_column(Integer, default=16000)
    temperature: Mapped[float] = mapped_column(Float, default=0.2)
    params: Mapped[dict] = mapped_column(default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class AgentConfig(Base):
    __tablename__ = "agent_configs"
    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(16), index=True)
    version: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="active")
    config: Mapped[dict] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    kind: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(256))
    body: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str | None] = mapped_column(String(256), nullable=True)
    level: Mapped[str] = mapped_column(String(16), default="info")
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    channels: Mapped[list] = mapped_column(default=list)
    count: Mapped[int] = mapped_column(Integer, default=1)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    category: Mapped[str] = mapped_column(String(24))  # operation / login / data_access / egress / config
    action: Mapped[str] = mapped_column(String(64))
    object_type: Mapped[str | None] = mapped_column(String(48), nullable=True)
    object_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[dict] = mapped_column(default=dict)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DomainEvent(Base):
    __tablename__ = "domain_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    object_type: Mapped[str] = mapped_column(String(32))
    object_id: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ----------------------------------------------------------------- 运维管理：组织、权限、配置、审批


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16), default="department")  # company / branch / department
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    leader_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    data_scope: Mapped[str] = mapped_column(String(32), default="department")
    overrides: Mapped[dict] = mapped_column(default=dict)  # 机构级参数覆盖（审批阈值、通知渠道）
    sort: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PermissionPack(Base):
    __tablename__ = "permission_packs"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True)
    base_role: Mapped[str] = mapped_column(String(16))
    permissions: Mapped[list] = mapped_column(default=list)
    description: Mapped[str] = mapped_column(Text, default="")
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AccessGrant(Base):
    """临时授权：有期限、有理由、需审批，到期自动回收，期间操作打标记审计。"""

    __tablename__ = "access_grants"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    permissions: Mapped[list] = mapped_column(default=list)
    data_scope: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reason: Mapped[str] = mapped_column(Text)
    hours: Mapped[float] = mapped_column(Float, default=2)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending / active / expired / revoked / rejected
    approval_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PlatformSetting(Base):
    __tablename__ = "platform_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    category: Mapped[str] = mapped_column(String(32))
    value: Mapped[dict] = mapped_column(default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    history: Mapped[list] = mapped_column(default=list)
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    request_no: Mapped[str] = mapped_column(String(32), unique=True)
    flow_type: Mapped[str] = mapped_column(String(32), index=True)
    object_type: Mapped[str] = mapped_column(String(32))
    object_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(256))
    summary: Mapped[dict] = mapped_column(default=dict)
    stages: Mapped[list] = mapped_column(default=list)
    current_stage: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending / approved / rejected / withdrawn
    applicant_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    applicant_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    context: Mapped[dict] = mapped_column(default=dict)
    result: Mapped[dict] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ----------------------------------------------------------------- 客户与保单服务


class DataAuthorization(Base):
    __tablename__ = "data_authorizations"
    id: Mapped[int] = mapped_column(primary_key=True)
    auth_no: Mapped[str] = mapped_column(String(32), unique=True)
    enterprise_id: Mapped[int] = mapped_column(Integer, index=True)
    policy_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scopes: Mapped[list] = mapped_column(default=list)  # iot / log / ops
    items: Mapped[list] = mapped_column(default=list)
    purpose: Mapped[str] = mapped_column(Text, default="")
    grantor: Mapped[str] = mapped_column(String(64), default="")
    valid_from: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    valid_to: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active / revoked / expired
    material_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recorded_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    effects: Mapped[list] = mapped_column(default=list)  # 授权变更对监测方案的影响记录
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ContactRecord(Base):
    __tablename__ = "contact_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    enterprise_id: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(16))  # visit / call / meeting / contact
    subject: Mapped[str] = mapped_column(String(256))
    contact_name: Mapped[str] = mapped_column(String(64), default="")
    contact_title: Mapped[str] = mapped_column(String(64), default="")
    contact_phone: Mapped[str] = mapped_column(String(32), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[dict] = mapped_column(default=dict)
    transcript_status: Mapped[str] = mapped_column(String(24), default="none")  # none / pending / done / unavailable
    material_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    data_level: Mapped[int] = mapped_column(Integer, default=2)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Endorsement(Base):
    __tablename__ = "endorsements"
    id: Mapped[int] = mapped_column(primary_key=True)
    endorse_no: Mapped[str] = mapped_column(String(32), unique=True)
    policy_id: Mapped[int] = mapped_column(Integer, index=True)
    change_type: Mapped[str] = mapped_column(String(32))
    before: Mapped[dict] = mapped_column(default=dict)
    after: Mapped[dict] = mapped_column(default=dict)
    reason: Mapped[str] = mapped_column(Text, default="")
    premium_delta: Mapped[float] = mapped_column(Float, default=0.0)
    calc: Mapped[dict] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending / effective / rejected / withdrawn
    approval_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    effective_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Renewal(Base):
    __tablename__ = "renewals"
    id: Mapped[int] = mapped_column(primary_key=True)
    policy_id: Mapped[int] = mapped_column(Integer, unique=True)
    status: Mapped[str] = mapped_column(String(16), default="reminded")  # reminded / assessing / quoted / accepted / declined / issued
    due_date: Mapped[datetime] = mapped_column(DateTime)
    experience: Mapped[dict] = mapped_column(default=dict)
    application_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uw_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quote: Mapped[dict] = mapped_column(default=dict)
    new_policy_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[list] = mapped_column(default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class PolicyDocument(Base):
    __tablename__ = "policy_documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    doc_no: Mapped[str] = mapped_column(String(40), unique=True)
    policy_id: Mapped[int] = mapped_column(Integer, index=True)
    doc_type: Mapped[str] = mapped_column(String(24))  # policy / endorsement / invoice / settlement / renewal_notice
    ref_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(256))
    html: Mapped[str] = mapped_column(Text)
    digest: Mapped[str] = mapped_column(String(64), default="")
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ProductVersion(Base):
    __tablename__ = "product_versions"
    __table_args__ = (UniqueConstraint("product_id", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(Integer, index=True)
    version: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(128))
    lines: Mapped[list] = mapped_column(default=list)
    package: Mapped[dict] = mapped_column(default=dict)
    publish: Mapped[dict] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(24), default="draft")
    change_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ----------------------------------------------------------------- 知识治理


class KnowledgeProposal(Base):
    __tablename__ = "knowledge_proposals"
    id: Mapped[int] = mapped_column(primary_key=True)
    proposal_no: Mapped[str] = mapped_column(String(32), unique=True)
    kb: Mapped[str] = mapped_column(String(8))
    action: Mapped[str] = mapped_column(String(16))  # add / modify / retire
    doc_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(256))
    content: Mapped[str] = mapped_column(Text, default="")
    meta: Mapped[dict] = mapped_column(default=dict)
    rationale: Mapped[str] = mapped_column(Text, default="")
    impact: Mapped[dict] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    approval_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    submitted_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class IngestBatch(Base):
    __tablename__ = "ingest_batches"
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_no: Mapped[str] = mapped_column(String(32), unique=True)
    kb: Mapped[str] = mapped_column(String(8))
    source: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="qc")  # qc / published / returned
    items: Mapped[list] = mapped_column(default=list)  # 待入库文档（解析与自动标注结果）
    sample_ids: Mapped[list] = mapped_column(default=list)
    qc: Mapped[dict] = mapped_column(default=dict)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ----------------------------------------------------------------- 评测与自进化


class EvalSet(Base):
    __tablename__ = "eval_sets"
    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(16), index=True)
    purpose: Mapped[str] = mapped_column(String(32), default="main")
    name: Mapped[str] = mapped_column(String(128))
    split: Mapped[str] = mapped_column(String(16), default="dev")  # dev / holdout
    version: Mapped[int] = mapped_column(Integer, default=1)
    samples: Mapped[list] = mapped_column(default=list)
    description: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class EvalJob(Base):
    __tablename__ = "eval_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    eval_set_id: Mapped[int] = mapped_column(Integer, index=True)
    eval_set_version: Mapped[int] = mapped_column(Integer, default=1)
    agent_id: Mapped[str] = mapped_column(String(16))
    purpose: Mapped[str] = mapped_column(String(32), default="main")
    label: Mapped[str] = mapped_column(String(128), default="")
    config_version: Mapped[str] = mapped_column(String(32), default="")
    candidate_config: Mapped[dict] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    results: Mapped[list] = mapped_column(default=list)
    metrics: Mapped[dict] = mapped_column(default=dict)
    baseline_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    proposal_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class EvolutionProposal(Base):
    __tablename__ = "evolution_proposals"
    id: Mapped[int] = mapped_column(primary_key=True)
    proposal_no: Mapped[str] = mapped_column(String(32), unique=True)
    agent_id: Mapped[str] = mapped_column(String(16), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # config / experience / rubric
    title: Mapped[str] = mapped_column(String(256))
    rationale: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[dict] = mapped_column(default=dict)
    change: Mapped[dict] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    eval_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    baseline_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    eval_summary: Mapped[dict] = mapped_column(default=dict)
    approval_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    canary: Mapped[dict] = mapped_column(default=dict)
    released_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    previous_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    history: Mapped[list] = mapped_column(default=list)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


# ----------------------------------------------------------------- 协作、集成、调度与报表


class CopilotSession(Base):
    __tablename__ = "copilot_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    context: Mapped[dict] = mapped_column(default=dict)
    messages: Mapped[list] = mapped_column(default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class DataSource(Base):
    __tablename__ = "data_sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(32))
    provider: Mapped[str] = mapped_column(String(64), default="演示数据集")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    health: Mapped[str] = mapped_column(String(16), default="unknown")
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    calls: Mapped[int] = mapped_column(Integer, default=0)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    failure_rate: Mapped[float] = mapped_column(Float, default=0.0)
    daily_quota: Mapped[int] = mapped_column(Integer, default=5000)
    unit_cost: Mapped[float] = mapped_column(Float, default=0.0)
    used_today: Mapped[int] = mapped_column(Integer, default=0)
    usage_date: Mapped[str] = mapped_column(String(10), default="")
    field_mapping: Mapped[dict] = mapped_column(default=dict)
    probe_history: Mapped[list] = mapped_column(default=list)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Connector(Base):
    __tablename__ = "connectors"
    id: Mapped[int] = mapped_column(primary_key=True)
    enterprise_id: Mapped[int] = mapped_column(Integer, index=True)
    policy_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String(16))  # iot / log / ops
    name: Mapped[str] = mapped_column(String(128))
    endpoint: Mapped[str] = mapped_column(String(256), default="")
    status: Mapped[str] = mapped_column(String(16), default="online")  # online / degraded / offline / suspended
    authorization_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    quality: Mapped[dict] = mapped_column(default=dict)
    mode: Mapped[str] = mapped_column(String(8), default="sim")  # sim / live
    direction: Mapped[str] = mapped_column(String(8), default="push")  # push / pull
    host: Mapped[str] = mapped_column(String(256), default="")
    port: Mapped[int] = mapped_column(Integer, default=443)
    path: Mapped[str] = mapped_column(String(256), default="")
    allow_ips: Mapped[list] = mapped_column(default=list)
    field_map: Mapped[dict] = mapped_column(default=dict)
    asset_map: Mapped[dict] = mapped_column(default=dict)
    pull_interval_minutes: Mapped[int] = mapped_column(Integer, default=60)
    pull_secret: Mapped[str] = mapped_column(Text, default="")
    token_hash: Mapped[str] = mapped_column(String(128), default="")
    configured: Mapped[bool] = mapped_column(Boolean, default=False)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ScheduledJob(Base):
    __tablename__ = "scheduled_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(48), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    job_type: Mapped[str] = mapped_column(String(48))
    interval_minutes: Mapped[int] = mapped_column(Integer, default=1440)
    params: Mapped[dict] = mapped_column(default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    max_retries: Mapped[int] = mapped_column(Integer, default=2)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class JobRun(Base):
    __tablename__ = "job_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(Integer, index=True)
    trigger: Mapped[str] = mapped_column(String(16), default="schedule")
    status: Mapped[str] = mapped_column(String(16), default="running")
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    output: Mapped[dict] = mapped_column(default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ReportDefinition(Base):
    __tablename__ = "report_definitions"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    owner_id: Mapped[int] = mapped_column(Integer)
    metrics: Mapped[list] = mapped_column(default=list)
    dimension: Mapped[str] = mapped_column(String(32), default="month")
    period_days: Mapped[int] = mapped_column(Integer, default=180)
    chart: Mapped[str] = mapped_column(String(16), default="bar")
    filters: Mapped[dict] = mapped_column(default=dict)
    subscribers: Mapped[list] = mapped_column(default=list)
    schedule: Mapped[str] = mapped_column(String(16), default="none")  # none / daily / weekly / monthly
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
