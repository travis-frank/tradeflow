"""
Datadog APM and structured logging initialization.
Call init_datadog() as the very first thing in api/main.py
before any other imports.
"""

import structlog
from ddtrace import patch_all


def init_datadog() -> None:
    patch_all(fastapi=True, sqlalchemy=True, redis=True, httpx=True, celery=True)

    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.JSONRenderer(),
        ]
    )
