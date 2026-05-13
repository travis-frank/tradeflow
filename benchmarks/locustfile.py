from __future__ import annotations

from itertools import cycle

from locust import HttpUser, between, task
from locust.exception import StopUser


class TradeflowUser(HttpUser):
    wait_time = between(0.5, 2)

    def on_start(self) -> None:
        response = self.client.post(
            "/api/auth/login",
            json={"email": "demo@tradeflow.dev", "password": "demo1234"},
            name="/api/auth/login",
        )

        if response.status_code != 200:
            raise StopUser(f"Login failed with status {response.status_code}: {response.text}")

        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise StopUser("Login response did not include access_token")

        self.auth_headers = {"Authorization": f"Bearer {token}"}
        self._ticker_cycle = cycle(["AAPL", "MSFT", "NVDA", "TSLA"])

    @task(6)
    def get_current_price(self) -> None:
        ticker = next(self._ticker_cycle)
        self.client.get(
            f"/api/prices/current/{ticker}",
            headers=self.auth_headers,
            name="/api/prices/current/[ticker]",
        )

    @task(3)
    def get_historical_prices(self) -> None:
        self.client.get(
            "/api/prices/historical/AAPL?start=2024-01-01&end=2024-12-31",
            headers=self.auth_headers,
            name="/api/prices/historical/AAPL",
        )

    @task(3)
    def get_news(self) -> None:
        self.client.get(
            "/api/news/AAPL",
            headers=self.auth_headers,
            name="/api/news/AAPL",
        )

    @task(2)
    def get_fundamentals_income(self) -> None:
        self.client.get(
            "/api/fundamentals/AAPL/income",
            headers=self.auth_headers,
            name="/api/fundamentals/AAPL/income",
        )

    @task(1)
    def get_indicators(self) -> None:
        self.client.get(
            "/api/prices/AAPL/indicators?start=2024-01-01&end=2024-12-31",
            headers=self.auth_headers,
            name="/api/prices/AAPL/indicators",
        )
