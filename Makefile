# Every check CI runs is a target here, and CI calls these targets, so a
# local `make lint test` runs exactly what CI runs.

PYTHON_VERSIONS ?= 3.11 3.12 3.13 3.14
# Per-test limit: a hung test fails by name, with a traceback.
TEST_TIMEOUT ?= 60
# Per-version limit enforced by scripts/test_matrix.py.
MATRIX_TIMEOUT ?= 300
PYTEST := pytest -q -p no:cacheprovider -W error::DeprecationWarning --timeout=$(TEST_TIMEOUT) --timeout-method=thread

.PHONY: help ruff typecheck lint test test-matrix build

help:
	@echo "Targets:"
	@echo "  ruff         - ruff check and ruff format --check"
	@echo "  typecheck    - mypy over the package, tests and scripts"
	@echo "  lint         - ruff + typecheck"
	@echo "  test         - run the tests on the current interpreter"
	@echo "                 (UV_PYTHON selects another one)"
	@echo "  test-matrix  - run the tests on each of PYTHON_VERSIONS, each in"
	@echo "                 its own .venvs/py<version> environment and time-boxed;"
	@echo "                 logs in .venvs/logs/"
	@echo "  build        - wheel and sdist into dist/"

ruff:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run --all-extras mypy .

lint: ruff typecheck

test:
	uv run --all-extras $(PYTEST)

test-matrix:
	python3 scripts/test_matrix.py --versions $(PYTHON_VERSIONS) \
		--timeout $(MATRIX_TIMEOUT) --uv-arg=--all-extras -- $(PYTEST)

build:
	uv build
