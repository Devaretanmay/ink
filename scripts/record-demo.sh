#!/usr/bin/env bash
set -euo pipefail

# Generate a README demo GIF
#
# Options:
#   --vhs         Use VHS (default if installed)
#   --asciinema   Use asciinema + agg (fallback)
#   --bench       Include benchmark output in recording
#
# Requirements:
#   VHS:     brew install vhs
#   asciinema: brew install asciinema
#   agg:       brew install agg

cd "$(dirname "$0")/.."

ASSETS_DIR="assets"
VHS_TAPE="scripts/demo.tape"

mkdir -p "$ASSETS_DIR"

# ── Detect recording method ─────────────────────────────────────────────────
USE_VHS=false
USE_ASCII=false
INCLUDE_BENCH=false

for arg in "$@"; do
    case "$arg" in
        --vhs) USE_VHS=true ;;
        --asciinema) USE_ASCII=true ;;
        --bench) INCLUDE_BENCH=true ;;
    esac
done

if ! $USE_VHS && ! $USE_ASCII; then
    if command -v vhs &>/dev/null; then
        USE_VHS=true
    elif command -v asciinema &>/dev/null && command -v agg &>/dev/null; then
        USE_ASCII=true
    else
        echo "ERROR: No recording tool found. Install one of:"
        echo "  brew install vhs"
        echo "  brew install asciinema agg"
        exit 1
    fi
fi

# ── Record with VHS (preferred) ─────────────────────────────────────────────
if $USE_VHS; then
    if [ ! -f "$VHS_TAPE" ]; then
        echo "ERROR: $VHS_TAPE not found."
        exit 1
    fi

    echo "Recording demo with VHS..."
    echo "  Tape: $VHS_TAPE"
    echo "  Output: $ASSETS_DIR/demo.gif"

    # Build demo if needed
    if [ ! -f "scripts/demo_loop.py" ]; then
        echo "WARNING: demo_loop.py not found. Some features may be missing."
    fi

    vhs "$VHS_TAPE"

    SIZE=$(wc -c < "$ASSETS_DIR/demo.gif" 2>/dev/null || echo "0")
    echo ""
    echo "Done! $ASSETS_DIR/demo.gif generated ($SIZE bytes)"
    exit 0
fi

# ── Record with asciinema + agg (fallback) ──────────────────────────────────
if $USE_ASCII; then
    echo "Recording with asciinema..."

    # Determine what command to record
    if $INCLUDE_BENCH; then
        # Record: build + demo + benchmark
        RECORD_CMD=$(cat <<'CMD'
echo "=== Microloop Demo ==="
echo ""
echo "Running Demo..."
python3 scripts/demo_loop.py --fast
echo ""
echo "Running Benchmarks..."
cargo run --release --bin microloop-benchmark 2>/dev/null || true
CMD
)
    else
        RECORD_CMD="python3 scripts/demo_loop.py --fast"
    fi

    CAST_FILE="/tmp/microloop-demo.cast"
    GIF_FILE="$ASSETS_DIR/demo.gif"

    asciinema rec "$CAST_FILE" \
        --command "$RECORD_CMD" \
        --overwrite \
        --quiet

    echo "Generating GIF..."
    agg --font-size 16 \
        --cols 80 \
        --rows 24 \
        --loop \
        "$CAST_FILE" "$GIF_FILE"

    SIZE=$(wc -c < "$GIF_FILE")
    echo ""
    echo "Done! $GIF_FILE generated ($SIZE bytes)"
    exit 0
fi
