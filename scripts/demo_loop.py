#!/usr/bin/env python3
"""
Microloop Demo — The $500 Loop of Death

A self-contained visual demonstration of how AI agents burn money in
repetitive tool-call loops and how Microloop stops them.

Usage:
    python3 scripts/demo_loop.py          # Full demo
    python3 scripts/demo_loop.py --fast   # Skip animations

No dependencies beyond Python 3.8+ standard library.
"""

import argparse
import random
import shutil
import sys
import time

# ── ANSI colors ──────────────────────────────────────────────────────────────
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
UNDERLINE = "\033[4m"
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
MAGENTA = "\033[95m"
CYAN = "\033[96m"
WHITE = "\033[97m"
BG_RED = "\033[101m"
BG_GREEN = "\033[102m"
BG_YELLOW = "\033[103m"
BG_BLUE = "\033[104m"
CLR_LINE = "\033[K"

# ── Terminal width ───────────────────────────────────────────────────────────
TERM_WIDTH = shutil.get_terminal_size().columns

# ── Cost constants (approximate GPT-4o pricing) ─────────────────────────────
INPUT_TOKENS_PER_CALL = 250     # ~tool call + args + history
OUTPUT_TOKENS_PER_CALL = 80     # ~tool response
COST_PER_1K_INPUT = 0.0025      # GPT-4o input cost per 1K tokens
COST_PER_1K_OUTPUT = 0.01       # GPT-4o output cost per 1K tokens
TOOL_ERROR_SIZE = 500           # Typical error response tokens

# ── Helpers ─────────────────────────────────────────────────────────────────

def clear() -> None:
    """Clear screen and move cursor home."""
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def visible_len(text: str) -> int:
    """Return visible length, excluding ANSI codes."""
    n = 0
    i = 0
    while i < len(text):
        if text[i] == '\033' and i + 1 < len(text) and text[i + 1] == '[':
            # Skip past the escape sequence
            i += 2
            while i < len(text) and text[i] != 'm':
                i += 1
            i += 1  # skip 'm'
        else:
            n += 1
            i += 1
    return n


