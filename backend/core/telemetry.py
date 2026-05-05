"""
Datadog APM and structured logging initialization.
Call init_datadog() as the very first thing in api/main.py
before any other imports.
"""
import structlog

try:
    from ddtrace import patch_all
    DATADOG_AVAILABLE = True
except ImportError:
    DATADOG_AVAILABLE = False


def init_datadog() -> None:
    if DATADOG_AVAILABLE:
        patch_all(fastapi=True, sqlalchemy=True, redis=True, httpx=True, celery=True)

    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.JSONRenderer(),
        ]
    )