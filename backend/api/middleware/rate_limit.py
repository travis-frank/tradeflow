import time
import uuid

import structlog
from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from cache.redis_client import get_redis
from core.config import get_settings

log = structlog.get_logger()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rate limiting middleware using a Redis sorted-set sliding-window algorithm."""

    skip_paths: set[str] = {"/health", "/metrics", "/docs", "/redoc", "/openapi.json"}

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if request.url.path in self.skip_paths:
            return await call_next(request)

        settings = get_settings()
        redis_client = await get_redis()

        client_host: str | None = request.client.host if request.client is not None else None
        client_ip: str = client_host or "unknown"
        key: str = f"rate_limit:{client_ip}"

        now_ms: int = int(time.time() * 1000)
        window_seconds: int = settings.rate_limit_window
        window_ms: int = window_seconds * 1000
        window_start_ms: int = now_ms - window_ms
        member: str = f"{now_ms}-{uuid.uuid4().hex}"

        await redis_client.zadd(key, {member: now_ms})
        await redis_client.zremrangebyscore(key, "-inf", window_start_ms - 1)
        count: int = await redis_client.zcard(key)
        await redis_client.expire(key, window_seconds)

        limit: int = settings.rate_limit_requests
        remaining: int = max(0, limit - count)

        if count > limit:
            log.warning("rate limit exceeded", client_ip=client_ip)
            response: Response = JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
            )
            response.headers["X-RateLimit-Limit"] = str(limit)
            response.headers["X-RateLimit-Remaining"] = str(remaining)
            return response

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response
