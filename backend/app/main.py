from __future__ import annotations

from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from starlette.responses import JSONResponse

from app.api import analytics, auth, bookings, calls, owner, public, restaurants, studio, tools, twilio
from app.core.config import settings
from app.core.database import SessionLocal, init_db
from app.core.observability import client_ip_for_request, configure_logging, get_request_id, logging_middleware
from app.core.rate_limit import InMemoryRateLimiter
from app.reservation_lab.web import app as reservation_lab_web_app
from app.services.seed import seed_demo_data
from starlette.middleware.wsgi import WSGIMiddleware

configure_logging(settings.log_level)
if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        traces_sample_rate=settings.sentry_traces_sample_rate,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.auto_create_schema and not settings.reservation_lab_booking_enabled:
        init_db()
        if settings.seed_demo:
            db = SessionLocal()
            try:
                seed_demo_data(db)
            finally:
                db.close()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
limiter = InMemoryRateLimiter()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", settings.tool_secret_header_name],
)


def _rate_limit_for_path(path: str) -> tuple[str, int]:
    if path in {"/health", "/healthz", "/readyz"}:
        return "health", 0
    if path.startswith(f"{settings.api_prefix}/auth/"):
        return "auth", settings.rate_limit_auth_requests_per_window
    if path.startswith(f"{settings.api_prefix}/tools/"):
        return "sensitive", settings.rate_limit_sensitive_requests_per_window
    if path.startswith(f"{settings.api_prefix}/public/"):
        return "public", settings.rate_limit_public_requests_per_window
    return "default", settings.rate_limit_requests_per_window


@app.middleware("http")
async def request_logging(request: Request, call_next):
    return await logging_middleware(request, call_next)


@app.middleware("http")
async def request_rate_limit(request: Request, call_next):
    if not settings.rate_limit_enabled:
        return await call_next(request)

    bucket_name, limit = _rate_limit_for_path(request.url.path)
    if limit <= 0:
        return await call_next(request)

    request_id = get_request_id(request)
    client_ip = client_ip_for_request(request)
    allowed, retry_after = limiter.allow(
        key=f"{bucket_name}:{client_ip}",
        limit=limit,
        window_seconds=settings.rate_limit_window_seconds,
    )
    if allowed:
        return await call_next(request)

    response = JSONResponse(
        status_code=429,
        content={
            "detail": "Rate limit exceeded",
            "retry_after_seconds": retry_after,
            "request_id": request_id,
        },
    )
    response.headers["Retry-After"] = str(retry_after)
    response.headers["X-Request-ID"] = request_id
    return response

app.include_router(auth.router, prefix=settings.api_prefix)
app.include_router(restaurants.router, prefix=settings.api_prefix)
app.include_router(bookings.router, prefix=settings.api_prefix)
app.include_router(calls.router, prefix=settings.api_prefix)
app.include_router(owner.router, prefix=settings.api_prefix)
app.include_router(analytics.router, prefix=settings.api_prefix)
app.include_router(studio.router, prefix=settings.api_prefix)
app.include_router(tools.router, prefix=settings.api_prefix)
app.include_router(public.router, prefix=settings.api_prefix)
app.include_router(twilio.router, prefix=settings.api_prefix)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> dict[str, str]:
    if settings.reservation_lab_booking_enabled:
        from app.reservation_lab.db import get_connection

        with get_connection() as conn:
            conn.execute("SELECT 1")
        return {"status": "ok"}
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
    finally:
        db.close()
    return {"status": "ok"}


# Reservation Lab owner UI (Flask) — mounted after API routes
app.mount("/", WSGIMiddleware(reservation_lab_web_app))
