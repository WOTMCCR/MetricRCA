"""FastAPI application factory for MetricRCA."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from metric_rca.api.dependencies import ApiDependencies
from metric_rca.api.routes import build_router


LOCAL_UI_ORIGINS = (
    "http://127.0.0.1:5173",
    "http://localhost:5173",
)


def create_app(dependencies: ApiDependencies | None = None) -> FastAPI:
    app = FastAPI(title="MetricRCA API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(LOCAL_UI_ORIGINS),
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["content-type"],
    )
    app.include_router(build_router(dependencies or ApiDependencies()))
    return app


app = create_app()
