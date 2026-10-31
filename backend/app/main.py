"""FastAPI 应用装配。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import db
from app.api import files, flows, instances, projects, releases

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

DEV_ORIGINS = ["http://127.0.0.1:5173", "http://localhost:5173"]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="NewDiag 诊断流程平台", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=DEV_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

for module in (flows, releases, instances, projects, files):
    app.include_router(module.router, prefix="/api")


@app.get("/api/health", tags=["meta"])
def health() -> dict[str, bool]:
    return {"ok": True}
