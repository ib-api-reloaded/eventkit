PYTHON_VERSIONS ?= 3.11 3.12 3.13 3.14
PYTEST := pytest -q -p no:cacheprovider -W error::DeprecationWarning

.PHONY: help test test-matrix lint

help:
	@echo "Targets:"
	@echo "  test         - run the tests on the default interpreter"
	@echo "  test-matrix  - run the tests on each of PYTHON_VERSIONS,"
	@echo "                 each in its own .venvs/py<version> environment"
	@echo "  lint         - ruff check, ruff format --check, mypy"

test:
	uv run --all-extras $(PYTEST)

test-matrix:
	@set -e; for v in $(PYTHON_VERSIONS); do \
		echo "== Python $$v"; \
		UV_PROJECT_ENVIRONMENT=.venvs/py$$v uv run --all-extras --python $$v $(PYTEST); \
	done

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy eventkit
