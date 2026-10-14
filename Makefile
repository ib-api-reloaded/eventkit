PYTHON_VERSIONS ?= 3.11 3.12 3.13 3.14
# Per-test limit: a hung test fails by name, with a traceback.
TEST_TIMEOUT ?= 60
# Per-version limit enforced by scripts/test_matrix.py.
MATRIX_TIMEOUT ?= 300
PYTEST := pytest -q -p no:cacheprovider -W error::DeprecationWarning --timeout=$(TEST_TIMEOUT) --timeout-method=thread

.PHONY: help test test-matrix lint

help:
	@echo "Targets:"
	@echo "  test         - run the tests on the default interpreter"
	@echo "  test-matrix  - run the tests on each of PYTHON_VERSIONS, each in"
	@echo "                 its own .venvs/py<version> environment and time-boxed;"
	@echo "                 logs in .venvs/logs/"
	@echo "  lint         - ruff check, ruff format --check, mypy"

test:
	uv run --all-extras $(PYTEST)

test-matrix:
	python3 scripts/test_matrix.py --versions $(PYTHON_VERSIONS) \
		--timeout $(MATRIX_TIMEOUT) --uv-arg=--all-extras -- $(PYTEST)

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy eventkit
