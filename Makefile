.PHONY: help install lint format typecheck test test-cov check init-db clean

help:  ## Show this help.
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## Install all dev dependencies into a uv-managed venv.
	uv sync --extra dev

lint:  ## Lint with ruff (no auto-fix).
	uv run ruff check pa_assistant tests

format:  ## Auto-format with ruff.
	uv run ruff format pa_assistant tests
	uv run ruff check --fix pa_assistant tests

typecheck:  ## Run mypy in strict mode.
	uv run mypy pa_assistant tests

test:  ## Run unit tests.
	uv run pytest -q

test-cov:  ## Run unit tests with coverage.
	uv run pytest --cov=pa_assistant --cov-report=term-missing

check: lint typecheck test  ## Run lint + typecheck + tests.

init-db:  ## Initialize the local DuckDB schema.
	uv run pa init-db

clean:  ## Remove caches and the local DuckDB file.
	rm -rf .ruff_cache .mypy_cache .pytest_cache .coverage htmlcov
	rm -f data/*.duckdb data/*.duckdb.wal
	find . -type d -name __pycache__ -exec rm -rf {} +
