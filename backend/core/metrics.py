"""Custom Datadog metrics"""

try:
    from datadog import statsd as _statsd
except ImportError:
    class _NoOpStatsd:
        def increment(self, *args, **kwargs): pass
        def histogram(self, *args, **kwargs): pass

    _statsd = _NoOpStatsd()  # type: ignore

statsd = _statsd


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