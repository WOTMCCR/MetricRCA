"""FastAPI application factory for MetricRCA."""

from __future__ import annotations

from fastapi import FastAPI

from metric_rca.api.dependencies import ApiDependencies
from metric_rca.api.routes import build_router


def create_app(dependencies: ApiDependencies | None = None) -> FastAPI:
    app = FastAPI(title="MetricRCA API")
    app.include_router(build_router(dependencies or ApiDependencies()))
    return app


app = create_app()
