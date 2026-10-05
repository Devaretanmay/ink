PYTHON ?= python3
PLATFORM ?= darwin
ARCH ?= arm64
INKWAY_BRIDGE ?= ../inkway/server/internal/ink/bridge.py

.PHONY: check fmt-check fmt-fix lint test test-examples wheel clean runtime-release

check: lint test test-examples wheel
	@echo "check: ok"

fmt-check:
	ruff format . --check

fmt-fix:
	ruff format .

lint:
	ruff check .

test:
	$(PYTHON) -m pytest python/ink/tests/

test-examples:
	rm -rf .ink/ci-smoke
	$(PYTHON) -m examples.refund_agent.agent --engine exact --output .ink/ci-smoke --require-lifecycle

wheel:
	$(PYTHON) -m build --wheel --sdist --outdir dist/

clean:
	rm -rf dist build *.egg-info python/ink/*.egg-info .pytest_cache .ink/ci-smoke

runtime-release: ## Package the verified Inkway runtime tree as a versioned Ink release asset
	@test "$(PLATFORM)" = darwin -a "$(ARCH)" = arm64 || (echo "runtime-release supports PLATFORM=darwin ARCH=arm64 only" >&2; exit 1)
	@test -n "$(VERSION)" || (echo "set VERSION=<immutable artifact version>" >&2; exit 1)
	@test -f "$(INKWAY_BRIDGE)" || (echo "Inkway bridge source is required" >&2; exit 1)
	INKWAY_BRIDGE_SOURCE="$(INKWAY_BRIDGE)" node scripts/build-runtime-bundle.mjs
	node scripts/package-runtime-release.mjs --runtime-dir .ink/runtime-bundle --bridge "$(INKWAY_BRIDGE)" --version "$(VERSION)"
