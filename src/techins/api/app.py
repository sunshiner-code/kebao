"""FastAPI 应用：启动时初始化数据、注册六个智能体、启动运行执行器与常驻监测调度。"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..config import settings

log = logging.getLogger("techins.api")
_bootstrap_state = {"status": "idle", "detail": None}


def _bootstrap_in_background() -> None:
    from ..seed.demo import bootstrap_demo

    _bootstrap_state["status"] = "running"
    try:
        _bootstrap_state["detail"] = bootstrap_demo()
        _bootstrap_state["status"] = "done"
    except Exception as exc:  # noqa: BLE001
        log.exception("demo bootstrap failed")
        _bootstrap_state["status"] = "failed"
        _bootstrap_state["detail"] = str(exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from ..db import ensure_enterprise_credit_code_index
    from ..knowledge.index import ensure_fulltext
    from ..seed.runner import seed_all

    seed_all()
    ensure_enterprise_credit_code_index()
    ensure_fulltext()
    import techins.agents  # noqa: F401
    from ..harness.executor import executor
    from ..services.monitoring import monitor_scheduler

    executor.start()
    monitor_scheduler.start()
    from ..services.evals import eval_runner
    from ..services.scheduler import job_scheduler

    eval_runner.recover()
    job_scheduler.start()
    if os.environ.get("TECHINS_DEMO_BOOTSTRAP", "0") == "1":
        threading.Thread(target=_bootstrap_in_background, name="demo-bootstrap", daemon=True).start()
    yield
    job_scheduler.stop()
    monitor_scheduler.stop()


def _cors_origins() -> list[str]:
    """允许的跨域来源，默认 *（本地开发）；生产环境请通过 TECHINS_CORS_ORIGINS 收紧。"""
    raw = os.environ.get("TECHINS_CORS_ORIGINS", "*")
    return [item.strip() for item in raw.split(",") if item.strip()]


def create_app() -> FastAPI:
    app = FastAPI(title="科保", version="1.0.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=_cors_origins(), allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def _api_metrics(request: Request, call_next):
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            if request.url.path.startswith("/api"):
                from ..services.ops import api_metrics

                api_metrics.record(request.url.path, status, (time.perf_counter() - started) * 1000)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {"category": "系统错误" if exc.status_code >= 500 else "状态冲突", "message": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content={"error": {**detail, "code": f"E{exc.status_code}"}})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        fields = [{"field": ".".join(str(x) for x in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"error": {"category": "输入契约不满足", "message": "请求参数不完整或格式不正确", "fields": fields, "code": "E422"}})

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        code = uuid.uuid4().hex[:8].upper()
        log.exception("unhandled error %s", code)
        return JSONResponse(status_code=500, content={"error": {"category": "系统错误", "message": f"系统内部错误（错误编号 {code}）", "suggestion": "复制错误编号联系管理员", "code": code}})

    from .routes_business import router as business_router
    from .routes_core import router as core_router
    from .routes_platform import router as platform_router

    app.include_router(core_router)
    app.include_router(business_router)
    app.include_router(platform_router)

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "llm_disabled": settings.llm_disabled, "rw_llm_disabled": settings.rw_llm_disabled, "bootstrap": _bootstrap_state}

    return app


app = create_app()
