"""FastAPI Enterprise Application Factory."""
from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from ..db import connect
from ..monitoring import REGISTRY
from ..web import PAGE
from .routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Database initialization
    db_target = os.environ.get("DATABASE_URL", "launch_intel.db")
    is_demo = os.environ.get("LAUNCH_INTEL_DEMO", "0") in ("1", "true", "True")

    if is_demo:
        from ..sample_data import seed_all
        conn = connect(target=":memory:", check_same_thread=False)
        seed_all(conn)
    else:
        conn = connect(target=db_target, check_same_thread=False)

    app.state.conn = conn
    yield
    try:
        conn.close()
    except Exception:
        pass


def create_app() -> FastAPI:
    app = FastAPI(
        title="Launch Intelligence Enterprise API",
        description=(
            "Production-grade entertainment launch analytics engine: velocity, share of voice, "
            "Bass diffusion trajectory forecasting, robust ensemble anomaly detection, "
            "aspect-based sentiment NLP, and claim-verified executive briefings."
        ),
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan
    )

    # CORS Middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Metrics & Latency Middleware
    @app.middleware("http")
    async def metrics_middleware(request: Request, call_next):
        start = time.perf_counter()
        response: Response = await call_next(request)
        duration = time.perf_counter() - start

        endpoint = request.url.path
        method = request.method
        status_code = str(response.status_code)

        REGISTRY.inc_counter("launch_intel_http_requests_total", 1.0, {
            "method": method,
            "endpoint": endpoint,
            "status": status_code
        })
        REGISTRY.observe_histogram("launch_intel_http_request_duration_seconds", duration, {
            "endpoint": endpoint
        })

        return response

    # Mount UI at root
    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index():
        return HTMLResponse(content=PAGE)

    # Include REST routers
    app.include_router(router)

    return app


app = create_app()
