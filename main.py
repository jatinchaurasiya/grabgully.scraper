"""
main.py
────────
Grab Gully Scraper Service — Production FastAPI application.

Entry point for Railway deployment.
All routes, middleware, startup/shutdown lifecycle managed here.

Run locally:
    uvicorn main:app --reload --host 0.0.0.0 --port 8000
"""
from __future__ import annotations
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request, HTTPException, Security, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

from core.config import get_settings
from core.logger import setup_logging, get_logger
from core.scheduler import create_scheduler
from api import deals, search, compare, affiliate, watchlist

# ── Bootstrap logging before anything else ────────────────────────────────────
setup_logging()
log = get_logger("main")

# ── Prometheus metrics ────────────────────────────────────────────────────────
REQUEST_COUNT = Counter(
    "grabgully_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
)
REQUEST_LATENCY = Histogram(
    "grabgully_request_duration_seconds",
    "HTTP request latency",
    ["endpoint"],
)

# ── App lifecycle (startup / shutdown) ────────────────────────────────────────
_scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: start scheduler. Shutdown: stop it cleanly."""
    global _scheduler
    s = get_settings()

    log.info(
        "startup",
        version=s.app_version,
        env=s.app_env,
        amazon_ready=s.amazon_configured,
        flipkart_ready=s.flipkart_configured,
    )

    _scheduler = create_scheduler()
    _scheduler.start()
    log.info("scheduler_started", jobs=len(_scheduler.get_jobs()))

    yield   # App runs here

    log.info("shutdown_initiated")
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
    log.info("shutdown_complete")


# ── FastAPI app ───────────────────────────────────────────────────────────────
settings = get_settings()

app = FastAPI(
    title        = "Grab Gully Scraper API",
    description  = "Backend scraping + deal aggregation service for Grab Gully Android app",
    version      = settings.app_version,
    docs_url     = None if settings.is_production else "/docs",
    redoc_url    = None if settings.is_production else "/redoc",
    lifespan     = lifespan,
)

# ── Middleware ─────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins     = settings.origins_list,
    allow_credentials = True,
    allow_methods     = ["GET", "POST", "PATCH", "DELETE"],
    allow_headers     = ["Authorization", "Content-Type"],
)

app.add_middleware(GZipMiddleware, minimum_size=500)


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    """Record request count + latency for every request."""
    start    = time.monotonic()
    response = await call_next(request)
    duration = time.monotonic() - start

    endpoint = request.url.path
    REQUEST_COUNT.labels(
        method   = request.method,
        endpoint = endpoint,
        status   = response.status_code,
    ).inc()
    REQUEST_LATENCY.labels(endpoint=endpoint).observe(duration)

    # Add timing header for debugging
    response.headers["X-Response-Time"] = f"{duration:.3f}s"
    return response


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """Add security headers to every response."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"]         = "DENY"
    response.headers["X-XSS-Protection"]        = "1; mode=block"
    response.headers["Referrer-Policy"]          = "strict-origin-when-cross-origin"
    return response


# ── Exception handlers ────────────────────────────────────────────────────────

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    log.warning("http_error", path=request.url.path,
                status=exc.status_code, detail=exc.detail)
    return JSONResponse(
        status_code = exc.status_code,
        content     = {"error": exc.detail, "status": exc.status_code},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    log.error("unhandled_exception", path=request.url.path,
              error=str(exc), exc_info=True)
    return JSONResponse(
        status_code = 500,
        content     = {"error": "Internal server error", "status": 500},
    )


# ── Admin auth ────────────────────────────────────────────────────────────────
security = HTTPBearer()

def verify_admin(creds: HTTPAuthorizationCredentials = Security(security)) -> str:
    if creds.credentials != get_settings().scraper_secret:
        raise HTTPException(403, "Forbidden")
    return creds.credentials


# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(deals.router,     prefix="/deals")
app.include_router(search.router,    prefix="/search")
app.include_router(compare.router,   prefix="/compare")
app.include_router(affiliate.router, prefix="/go")
app.include_router(watchlist.router, prefix="/watchlist")


# ── Public endpoints ──────────────────────────────────────────────────────────

@app.get("/health", tags=["system"])
async def health():
    """
    Health check — Railway uses this to know the app is ready.
    Returns 200 when scheduler is running and DB is reachable.
    """
    db_ok = True
    try:
        from services.db import get_db
        get_db().table("platform_listings").select("id").limit(1).execute()
    except Exception:
        db_ok = False

    scheduler_ok = _scheduler is not None and _scheduler.running
    status       = "healthy" if (db_ok and scheduler_ok) else "degraded"

    return {
        "status":        status,
        "version":       settings.app_version,
        "env":           settings.app_env,
        "db":            "ok" if db_ok else "error",
        "scheduler":     "running" if scheduler_ok else "stopped",
        "amazon_api":    "configured" if settings.amazon_configured else "not_configured",
        "flipkart_api":  "configured" if settings.flipkart_configured else "not_configured",
    }


@app.get("/metrics", tags=["system"])
async def metrics():
    """Prometheus metrics endpoint."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ── Admin-only endpoints ──────────────────────────────────────────────────────

@app.post("/admin/trigger-scrape", tags=["admin"])
async def trigger_scrape(_: str = Depends(verify_admin)):
    """Manually trigger a full scrape run. Requires SCRAPER_SECRET header."""
    import asyncio
    from core.scheduler import _run_scrapers
    asyncio.create_task(_run_scrapers())
    return {"message": "Scraping shuru ho gaya! Check /health for status."}


@app.post("/admin/trigger-price-check", tags=["admin"])
async def trigger_price_check(_: str = Depends(verify_admin)):
    """Manually trigger price drop check + notifications."""
    import asyncio
    from core.scheduler import _run_price_check
    asyncio.create_task(_run_price_check())
    return {"message": "Price check shuru ho gaya!"}


@app.get("/admin/jobs", tags=["admin"])
async def list_jobs(_: str = Depends(verify_admin)):
    """List all scheduled jobs and their next run times."""
    if not _scheduler:
        return {"jobs": []}
    jobs = []
    for job in _scheduler.get_jobs():
        jobs.append({
            "id":       job.id,
            "name":     job.name,
            "next_run": str(job.next_run_time),
        })
    return {"jobs": jobs}
