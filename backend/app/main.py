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
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import db
from app.api import files, flows, instances, projects, releases
from app.logging_setup import setup_logging
from app.services import storage

setup_logging()

logger = logging.getLogger(__name__)

BACKEND_ROOT = Path(__file__).resolve().parents[1]
# 只有显式指定、或同目录下存在 web/（离线包形态）时才托管前端；开发态仍走 Vite
WEB_DIR = Path(os.environ.get("NEWDIAG_WEB_DIR") or (BACKEND_ROOT.parent / "web"))
if not (WEB_DIR / "index.html").is_file():
    WEB_DIR = None  # type: ignore[assignment]

DEV_ORIGINS = ["http://127.0.0.1:5173", "http://localhost:5173"]

# multipart 表单自身开销的粗筛余量
MULTIPART_SLACK_BYTES = 1024 * 1024


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    try:
        storage.cleanup_temp_files()
    except Exception:  # 清扫失败不能挡住启动
        logger.warning("上传临时文件清扫失败", exc_info=True)
    logger.info(
        "启动完成：python=%s pid=%s cwd=%s db=%s",
        sys.version.split()[0],
        os.getpid(),
        os.getcwd(),
        os.environ.get("NEWDIAG_DB") or "(默认 backend/data/newdiag.db)",
    )
    _report_stale_instances()
    yield
    from app.services.generated_runner import runner
    runner.shutdown()
    logger.info("进程退出 pid=%s", os.getpid())


def _report_stale_instances() -> None:
    """启动时点名上次没跑完的实例。

    诊断分支是后端的子进程，后端退出（含开发态改代码触发的重启）会把整棵树带走，
    库里的 token 却还停在 running。不打这行日志，界面看起来就是「卡在某个节点」。
    """
    from sqlalchemy import select

    from app import models

    try:
        with db.SessionLocal() as session:
            rows = session.scalars(select(models.Instance).where(
                models.Instance.status.in_(("running", "waiting", "paused"))
            )).all()
    except Exception:
        logger.warning("检查遗留实例失败", exc_info=True)
        return
    if not rows:
        return
    sample = "、".join(f"{item.id}({item.status})" for item in rows[:5])
    logger.warning(
        "发现 %d 个上次未结束的实例，诊断进程已随父进程退出，它们会一直停在原状态：%s%s",
        len(rows), sample, " 等" if len(rows) > 5 else "",
    )


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
async def reject_oversized_upload(request: Request, call_next):
    """上传前置粗筛。

    FastAPI 在进入 endpoint 前就会把整个 multipart 收完，所以只能靠 Content-Length
    提前拒绝。该值是整个表单大小（含 boundary、part 头、其他字段），比文件本身大，
    因此留 1 MiB 余量只做粗筛：命中即拒，未命中交给流式计数精确判定。
    这里绝不能读 body，也绝不能抛 HTTPException（middleware 里不会被转成响应）。
    """
    if request.method == "POST" and request.url.path == "/api/files":
        raw = request.headers.get("content-length")
        if raw is not None:
            try:
                declared = int(raw)
            except ValueError:
                declared = None
            if declared is not None and declared > storage.MAX_UPLOAD_BYTES + MULTIPART_SLACK_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={"detail": storage.UPLOAD_TOO_LARGE_DETAIL},
                )
    return await call_next(request)


@app.middleware("http")
async def access_log(request: Request, call_next):
    """每个请求一行日志，带 request id，方便和前端报错对上。"""
    request_id = uuid.uuid4().hex[:8]
    request.state.request_id = request_id
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


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    """兜底：任何未捕获异常都变成带请求 id 的 500，不让客户端拿到裸 HTML。"""
    request_id = getattr(request.state, "request_id", "-")
    logger.error("[%s] 未捕获异常：%s %s", request_id, request.method, request.url.path, exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={"detail": f"服务器内部错误（请求 id {request_id}），请把该 id 发给管理员"},
        headers={"X-Request-ID": request_id},
    )


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
