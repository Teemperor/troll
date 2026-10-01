"""Find/replace over a list of lines (matches never span lines)."""

from __future__ import annotations

import re
from dataclasses import dataclass

Pos = tuple[int, int]


@dataclass
class Match:
    start: Pos
    end: Pos
    groups: re.Match | None = None


def compile_query(query: str, regex: bool, case_sensitive: bool) -> re.Pattern:
    """Raises re.error for invalid regular expressions."""
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(query if regex else re.escape(query), flags)


def _line_matches(pattern: re.Pattern, line: str):
    """Non-empty matches in `line` (empty regex matches are useless for editing)."""
    for m in pattern.finditer(line):
        if m.end() > m.start():
            yield m


def find(
    lines: list[str],
    start: Pos,
    pattern: re.Pattern,
    forward: bool = True,
    wrap: bool = True,
    inclusive: bool = False,
) -> tuple[Match, bool] | None:
    """Find the next match after (or before) `start`.

    With `inclusive` a (forward) match starting exactly at `start` counts.
    Returns (match, wrapped) or None.
    """
    n = len(lines)
    row, col = start
    if forward:
        order = [(r, False) for r in range(row, n)]
        if wrap:
            order += [(r, True) for r in range(0, row + 1)]
        for r, wrapped in order:
            for m in _line_matches(pattern, lines[r]):
                if r == row:
                    after = m.start() >= col if inclusive else m.start() > col
                    if after == wrapped:
                        continue
                return Match((r, m.start()), (r, m.end()), m), wrapped
    else:
        order = [(r, False) for r in range(row, -1, -1)]
        if wrap:
            order += [(r, True) for r in range(n - 1, row - 1, -1)]
        for r, wrapped in order:
            found = list(_line_matches(pattern, lines[r]))
            for m in reversed(found):
                if not wrapped and r == row and m.start() >= col:
                    continue
                if wrapped and r == row and m.start() < col:
                    continue
                return Match((r, m.start()), (r, m.end()), m), wrapped
    return None


def find_all(lines: list[str], pattern: re.Pattern, lo: Pos | None = None, hi: Pos | None = None) -> list[Match]:
    out = []
    for r, line in enumerate(lines):
        for m in _line_matches(pattern, line):
            s, e = (r, m.start()), (r, m.end())
            if lo is not None and s < lo:
                continue
            if hi is not None and e > hi:
                continue
            out.append(Match(s, e, m))
    return out


def replacement_text(match: Match, replacement: str, regex: bool) -> str:
    if regex and match.groups is not None:
        return match.groups.expand(replacement)
    return replacement


def count_words(text: str) -> tuple[int, int, int]:
    """(lines, words, characters) like nano's M-D."""
    lines = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
    return lines, len(text.split()), len(text)
