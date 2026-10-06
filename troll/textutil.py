"""Small text helpers: display widths, word motion, bracket matching."""

from __future__ import annotations

import unicodedata

Pos = tuple[int, int]

OPENERS = "([{"
CLOSERS = ")]}"
BRACKET_PAIRS = {"(": ")", "[": "]", "{": "}", ")": "(", "]": "[", "}": "{"}


def char_width(ch: str) -> int:
    """Number of terminal cells used to display a (non-tab) character."""
    o = ord(ch)
    if 32 <= o < 127:
        return 1
    if o < 32 or o == 127:
        return 2  # rendered as ^X
    if unicodedata.combining(ch):
        return 0
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 2
    return 1


def text_width(text: str) -> int:
    """Number of terminal cells used to display `text` (no tabs)."""
    if text.isascii() and text.isprintable():  # the common case, much faster
        return len(text)
    return sum(char_width(c) for c in text)


def char_display(ch: str, col: int, tab_size: int) -> str:
    """Text shown for `ch` when it starts at display column `col`."""
    if ch == "\t":
        return " " * (tab_size - col % tab_size)
    o = ord(ch)
    if o < 32:
        return "^" + chr(o + 64)
    if o == 127:
        return "^?"
    return ch


def advance(ch: str, col: int, tab_size: int) -> int:
    if ch == "\t":
        return col + tab_size - col % tab_size
    return col + char_width(ch)


def display_col(line: str, index: int, tab_size: int) -> int:
    """Display column at which character `index` of `line` starts."""
    col = 0
    for ch in line[:index]:
        col = advance(ch, col, tab_size)
    return col


def display_width(line: str, tab_size: int) -> int:
    return display_col(line, len(line), tab_size)


def index_at_display_col(line: str, target: int, tab_size: int) -> int:
    """Index of the character covering display column `target` (clamped)."""
    col = 0
    for i, ch in enumerate(line):
        nxt = advance(ch, col, tab_size)
        if nxt > target:
            return i
        col = nxt
    return len(line)


def leading_ws(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def is_word_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def next_word_start(lines: list[str], pos: Pos) -> Pos:
    """nano's ^Space: move to the start of the next word."""
    row, col = pos
    line = lines[row]
    # skip the rest of the current word
    while col < len(line) and is_word_char(line[col]):
        col += 1
    while True:
        while col < len(line) and not is_word_char(line[col]):
            col += 1
        if col < len(line):
            return (row, col)
        if row + 1 >= len(lines):
            return (row, len(line))
        row += 1
        col = 0
        line = lines[row]


def prev_word_start(lines: list[str], pos: Pos) -> Pos:
    """nano's M-Space: move to the start of the previous word."""
    row, col = pos
    while True:
        line = lines[row]
        while col > 0 and not is_word_char(line[col - 1]):
            col -= 1
        if col > 0:
            while col > 0 and is_word_char(line[col - 1]):
                col -= 1
            return (row, col)
        if row == 0:
            return (0, 0)
        row -= 1
        col = len(lines[row])


def _chars_forward(lines: list[str], row: int, col: int):
    while row < len(lines):
        line = lines[row]
        while col < len(line):
            yield row, col, line[col]
            col += 1
        row += 1
        col = 0


def _chars_backward(lines: list[str], row: int, col: int):
    while row >= 0:
        line = lines[row]
        col = min(col, len(line) - 1)
        while col >= 0:
            yield row, col, line[col]
            col -= 1
        row -= 1
        if row >= 0:
            col = len(lines[row]) - 1


def find_matching_bracket(lines: list[str], pos: Pos) -> Pos | None:
    """Find the bracket matching the one at `pos` (or just before it)."""
    row, col = pos
    line = lines[row]
    if not (col < len(line) and line[col] in BRACKET_PAIRS):
        if col > 0 and line[col - 1] in BRACKET_PAIRS:
            col -= 1
        else:
            return None
    ch = line[col]
    other = BRACKET_PAIRS[ch]
    chars = _chars_forward if ch in OPENERS else _chars_backward
    depth = 0
    for r, c, x in chars(lines, row, col):
        if x == ch:
            depth += 1
        elif x == other:
            depth -= 1
            if depth == 0:
                return (r, c)
    return None


def find_enclosing_opener(lines: list[str], pos: Pos) -> Pos | None:
    """Find the nearest unmatched opening bracket before `pos`."""
    depth = {")": 0, "]": 0, "}": 0}
    row, col = pos
    while row >= 0:
        line = lines[row]
        start = col - 1 if col is not None else len(line) - 1
        for c in range(min(start, len(line) - 1), -1, -1):
            ch = line[c]
            if ch in CLOSERS:
                depth[ch] += 1
            elif ch in OPENERS:
                closer = BRACKET_PAIRS[ch]
                if depth[closer] == 0:
                    return (row, c)
                depth[closer] -= 1
        row -= 1
        col = None
    return None


def word_at(line: str, col: int) -> str:
    """The word touching position `col` (just after a word counts), or ""."""
    s = col
    while s > 0 and is_word_char(line[s - 1]):
        s -= 1
    e = col
    while e < len(line) and is_word_char(line[e]):
        e += 1
    return line[s:e]


def wrap_starts(line: str, width: int, tab_size: int) -> list[int]:
    """Indices where the screen rows of a soft-wrapped line start.

    Rows break after the last blank that fits (like nano's `atblanks`), or
    mid-word when a word is longer than a whole row.
    """
    starts = [0]
    if width < 1:
        return starts
    cols = [0] * (len(line) + 1)
    for i, ch in enumerate(line):
        cols[i + 1] = advance(ch, cols[i], tab_size)
    seg = 0
    blank = None  # index after the last blank in the current row
    i = 0
    while i < len(line):
        if cols[i + 1] - cols[seg] > width and i > seg:
            seg = blank if blank is not None and seg < blank <= i else i
            starts.append(seg)
            blank = None
            continue  # re-check character i against the new row
        if line[i] in " \t":
            blank = i + 1
        i += 1
    return starts


def row_of(starts: list[int], col: int) -> int:
    """Which soft-wrapped row (index into `starts`) contains `col`."""
    k = 0
    while k + 1 < len(starts) and starts[k + 1] <= col:
        k += 1
    return k
