"""FastAPI 应用装配。"""

from __future__ import annotations

import logging
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import db
from app.api import files, flows, instances, projects, releases
from app.logging_setup import setup_logging

setup_logging()

logger = logging.getLogger(__name__)

BACKEND_ROOT = Path(__file__).resolve().parents[1]
# 只有显式指定、或同目录下存在 web/（离线包形态）时才托管前端；开发态仍走 Vite
WEB_DIR = Path(os.environ.get("NEWDIAG_WEB_DIR") or (BACKEND_ROOT.parent / "web"))
if not (WEB_DIR / "index.html").is_file():
    WEB_DIR = None  # type: ignore[assignment]

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


if WEB_DIR is not None:
    # 离线包形态：一个进程同时提供前端与接口，静态资源单独挂载，其余路径回落到 index.html
    _assets = WEB_DIR / "assets"
    if _assets.is_dir():
        app.mount("/assets", StaticFiles(directory=_assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        target = (WEB_DIR / full_path).resolve()
        try:
            target.relative_to(WEB_DIR.resolve())
        except ValueError:
            raise HTTPException(status_code=404, detail="Not Found") from None
        if full_path and target.is_file():
            return FileResponse(target)
        return FileResponse(WEB_DIR / "index.html")

    logger.info("前端静态目录 %s", WEB_DIR)
