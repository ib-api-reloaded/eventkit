# Every check CI runs is a target here, and CI calls these targets, so a
# local `make lint test` runs exactly what CI runs. Releases and the
# multi-version test run go through releasekit (configured under
# [tool.releasekit] in pyproject.toml).

# Per-test limit: a hung test fails by name, with a traceback.
TEST_TIMEOUT ?= 60
PYTEST := pytest -q -p no:cacheprovider -W error::DeprecationWarning --timeout=$(TEST_TIMEOUT) --timeout-method=thread
RELEASEKIT ?= releasekit
# test-matrix: a subset of the configured Python versions, e.g. "3.13 3.15".
PYTHONS ?=

.PHONY: help ruff typecheck lint test test-matrix build release-check release publish-dry-run publish publish-test

help:
	@echo "Targets:"
	@echo "  ruff            - ruff check and ruff format --check"
	@echo "  typecheck       - mypy over the package and tests"
	@echo "  lint            - ruff + typecheck"
	@echo "  test            - run the tests on the current interpreter"
	@echo "                    (UV_PYTHON selects another one)"
	@echo "  test-matrix     - run the tests on each configured Python version, each"
	@echo "                    in its own .venvs/py<version> environment and time-boxed;"
	@echo "                    logs in .venvs/logs/ (PYTHONS=\"3.13 3.15\" for a subset)"
	@echo "  build           - wheel and sdist into dist/"
	@echo "  release-check   - every reason not to publish this version now"
	@echo "  release         - checks, build from HEAD, smoke test; no upload"
	@echo "  publish-dry-run - everything publish does locally; prints the upload and tag steps"
	@echo "  publish         - publish to PyPI, tag, verify"
	@echo "  publish-test    - the same against TestPyPI"

ruff:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run --all-extras mypy .

lint: ruff typecheck

test:
	uv run --all-extras $(PYTEST)

test-matrix:
	$(RELEASEKIT) matrix $(foreach v,$(PYTHONS),--python=$(v))

build:
	uv build

release-check:
	$(RELEASEKIT) check

release:
	$(RELEASEKIT) release

publish-dry-run:
	$(RELEASEKIT) publish --dry-run

publish:
	$(RELEASEKIT) publish

publish-test:
	$(RELEASEKIT) publish --test-pypi
