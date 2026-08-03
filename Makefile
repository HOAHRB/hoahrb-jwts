.PHONY: check build

check:
	uv run ruff check .
	uv run ruff format --check .
	uv run pytest -v

build:
	uv build
