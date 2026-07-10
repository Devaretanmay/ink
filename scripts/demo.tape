# Microloop Demo GIF for README
# Requires: VHS (github.com/charmbracelet/vhs)
#
# On macOS: brew install vhs
# On Linux: go install github.com/charmbracelet/vhs@latest
#
# Usage: vhs scripts/demo.tape

Output assets/demo.gif

Set FontSize 16
Set Width 900
Set Height 640
Set Theme "Aardvark Blue"
Set TypingSpeed 0ms
Set Padding 20
Set Framerate 8
Set BorderRadius 6

# ── Title ──────────────────────────────────────────────────────────────────
Type "./scripts/demo_loop.py --fast" Enter
Sleep 2s

# ── The Setup scene (auto-advances) ─────────────────────────────────────────
Sleep 5s

# ── The Burn Rate scene ─────────────────────────────────────────────────────
Sleep 4s

# ── Overnight extrapolation ─────────────────────────────────────────────────
Sleep 5s

# ── Microloop comparison ────────────────────────────────────────────────────
Sleep 3s

# ── Live Demo scene ─────────────────────────────────────────────────────────
Sleep 4s

# ── Cost Comparison table ───────────────────────────────────────────────────
Sleep 4s

# ── CTA / final screen ──────────────────────────────────────────────────────
Sleep 4s
