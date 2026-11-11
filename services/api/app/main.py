"""CLIPX AI API (FastAPI)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from app.config import get_settings
from app.routers import clips, core, integrations, publishing


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    s = get_settings()
    logging.basicConfig(level=s.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if s.environment == "production":
        problems = s.production_problems()
        if problems:  # fail closed: never serve with an insecure configuration
            raise RuntimeError("refusing to start: " + "; ".join(problems))
    yield


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="CLIPX AI API", version="0.1.0", lifespan=lifespan,
                  docs_url=None if s.environment == "production" else "/docs",
                  redoc_url=None, openapi_url=None if s.environment == "production" else "/openapi.json")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        if int(request.headers.get("content-length") or 0) > 5 * 1024 * 1024:
            return JSONResponse({"detail": "request too large"}, status_code=413)
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = response.headers.get("Cache-Control", "no-store")
        return response

    app.include_router(core.public)
    app.include_router(core.router)
    app.include_router(clips.router)
    app.include_router(publishing.router)
    app.include_router(integrations.router)
    app.include_router(integrations.webhooks)
    return app


app = create_app()
