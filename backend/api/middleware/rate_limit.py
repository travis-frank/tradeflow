import structlog
from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from cache.redis_client import get_redis
from core.config import get_settings

log = structlog.get_logger()


class RateLimitMiddleware(BaseHTTPMiddleware):
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

        count: int = await redis_client.incr(key)
        if count == 1:
            await redis_client.expire(key, settings.rate_limit_window)

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
