.PHONY: install test lint format typecheck openapi run migrate

install:
	uv sync --all-extras
	uv run playwright install chromium

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .
	uv run ruff check --fix .

typecheck:
	uv run mypy

openapi:
	uv run python -m ptjobs openapi > docs/openapi.json

run:
	uv run uvicorn ptjobs.api:app --reload

migrate:
	uv run alembic upgrade head
