"""
core/logger.py
==============
Centralised structured logging for the Grab Gully Scraper service.

Architecture
------------
We use **structlog** (https://www.structlog.org) layered on top of Python's
standard ``logging`` module.  The two-layer approach is intentional:

  ┌────────────────────────────────────────────────────┐
  │  Application code calls get_logger() / log.info()  │
  │              (structlog BoundLogger)                │
  └────────────────────┬───────────────────────────────┘
                       │  processor pipeline
  ┌────────────────────▼───────────────────────────────┐
  │  structlog.stdlib.LoggerFactory                     │
  │  → wraps stdlib logging.Logger objects              │
  └────────────────────┬───────────────────────────────┘
                       │
  ┌────────────────────▼───────────────────────────────┐
  │  stdlib logging (used by uvicorn, APScheduler,     │
  │  httpx, Playwright) — single output destination    │
  └────────────────────────────────────────────────────┘

Why NOT PrintLoggerFactory?
---------------------------
structlog ships two logger factories:

  • PrintLoggerFactory  – structlog-native; produces PrintLogger objects
                          that have NO ``.name`` attribute.
  • stdlib.LoggerFactory – wraps ``logging.getLogger(name)``; the resulting
                           objects ARE standard ``logging.Logger`` instances,
                           which carry a ``.name`` attribute.

``structlog.stdlib.add_logger_name`` reads ``logger.name`` at call time.
Using PrintLoggerFactory with that processor raises:

    AttributeError: 'PrintLogger' object has no attribute 'name'

Switching to ``stdlib.LoggerFactory`` eliminates this crash and keeps
structlog fully interoperable with the stdlib handlers already configured
for uvicorn / APScheduler / httpx.

Usage
-----
Call ``setup_logging()`` once at application startup (e.g. in main.py):

    from core.logger import setup_logging, get_logger
    setup_logging()

Then obtain a logger anywhere in the codebase:

    from core.logger import get_logger
    log = get_logger(__name__)

    log.info("scrape_started", platform="flipkart", query="shoes")
    log.warning("rate_limited", retry_after=30)
    log.exception("unexpected_error", exc_info=True)

Each call produces a structured event dict that is serialised to either
compact JSON (production) or a colourised human-readable string (dev).
"""

import logging
import sys

import structlog

from core.config import get_settings


def setup_logging() -> None:
    """Configure structlog and stdlib logging for the whole application.

    Must be called **once** before any logger is created.  Subsequent calls
    are effectively no-ops because ``cache_logger_on_first_use=True`` freezes
    the pipeline after the first ``get_logger()`` call.

    Processor pipeline (applied to every log event, in order)
    ----------------------------------------------------------
    1. ``merge_contextvars``  – injects context variables bound with
                                ``structlog.contextvars.bind_contextvars()``,
                                e.g. ``request_id``, ``scrape_id``.
    2. ``add_log_level``      – adds ``level`` key ("info", "warning", …).
                                Must come *before* add_logger_name so the
                                level is available to downstream processors.
    3. ``add_logger_name``    – adds ``logger`` key from ``logger.name``.
                                Requires a stdlib-backed logger (see module
                                docstring for why PrintLoggerFactory fails).
    4. ``TimeStamper``        – adds ISO-8601 ``timestamp`` key.
    5. ``StackInfoRenderer``  – renders ``stack_info`` when present.
    6a. Production only:
        ``dict_tracebacks``   – serialises exceptions to a dict (not a string)
                                so Railway/Datadog can index individual frames.
        ``JSONRenderer``      – serialises the whole event dict to a JSON line.
    6b. Development only:
        ``ConsoleRenderer``   – pretty-prints with colours and alignment.

    Stdlib logging (uvicorn, APScheduler, httpx, Playwright)
    ---------------------------------------------------------
    ``logging.basicConfig`` is called with ``format="%(message)s"`` so that
    structlog's pre-formatted output is not double-formatted by the stdlib
    formatter.  Noisy third-party loggers are throttled to WARNING to reduce
    log volume.
    """
    settings = get_settings()

    # ── Shared processors (run in every environment) ──────────────────────
    shared_processors: list = [
        # Pull in any context vars bound via structlog.contextvars (e.g.
        # request_id set in a FastAPI middleware).
        structlog.contextvars.merge_contextvars,

        # Add "level" key BEFORE add_logger_name so downstream formatters
        # can use the level when rendering the logger name if needed.
        structlog.stdlib.add_log_level,

        # Add "logger" key — works only because we use stdlib.LoggerFactory,
        # which produces logging.Logger objects that carry a `.name` attribute.
        structlog.stdlib.add_logger_name,

        # ISO-8601 timestamp, e.g. "2024-04-20T04:05:06.789Z"
        structlog.processors.TimeStamper(fmt="iso"),

        # Render __stack_info__ if the caller passed stack_info=True.
        structlog.processors.StackInfoRenderer(),
    ]

    # ── Environment-specific renderer ─────────────────────────────────────
    if settings.is_production:
        # Structured JSON — easy to ingest by Railway log drains, Datadog, etc.
        # dict_tracebacks converts exception objects to dicts so individual
        # frames can be searched / indexed rather than treating the traceback
        # as an opaque string.
        processors = shared_processors + [
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]
    else:
        # Human-readable, colourised output for local development.
        processors = shared_processors + [
            structlog.dev.ConsoleRenderer(colors=True),
        ]

    # ── Bind the pipeline to structlog ────────────────────────────────────
    structlog.configure(
        processors=processors,

        # BoundLogger is the stdlib-compatible wrapper class.  It exposes the
        # familiar .debug() / .info() / .warning() / .error() / .exception()
        # interface while routing events through the processor pipeline above.
        wrapper_class=structlog.stdlib.BoundLogger,

        # Plain dict is sufficient for our context storage; no thread-local
        # or async-local magic needed because we use contextvars explicitly.
        context_class=dict,

        # LoggerFactory wraps logging.getLogger(name) so the underlying
        # logger object is always a stdlib Logger with a .name attribute.
        # This is what makes structlog.stdlib.add_logger_name work correctly.
        logger_factory=structlog.stdlib.LoggerFactory(),

        # After the first call to get_logger() the pipeline is frozen and
        # stored on the bound logger instance — effectively free on hot paths.
        cache_logger_on_first_use=True,
    )

    # ── Stdlib logging (for uvicorn, APScheduler, httpx, Playwright) ──────
    # format="%(message)s" prevents double-formatting: structlog already
    # renders the full line; stdlib should just emit it as-is.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.getLevelName(settings.log_level.upper()),
    )

    # Throttle noisy third-party loggers that would otherwise flood the output
    # with low-value DEBUG/INFO lines during normal operation.
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("playwright").setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a structlog BoundLogger bound to *name*.

    Args:
        name: Typically ``__name__`` of the calling module, e.g.
              ``"scrapers.flipkart"``.  This value is emitted as the
              ``logger`` field in every log line produced by the returned
              logger.

    Returns:
        A ``structlog.stdlib.BoundLogger`` instance.  The logger is
        backed by a stdlib ``logging.Logger`` so it participates in the
        stdlib handler hierarchy (level filtering, handlers, propagation).

    Example::

        log = get_logger(__name__)
        log.info("product_fetched", product_id="P123", price=499)
    """
    return structlog.stdlib.get_logger(name)
