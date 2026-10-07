PYTHON ?= python3

.PHONY: check fmt-check fmt-fix lint test test-examples hygiene wheel clean

check: hygiene lint test test-examples wheel
	@echo "check: ok"

fmt-check:
	ruff format . --check

fmt-fix:
	ruff format .

lint:
	ruff check .

hygiene:
	$(PYTHON) scripts/check-repo-hygiene.py
	$(PYTHON) scripts/check-brand-hygiene.py
	$(PYTHON) scripts/check-product-hygiene.py

test:
	$(PYTHON) -m pytest python/ink/tests/

test-examples:
	rm -rf .ink/ci-smoke
	$(PYTHON) -m examples.refund_agent.agent --engine exact --output .ink/ci-smoke --require-lifecycle

wheel:
	$(PYTHON) -m build --wheel --sdist --outdir dist/

clean:
	rm -rf dist build *.egg-info python/ink/*.egg-info .pytest_cache .ink/ci-smoke
