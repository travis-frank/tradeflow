"""Custom Datadog metrics"""

from datadog import statsd


def cache_hit(endpoint: str) -> None:
    statsd.increment("tradeflow.cache.hit", tags=[f"endpoint:{endpoint}"])


def cache_miss(endpoint: str) -> None:
    statsd.increment("tradeflow.cache.miss", tags=[f"endpoint:{endpoint}"])


def api_call(provider: str, ticker: str) -> None:
    statsd.increment(
        "tradeflow.api.call",
        tags=[f"provider:{provider}", f"ticker:{ticker}"],
    )


def record_latency(metric: str, ms: float) -> None:
    statsd.histogram(f"tradeflow.latency.{metric}", ms)