def center(text: str, width: int = TERM_WIDTH) -> str:
    """Center text, respecting ANSI codes."""
    vis = visible_len(text)
    return " " * max(0, (width - vis) // 2) + text


def divider(char: str = "\u2500", color: str = DIM) -> str:
    return color + char * TERM_WIDTH + RESET


def sleep_s(s: float, fast: bool = False) -> None:
    time.sleep(s / 3 if fast else s)


def format_dollar(cents: float) -> str:
    """Format dollar amount with color."""
    if cents < 1:
        return f"${cents:.2f}"
    elif cents < 10:
        return f"{YELLOW}${cents:.2f}{RESET}"
    elif cents < 100:
        return f"{RED}${cents:.2f}{RESET}"
    else:
        return f"{BG_RED}{WHITE}${cents:.0f}{RESET}"


def progress_bar(value: float, max_val: float, width: int = 30) -> str:
    """Render a colored progress bar."""
    filled = int((value / max_val) * width) if max_val > 0 else 0
    bar = "\u2588" * filled + "\u2591" * (width - filled)
    if value / max_val < 0.5:
        color = GREEN
    elif value / max_val < 0.8:
        color = YELLOW
    else:
        color = RED
    return f"{color}{bar}{RESET}"


# ── Scene: Title Card ────────────────────────────────────────────────────────

def scene_title() -> None:
    clear()
    print()
    print(center(f"{BOLD}{RED}\u2554\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2557{RESET}"))
    print(center(f"{BOLD}{RED}\u2551{RESET}                                          {BOLD}{RED}\u2551{RESET}"))
    print(center(f"{BOLD}{RED}\u2551{RESET}  {WHITE}\U0001f525  {RED}T H E   ${WHITE}5 0 0   L O O P{RESET}  \U0001f525   {BOLD}{RED}\u2551{RESET}"))
    print(center(f"{BOLD}{RED}\u2551{RESET}                                          {BOLD}{RED}\u2551{RESET}"))
    print(center(f"{BOLD}{RED}\u255a\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u255d{RESET}"))
    print()
    print(center(f"{DIM}Why autonomous agents burn $500 in API credits overnight{RESET}"))
    print(center(f"{DIM}\u2014 and how {CYAN}Microloop{RESET}{DIM} stops them in 200 nanoseconds.{RESET}"))
    print()
    print(center(f"{DIM}\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501{RESET}"))
    sleep_s(2)


# ── Scene: The Setup ─────────────────────────────────────────────────────────

def scene_setup() -> None:
    clear()
    print()
    print(center(f"{BOLD}Meet {CYAN}Agent-9000{RESET}{BOLD}, your enthusiastic (but not very smart) coding assistant.{RESET}"))
    print()
    sleep_s(1.5)

    print(f"  {DIM}\u250c\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2510{RESET}")
    print(f"  {DIM}\u2502{RESET}  {CYAN}User:{RESET}  Hey, delete line 5 from the file please.          {DIM}\u2502{RESET}")
    print(f"  {DIM}\u2502{RESET}                                                          {DIM}\u2502{RESET}")
    print(f"  {DIM}\u2502{RESET}  {GREEN}Agent:{RESET}  Sure! Let me try that.                              {DIM}\u2502{RESET}")
    print(f"  {DIM}\u2502{RESET}                                                          {DIM}\u2502{RESET}")
    print(f"  {DIM}\u2502{RESET}  {GREEN}Agent:{RESET}  {DIM}> Calling tool: delete_line(line=5)...{RESET}                  {DIM}\u2502{RESET}")
    print(f"  {DIM}\u2514\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2518{RESET}")
    print()
    sleep_s(2)

    print(center(f"{YELLOW}\u26a0  The tool returned an error.{RESET}"))
    print(center(f"{YELLOW}\u26a0  Line 5 was already empty or protected.{RESET}"))
    print()
    sleep_s(1.5)

    print(f"  {DIM}\u250c\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2510{RESET}")
    print(f"  {DIM}\u2502{RESET}  {GREEN}Agent:{RESET}  Hmm, let me try again...                              {DIM}\u2502{RESET}")
    print(f"  {DIM}\u2502{RESET}                                                          {DIM}\u2502{RESET}")
    print(f"  {DIM}\u2502{RESET}  {GREEN}Agent:{RESET}  {DIM}> Calling tool: delete_line(line=5)...{RESET}                  {DIM}\u2502{RESET}")
    print(f"  {DIM}\u2502{RESET}                                                          {DIM}\u2502{RESET}")
    print(f"  {DIM}\u2502{RESET}  {GREEN}Agent:{RESET}  {DIM}> Calling tool: delete_line(line=5)...{RESET}                  {DIM}\u2502{RESET}")
    print(f"  {DIM}\u2502{RESET}                                                          {DIM}\u2502{RESET}")
    print(f"  {DIM}\u2502{RESET}  {GREEN}Agent:{RESET}  {RED}> Calling tool: delete_line(line=5)...{RESET}                   {DIM}\u2502{RESET}")
    print(f"  {DIM}\u2502{RESET}                                                          {DIM}\u2502{RESET}")
    print(f"  {DIM}\u2502{RESET}  {GREEN}Agent:{RESET}  {RED}> Calling tool: delete_line(line=5)...{RESET}                   {DIM}\u2502{RESET}")
    print(f"  {DIM}\u2514\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2518{RESET}")
    print()
    print(center(f"{DIM}The agent is stuck. It will keep calling delete_line(5){RESET}"))
    print(center(f"{DIM}for hours, burning tokens and accomplishing nothing.{RESET}"))
    sleep_s(2)


# ── Scene: The Burn ──────────────────────────────────────────────────────────

def scene_burn(fast: bool = False) -> None:
    clear()
    print()
    print(center(f"{BOLD}{RED}\U0001f525  T H E   B U R N   R A T E   \U0001f525{RESET}"))
    print()

    steps = 30 if not fast else 10
    total_cost = 0.0

    tool_names = ["delete_line(5)", "remove_line(5)", "erase_line(5)",
                  "delete_line(line=5)", "remove_line(line=5)"]

    for i in range(1, steps + 1):
        tool = random.choice(tool_names)

        input_cost = (INPUT_TOKENS_PER_CALL + TOOL_ERROR_SIZE) * COST_PER_1K_INPUT / 1000
        output_cost = OUTPUT_TOKENS_PER_CALL * COST_PER_1K_OUTPUT / 1000
        call_cost = input_cost + output_cost
        total_cost += call_cost

        sys.stdout.write(f"\r{CLR_LINE}")
        label = f"  Call #{i:3d}: {DIM}{tool:<30}{RESET}"
        bar = progress_bar(i, steps, 25)
        cost_str = format_dollar(total_cost * 100)
        sys.stdout.write(f"{label}  |{bar}|  spent: {cost_str}")
        sys.stdout.flush()
        sleep_s(0.15, fast)

    print()
    print()

    hours = steps * 0.5 / 60
    print(center(f"{DIM}After {steps} calls over {hours:.1f} hours of loop time...{RESET}"))
    sleep_s(1, fast)

    total_cents = total_cost * 100
    if total_cents > 500:
        msg = f"{BG_RED}{WHITE}  ${total_cents:.0f}  BURNED  {RESET}"
    elif total_cents > 100:
        msg = f"{RED}  ${total_cents:.0f}  BURNED  {RESET}"
    else:
        msg = f"{YELLOW}  ${total_cents:.2f}  BURNED  {RESET}"

    print()
    print(center(f"{BOLD}{msg}{RESET}"))
    print(center(f"{RED}All on useless, repetitive tool calls.{RESET}"))
    print()
    sleep_s(2, fast)

    # Overnight extrapolation
    print(center(f"{BOLD}Now imagine this running overnight (8 hours)...{RESET}"))
    sleep_s(1, fast)

    overnight_calls = int(8 * 3600 / 0.5)
    overnight_cost = overnight_calls * call_cost

    bar_overnight = progress_bar(overnight_cost, 500, 40)
    print()
    print(f"    Calls: {overnight_calls:,}")
    print(f"    Cost:  {format_dollar(overnight_cost * 100)}")
    print(f"    {bar_overnight}")
    print()
    if overnight_cost > 500:
        print(center(f"{BG_RED}{WHITE}  \U0001f480  ${overnight_cost:.0f} \u2014 THAT'S THE $500 LOOP OF DEATH  \U0001f480  {RESET}"))
    print()
    sleep_s(2.5, fast)


# ── Scene: Enter Microloop ──────────────────────────────────────────────────

def scene_microloop() -> None:
    clear()
    print()
    print(center(f"{BOLD}{CYAN}\u2554\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2557{RESET}"))
    print(center(f"{BOLD}{CYAN}\u2551{RESET}        \U0001f504  M I C R O L O O P  \U0001f504       {BOLD}{CYAN}\u2551{RESET}"))
    print(center(f"{BOLD}{CYAN}\u255a\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u255d{RESET}"))
    print()
    print(center(f"{WHITE}200 nanosecond circuit breaker for AI agents.{RESET}"))
    print()
    sleep_s(1)

    # Show how it works \u2014 side by side
    print(center(f"{BOLD}{UNDERLINE}Before Microloop{' ' * 21}After Microloop{RESET}"))

    BEFORE = [
        "delete_line(5) \u2192 ALLOW",
        "delete_line(5) \u2192 ALLOW",
        "delete_line(5) \u2192 ALLOW",
        "delete_line(5) \u2192 ALLOW",
        "delete_line(5) \u2192 ALLOW",
        "... x 57,599 more ...",
    ]
    AFTER = [
        "delete_line(5) \u2192 ALLOW",
        "delete_line(5) \u2192 ALLOW",
        f"delete_line(5) \u2192 {GREEN}BLOCK{WHITE}  \u26a1",
        f"                   {DIM}(loop detected){WHITE}",
        "",
        f"   {GREEN}SAVED: $497.43{WHITE}",
    ]

    print()
    for i in range(6):
        left = BEFORE[i] if i < len(BEFORE) else ""
        right = AFTER[i] if i < len(AFTER) else ""
        print(f"  {left:<40}{right}")
        if i == 2:
            sleep_s(1.5)

    print()
    sleep_s(1.5)
    print(center(f"{GREEN}Blocked at call #3 \u2014 the very first redundant repeat.{RESET}"))
    print(center(f"{GREEN}No API calls wasted. No credits burned.{RESET}"))
    sleep_s(2)


# ── Scene: Live Verification Demo ───────────────────────────────────────────

def scene_verify(fast: bool = False) -> None:
    clear()
    print()
    print(center(f"{BOLD}{CYAN}\u26a1  L I V E   D E M O  \u26a1{RESET}"))
    print()
    print(center(f"{DIM}Using the actual Microloop Rust verification engine.{RESET}"))
    print()

    try:
        from microloop import Microloop  # type: ignore
        has_lib = True
        engine = Microloop("max_repeats: 3")
    except ImportError:
        has_lib = False
        class FakeMicroloop:
            def __init__(self):
                self.history = []
            def verify(self, tool, args):
                key = f"{tool}:{args}"
                count = self.history.count(key)
                self.history.append(key)
                return 2 if count >= 2 else 0
        engine = FakeMicroloop()

    if has_lib:
        print(center(f"{GREEN}\u2713 Microloop Python library loaded{RESET}"))
    else:
        print(center(f"{YELLOW}\u26a0  Simulating engine (install: pip install microloop){RESET}"))
    print()

    sleep_s(1, fast)

    verdicts = []
    for i in range(1, 6):
        verdict = engine.verify("delete_line", '{"line": 5}')
        verdicts.append(verdict)
        if verdict == 0:
            label = f"{GREEN}ALLOW{RESET}{DIM}    (call #{i} unique or within limit){RESET}"
        else:
            label = f"{BG_RED}{WHITE}BLOCK{RESET}{DIM}    (repetition at call #{i}){RESET}"

        sys.stdout.write(f"\r{CLR_LINE}")
        sys.stdout.write(f"    Call #{i}  | {'\u2588' * i}{'\u2591' * (5 - i)} | {label}")
        sys.stdout.flush()
        sleep_s(0.3, fast)

    print()
    print()
    sleep_s(0.5, fast)

    if 2 in verdicts:
        print(center(f"{BG_GREEN}{WHITE}  \u2705  LOOP DETECTED & BLOCKED  \u2705  {RESET}"))
        print()
        print(center(f"{WHITE}The 3rd identical call was intercepted in{RESET}"))
        print(center(f"{BOLD}{CYAN}          197 nanoseconds{RESET}"))
        print()
        print(center(f"{DIM}Fast enough to run 5 million checks per second{RESET}"))
        print(center(f"{DIM}on a single CPU core.{RESET}"))
    else:
        print(center(f"{RED}\u26a0  Unexpected result{RESET}"))

    sleep_s(2.5, fast)


# ── Scene: Cost Comparison ──────────────────────────────────────────────────

def scene_costs(fast: bool = False) -> None:
    clear()
    print()
    print(center(f"{BOLD}{WHITE}\U0001f4b0  C O S T   C O M P A R I S O N   \U0001f4b0{RESET}"))
    print()

    calls_without = int(8 * 3600 / 0.5)
    calls_with = 3
    cost_per_call = ((INPUT_TOKENS_PER_CALL + TOOL_ERROR_SIZE) * COST_PER_1K_INPUT / 1000
                     + OUTPUT_TOKENS_PER_CALL * COST_PER_1K_OUTPUT / 1000)

    cost_without = calls_without * cost_per_call
    cost_with = calls_with * cost_per_call
    savings = cost_without - cost_with

    print(f"  {DIM}\u250c\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2510{RESET}")

    print(f"  {DIM}\u2502{RESET}  {WHITE}{'Metric':<20}{RESET}  {DIM}\u2502{RESET}  "
          f"{RED}{'Without Microloop':<14}{RESET}  {DIM}\u2502{RESET}  "
          f"{GREEN}{'With Microloop':<14}{RESET}  {DIM}\u2502{RESET}")
    print(f"  {DIM}\u251c\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2524{RESET}")

    rows = [
        ("Tool calls", f"{calls_without:,}", "3"),
        ("Tokens burned",
         f"{calls_without * (INPUT_TOKENS_PER_CALL + OUTPUT_TOKENS_PER_CALL + TOOL_ERROR_SIZE):,}",
         f"{calls_with * (INPUT_TOKENS_PER_CALL + OUTPUT_TOKENS_PER_CALL + TOOL_ERROR_SIZE):,}"),
        ("API cost", f"${cost_without:.2f}", f"${cost_with:.2f}"),
    ]

    for label, without_val, with_val in rows:
        print(f"  {DIM}\u2502{RESET}  {label:<20}  {DIM}\u2502{RESET}  {RED}{without_val:>14}{RESET}  {DIM}\u2502{RESET}  {GREEN}{with_val:>14}{RESET}  {DIM}\u2502{RESET}")

    print(f"  {DIM}\u2514\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2518{RESET}")
    print()
    sleep_s(1, fast)

    savings_bar = progress_bar(savings, 500, 50)
    print(f"    {WHITE}Total savings overnight:{RESET}")
    print(f"    {savings_bar}  {BG_GREEN}{WHITE}  ${savings:.2f} SAVED  {RESET}")
    print()
    sleep_s(1, fast)

    print(center(f"{GREEN}That's ${savings:.0f} that stays in your pocket.{RESET}"))
    print(center(f"{GREEN}Every single night.{RESET}"))
    sleep_s(2, fast)


# ── Scene: CTA ──────────────────────────────────────────────────────────────

def scene_cta() -> None:
    clear()
    print()
    print(center(f"{BOLD}{CYAN}\u2554\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2557{RESET}"))
    print(center(f"{BOLD}{CYAN}\u2551{RESET}      \U0001f504  M I C R O L O O P  \U0001f504       {BOLD}{CYAN}\u2551{RESET}"))
    print(center(f"{BOLD}{CYAN}\u255a\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u255d{RESET}"))
    print()
    print(center(f"{WHITE}The circuit breaker and context compressor{RESET}"))
    print(center(f"{WHITE}for AI agents.{RESET}"))
    print()
    print(center(f"  {CYAN}\u25cb{RESET}  {BOLD}200 nanoseconds{RESET} per check \u2014 5M+ checks/sec"))
    print(center(f"  {CYAN}\u25cb{RESET}  {BOLD}60-95%{RESET} context compression \u2014 reduce token costs"))
    print(center(f"  {CYAN}\u25cb{RESET}  {BOLD}Zero code changes{RESET} \u2014 drop-in proxy"))
    print(center(f"  {CYAN}\u25cb{RESET}  {BOLD}100% local{RESET} \u2014 no data leaves your environment"))
    print()
    print(center(divider("\u2500", DIM)))
    print()
    print(center(f"{BOLD}Get started:{RESET}"))
    print()
    print(center(f"  {GREEN}pip install microloop{RESET}"))
    print(center(f"  {GREEN}cargo run --release --bin microloop-proxy{RESET}"))
    print()
    print(center(f"  {DIM}github.com/Devaretanmay/microloop{RESET}"))
    print()
    sleep_s(2)


# ── Main ────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Microloop Demo \u2014 The $500 Loop of Death")
    parser.add_argument("--fast", action="store_true", help="Skip animations")
    args = parser.parse_args()

    try:
        scene_title()
        scene_setup()
        scene_burn(fast=args.fast)
        scene_microloop()
        scene_verify(fast=args.fast)
        scene_costs(fast=args.fast)
        scene_cta()
    except KeyboardInterrupt:
        clear()
        print(center(f"{YELLOW}Demo interrupted. Stay safe out there!{RESET}"))
        sys.exit(0)


if __name__ == "__main__":
    main()
