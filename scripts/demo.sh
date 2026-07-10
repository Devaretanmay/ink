#!/bin/bash
set -e

# ── Microloop Demo ──────────────────────────────────────────────────────────
# One-shot visual demonstration of the $500 Loop of Death and how Microloop
# stops it.
#
# Usage:
#   ./scripts/demo.sh              # Full animated demo
#   ./scripts/demo.sh --fast       # Skip animations
#   ./scripts/demo.sh --bench      # Include Rust benchmark run
# ────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo -e "\033[96m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m"
echo -e "\033[96m  🔄  Microloop Demo  🔄\033[0m"
echo -e "\033[96m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m"
echo ""

# Run the Python demo
python3 "$SCRIPT_DIR/demo_loop.py" "$@"

# Optional: run Rust benchmarks too
if [[ "$*" == *"--bench"* ]]; then
    echo ""
    echo -e "\033[96m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m"
    echo -e "\033[96m  📊  Running Rust Benchmarks  📊\033[0m"
    echo -e "\033[96m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m"
    echo ""
    cd "$PROJECT_DIR"
    cargo run --release --bin microloop-benchmark
fi
