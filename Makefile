.PHONY: check fmt lint test test-rust test-python build wheel clean

check: fmt lint test

fmt:
	cargo fmt --all --check

lint:
	cargo clippy --workspace --all-targets -- -D warnings

test: test-rust test-python

test-rust:
	cargo test --workspace

test-python:
	ruff check python/
	pytest python/microloop/tests/

build:
	cargo build --release -p microloop-core

wheel:
	maturin build --manifest-path python/microloop/Cargo.toml

clean:
	cargo clean
	rm -rf python/microloop/target
