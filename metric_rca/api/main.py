"""FastAPI application factory for MetricRCA."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from metric_rca.api.dependencies import ApiDependencies
from metric_rca.api.routes import build_router
from metric_rca.api.schemas import ErrorBody


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

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        if exc.status_code == 404:
            return JSONResponse(
                status_code=404,
                content=ErrorBody(
                    error_code="ROUTE_NOT_FOUND",
                    message="route not found",
                ).model_dump(),
            )
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorBody(
                error_code="HTTP_ERROR",
                message=str(exc.detail),
            ).model_dump(),
        )

    app.include_router(build_router(dependencies or ApiDependencies()))
    return app


app = create_app()
