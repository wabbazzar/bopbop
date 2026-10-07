"""BopBop's original Pearl character as pre-rendered ANSI terminal art."""
from __future__ import annotations

import pathlib
import re
import shutil
import sys
import time

ART_DIR = pathlib.Path(__file__).resolve().parent.parent / "assets" / "cli"
SGR = re.compile(r"\x1b\[[0-9;]*m")
WAVE = ("rest", "low", "left", "right", "left", "right", "low", "rest")


def _width(line: str) -> int:
    return len(SGR.sub("", line))


def _text(value: str, width: int, color: str = "38;2;165;210;224") -> str:
    if len(value) > width:
        value = value[:max(0, width - 1)] + "…"
    return f"\x1b[{color}m{value}\x1b[0m"


def _layout(model: str, context: int, columns: int, rows: int):
    """Fit the banner without wrapping; short terminals get a compact header."""
    if columns < 26 or rows < 20:
        return None
    size = "wide" if columns >= 80 and rows >= 24 else "compact"
    frames = {
        pose: (ART_DIR / f"pearl-{size}-{pose}.ansi").read_text().splitlines()
        for pose in set(WAVE)
    }
    portrait_width = max(_width(line) for line in frames["rest"])
    side_width = columns - portrait_width - 5
    wordmark = (ART_DIR / "wordmark.ansi").read_text().splitlines()

    def compose(pose: str) -> list[str]:
        portrait = frames[pose]
        if side_width < 26:
            indent = " " * max(0, (columns - portrait_width) // 2)
            return [*(indent + line for line in portrait),
                    "  " + _text("bopbop · LOCAL AGENT", columns - 2),
                    "  " + _text(model, columns - 2),
                    "  " + _text(f"{context:,} token context", columns - 2, "90")]

        right = [""] * len(portrait)
        if side_width >= max(_width(line) for line in wordmark):
            start = 2 if size == "wide" else 0
            right[start:start + len(wordmark)] = wordmark
        else:
            right[1] = _text("bopbop", side_width, "1;36")
        label = 9 if size == "wide" else 6
        right[label] = _text("LOCAL AGENT", side_width, "90")
        right[label + 2] = _text(model, side_width)
        right[label + 3] = _text(f"{context:,} token context", side_width, "90")
        return ["  " + left + " " * (portrait_width - _width(left) + 3) + right[i]
                for i, left in enumerate(portrait)]

    return compose


def show_banner(model: str, context: int, *, color: bool,
                mode: str = "auto", interactive: bool = True) -> bool:
    """Print a static portrait or a short wave; return False for plain fallback."""
    if mode == "off" or not color or (mode == "auto" and not sys.stdout.isatty()):
        return False
    terminal_size = shutil.get_terminal_size((80, 24))
    try:
        compose = _layout(model, context, *terminal_size)
    except (OSError, ValueError):
        return False
    if compose is None:
        return False
    animate = (mode == "auto" and interactive
               and sys.stdin.isatty() and sys.stdout.isatty())
    if not animate:
        print("\n".join(compose("rest")), flush=True)
        return True

    height = 0
    try:
        sys.stdout.write("\x1b[?25l")
        for index, pose in enumerate(WAVE):
            # A resize can make a previous frame wrap. Stop before moving the
            # cursor through lines whose positions are no longer predictable.
            if shutil.get_terminal_size((80, 24)) != terminal_size:
                break
            lines = compose(pose)
            if height:
                sys.stdout.write(f"\x1b[{height}A")
            for line in lines:
                sys.stdout.write("\r\x1b[2K" + line + "\n")
            height = len(lines)
            sys.stdout.flush()
            if index < len(WAVE) - 1:
                time.sleep(0.09)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\x1b[0m\x1b[?25h")
        sys.stdout.flush()
    return True
