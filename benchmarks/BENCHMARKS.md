# tradeflow Load Benchmarks

All tests run against a local Docker Compose stack on a MacBook Air.
High latency figures at scale reflect cold yfinance provider calls under concurrent load —
cached endpoint performance is significantly faster (see Cold vs Warm table below).

---

## Cold vs Warm Latency (p50)

| Endpoint | Cold p50 | Warm p50 | Reduction |
|---|---:|---:|---:|
| `GET /api/prices/current/[ticker]` | 518ms | 50ms | **90%** |
| `GET /api/prices/historical/AAPL` | 272ms | 41ms | **85%** |
| `GET /api/news/AAPL` | 645ms | 21ms | **97%** |
| `GET /api/fundamentals/AAPL/income` | 671ms | 19ms | **97%** |
| `GET /api/prices/AAPL/indicators` | 133ms | 25ms | **81%** |

Warm p50 taken as median of 5 consecutive cached requests after cold miss.
Cold hit triggers yfinance provider fetch + DB persist + Redis cache write.
Warm hit is a pure Redis read.

---

## Load Test Summary

| Concurrent Users | RPS | p50 (ms) | p95 (ms) | Error Rate | Notes |
|---:|---:|---:|---:|---:|---|
| 100 | 28 | 770 | 7,700 | 0.03% | 1 connection blip; no rate limit errors |
| 500 | 11 | 12,000 | 16,000 | 0.00% | Zero failures; latency increase from concurrent cold provider calls |
| 1,000 | 4 | 15,000 | 23,000 | 0.69% | 3 auth 500s under extreme load; market data endpoints had 0 failures |

p50/p95 figures reflect a mix of cached reads (~20ms) and cold yfinance provider fetches
(5–20s) across the test window. Under steady-state cached traffic, p50 is sub-100ms
(see Cold vs Warm table above).

---

## How To Reproduce

```bash
# 1. Start the stack
make dev

# 2. Raise rate limit for benchmark (add to .env, then restart api)
# RATE_LIMIT_REQUESTS=10000
docker compose up -d --force-recreate api

# 3. Seed the demo user (password must be 8+ chars)
python3 benchmarks/scripts/seed_demo_user.py

# 4. Run load tests
mkdir -p benchmarks/results

locust -f benchmarks/locustfile.py --host http://localhost:8000 \
  --headless -u 100 -r 20 -t 2m \
  --csv benchmarks/results/locust_100

locust -f benchmarks/locustfile.py --host http://localhost:8000 \
  --headless -u 500 -r 50 -t 2m \
  --csv benchmarks/results/locust_500

locust -f benchmarks/locustfile.py --host http://localhost:8000 \
  --headless -u 1000 -r 100 -t 2m \
  --csv benchmarks/results/locust_1000
```

Raw CSV results are gitignored (`benchmarks/results/`).