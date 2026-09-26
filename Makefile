.PHONY: check fmt fmt-fix lint test test-rust test-python test-examples build wheel clean

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

# The coding-agent example is a deterministic, scripted recovery demo. Running it
# in CI keeps the documented public API honest.
test-examples:
	python examples/coding-agent/agent.py

build:
	cargo build --release -p microloop-core

wheel:
	maturin build --manifest-path python/microloop/Cargo.toml

clean:
	cargo clean
	rm -rf python/microloop/target
