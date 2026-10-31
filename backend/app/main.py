"""FastAPI 应用装配。"""

from __future__ import annotations

import logging
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app import db
from app.api import files, flows, instances, projects, releases
from app.logging_setup import setup_logging

setup_logging()

logger = logging.getLogger(__name__)

DEV_ORIGINS = ["http://127.0.0.1:5173", "http://localhost:5173"]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    logger.info(
        "启动完成：python=%s pid=%s cwd=%s db=%s",
        sys.version.split()[0],
        os.getpid(),
        os.getcwd(),
        os.environ.get("NEWDIAG_DB") or "(默认 backend/data/newdiag.db)",
    )
    yield
    logger.info("进程退出 pid=%s", os.getpid())


app = FastAPI(title="NewDiag 诊断流程平台", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=DEV_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)


@app.middleware("http")
async def access_log(request: Request, call_next):
    """每个请求一行日志，带 request id，方便和前端报错对上。"""
    request_id = uuid.uuid4().hex[:8]
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("[%s] %s %s 未捕获异常", request_id, request.method, request.url.path)
        raise
    cost = (time.perf_counter() - started) * 1000
    if request.url.path == "/api/health" and response.status_code < 400:
        level = logging.DEBUG
    elif response.status_code >= 400:
        level = logging.WARNING
    else:
        level = logging.INFO
    logger.log(
        level,
        "[%s] %s %s -> %s %.0fms",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        cost,
    )
    response.headers["X-Request-ID"] = request_id
    return response


for module in (flows, releases, instances, projects, files):
    app.include_router(module.router, prefix="/api")


@app.get("/api/health", tags=["meta"])
def health() -> dict[str, bool]:
    return {"ok": True}
