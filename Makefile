.PHONY: dev down logs test lint format migrate migration setup

dev:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f api worker

migrate:
	docker compose exec api uv run alembic upgrade head

migration:
	docker compose exec api uv run alembic revision --autogenerate -m "$(name)"

test:
	cd backend && uv run pytest

test-cov:
	cd backend && uv run pytest --cov=. --cov-report=html

lint:
	cd backend && uv run ruff check . && uv run mypy .

format:
	cd backend && uv run black . && uv run ruff check --fix .

setup:
	cp .env.example .env
	cd backend && uv sync
	cd backend && uv run pre-commit install
	@echo "tradeflow ready — edit .env then run: make dev"