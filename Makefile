.PHONY: check fmt fmt-fix lint test test-rust test-python test-examples perf build wheel clean

# One command that must pass before anything is published.
check: fmt lint test test-examples wheel
	@echo "check: ok"

fmt:
	cargo fmt --all --check

fmt-fix:
	cargo fmt --all

lint:
	cargo clippy --workspace --all-targets -- -D warnings
	ruff check .

test: test-rust test-python

test-rust:
	cargo test --workspace

test-python:
	pytest python/microloop/tests/

# Both examples are deterministic and offline. Running them in CI keeps the
# documented public API honest and pins the adaptive example's result: it asserts
# that intervening as fast as a run stalls loses a task that doing nothing wins.
test-examples:
	python examples/coding-agent/agent.py
	python examples/adaptive-coding-agent/agent.py

# Reproduces the per-step cost and memory figures quoted in docs/legacy/README-v0.3.md.
perf:
	python benchmarks/perf.py

build:
	cargo build --release -p microloop-core

wheel:
	maturin build --manifest-path python/microloop/Cargo.toml

clean:
	cargo clean
	rm -rf python/microloop/target
