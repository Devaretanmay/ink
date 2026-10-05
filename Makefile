PYTHON ?= python3
PLATFORM ?= darwin
ARCH ?= arm64
ISSUWAY_BRIDGE ?= ../multica/server/internal/microloop/bridge.py

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
	$(PYTHON) -m pytest python/microloop/tests/

test-examples:
	rm -rf .microloop/ci-smoke
	$(PYTHON) -m examples.refund_agent.agent --engine exact --output .microloop/ci-smoke --require-lifecycle

wheel:
	$(PYTHON) -m build --wheel --sdist --outdir dist/

clean:
	rm -rf dist build *.egg-info python/microloop/*.egg-info .pytest_cache .microloop/ci-smoke

runtime-release: ## Package the verified Issuway runtime tree as a versioned Microloop release asset
	@test "$(PLATFORM)" = darwin -a "$(ARCH)" = arm64 || (echo "runtime-release supports PLATFORM=darwin ARCH=arm64 only" >&2; exit 1)
	@test -n "$(VERSION)" || (echo "set VERSION=<immutable artifact version>" >&2; exit 1)
	@test -f "$(ISSUWAY_BRIDGE)" || (echo "Issuway bridge source is required" >&2; exit 1)
	node scripts/build-runtime-bundle.mjs
	node scripts/package-runtime-release.mjs --runtime-dir .microloop/runtime-bundle --bridge "$(ISSUWAY_BRIDGE)" --version "$(VERSION)"
