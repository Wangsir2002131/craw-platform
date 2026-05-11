"""FastAPI application entry point for the crawler platform."""

from __future__ import annotations

from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(
        title="Crawler Platform API",
        description="API service for crawler platform control and observability.",
        version="0.1.0",
    )

    @app.get("/")
    async def root() -> dict[str, str]:
        return {
            "service": "crawler-platform-api",
            "status": "ok",
        }

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "healthy"}

    return app


app = create_app()
