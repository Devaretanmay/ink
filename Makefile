PYTHON ?= python3

.PHONY: check fmt-check fmt-fix lint test test-examples wheel clean

check: lint test test-examples wheel
	@echo "check: ok"

fmt-check:
	ruff format . --check

fmt-fix:
	ruff format .

lint:
	ruff check .

test:
	$(PYTHON) -m pytest python/microloop/tests/

test-examples:
	rm -rf .microloop/ci-smoke
	$(PYTHON) -m examples.refund_agent.agent --engine exact --output .microloop/ci-smoke --require-lifecycle

wheel:
	$(PYTHON) -m build --wheel --sdist --outdir dist/

clean:
	rm -rf dist build *.egg-info python/microloop/*.egg-info .pytest_cache .microloop/ci-smoke
