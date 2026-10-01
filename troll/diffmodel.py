"""Line diffs for the commit editor.

A commit file is shown as its post-commit contents with the commit's diff
overlaid: lines the commit added are marked, lines it removed appear as
read-only "ghost" lines, and lines you've changed relative to the commit
are flagged separately. Unchanged stretches can be folded away.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field

Opcode = tuple[str, int, int, int, int]


def diff_opcodes(a: list[str], b: list[str]) -> list[Opcode]:
    """SequenceMatcher opcodes, with the common prefix/suffix handled cheaply."""
    n, m = len(a), len(b)
    p = 0
    while p < n and p < m and a[p] == b[p]:
        p += 1
    s = 0
    while s < n - p and s < m - p and a[n - 1 - s] == b[m - 1 - s]:
        s += 1
    ops: list[Opcode] = []
    if p:
        ops.append(("equal", 0, p, 0, p))
    mid_a, mid_b = a[p : n - s], b[p : m - s]
    if mid_a or mid_b:
        if not mid_a:
            ops.append(("insert", p, p, p, m - s))
        elif not mid_b:
            ops.append(("delete", p, n - s, p, p))
        else:
            sm = difflib.SequenceMatcher(None, mid_a, mid_b, autojunk=False)
            for tag, i1, i2, j1, j2 in sm.get_opcodes():
                ops.append((tag, i1 + p, i2 + p, j1 + p, j2 + p))
    if s:
        ops.append(("equal", n - s, n, m - s, m))
    return ops


@dataclass
class LineDiff:
    added: set[int] = field(default_factory=set)  # current rows added by the commit
    ghosts: dict[int, list[str]] = field(default_factory=dict)  # removed lines shown before a row
    hunks: list[tuple[int, int]] = field(default_factory=list)  # [start, end) current rows
    edited: set[int] = field(default_factory=set)  # rows changed relative to the commit
    edit_deletions: set[int] = field(default_factory=set)  # rows before which commit lines were deleted
    added_count: int = 0
    removed_count: int = 0
    opcodes: list[Opcode] = field(default_factory=list)  # base -> current
    base: list[str] = field(default_factory=list)
    removed: set[int] = field(default_factory=set)  # base rows removed by the commit

    def hunk_at(self, row: int) -> int | None:
        for i, (s, e) in enumerate(self.hunks):
            if s <= row < max(e, s + 1):
                return i
        return None


def compute(base: list[str], original: list[str] | None, current: list[str]) -> LineDiff:
    d = LineDiff(base=base)
    d.opcodes = diff_opcodes(base, current)
    for tag, i1, i2, j1, j2 in d.opcodes:
        if tag == "equal":
            continue
        if tag in ("replace", "delete"):
            d.ghosts.setdefault(j1, []).extend(base[i1:i2])
            d.removed.update(range(i1, i2))
            d.removed_count += i2 - i1
        if tag in ("replace", "insert"):
            d.added.update(range(j1, j2))
            d.added_count += j2 - j1
        d.hunks.append((j1, j2))
    if original is not None:
        for tag, i1, i2, j1, j2 in diff_opcodes(original, current):
            if tag in ("replace", "insert"):
                d.edited.update(range(j1, j2))
            elif tag == "delete":
                d.edit_deletions.add(j1)
    return d


def visible_intervals(diff: LineDiff, n: int, context: int, always: tuple[int, ...] = ()) -> list[tuple[int, int]]:
    """Row intervals [start, end) that stay visible when folding."""
    spans = []
    for s, e in diff.hunks:
        spans.append((max(0, s - context), min(n, e + context)))
    for r in diff.edited | diff.edit_deletions:
        spans.append((max(0, r - context), min(n, r + 1 + context)))
    for r in always:
        if 0 <= r < n:
            spans.append((r, r + 1))
    spans.sort()
    merged: list[tuple[int, int]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


@dataclass
class Row:
    """One display row of a document."""

    kind: str  # "line", "ghost" or "fold"
    row: int  # buffer row ("line"), row the ghost precedes, or first folded row
    text: str = ""
    count: int = 0  # number of folded lines
    right: int | None = None  # side-by-side: the base row shown next to it


def _unified_rows(diff: LineDiff, n: int):
    """Removed lines as ghost rows right before the line they were removed at."""
    for r in range(n + 1):
        for text in diff.ghosts.get(r, ()):
            yield Row("ghost", r, text)
        if r < n:
            yield Row("line", r)


def _side_by_side_rows(diff: LineDiff, n: int):
    """Current lines paired with the base lines they replaced.

    Unchanged lines pair 1:1; in a changed block removed and added lines are
    paired in order, and removed lines left over get a row with no current
    line (a ghost anchored after the block).
    """
    base = diff.base
    for tag, i1, i2, j1, j2 in diff.opcodes:
        if tag == "equal":
            for k in range(i2 - i1):
                yield Row("line", j1 + k, right=i1 + k)
            continue
        for k in range(max(i2 - i1, j2 - j1)):
            i = i1 + k if k < i2 - i1 else None
            if k < j2 - j1:
                yield Row("line", j1 + k, right=i)
            else:
                yield Row("ghost", j2, base[i], right=i)


def build_rows(diff: LineDiff, n: int, fold: bool, context: int, cursor_row: int, side_by_side: bool = False) -> list[Row]:
    items = _side_by_side_rows(diff, n) if side_by_side else _unified_rows(diff, n)
    if not fold:
        return list(items)
    intervals = visible_intervals(diff, n, context, (cursor_row,))

    def visible(anchor: int) -> bool:
        if anchor >= n:  # removed at the very end of the file
            return not intervals or intervals[-1][1] >= n
        return any(s <= anchor < e for s, e in intervals)

    rows: list[Row] = []
    hidden = 0
    first = 0
    for item in items:
        if visible(item.row):
            if hidden:
                rows.append(Row("fold", first, count=hidden))
                hidden = 0
            rows.append(item)
        elif item.kind == "line":
            if not hidden:
                first = item.row
            hidden += 1
    if hidden:
        rows.append(Row("fold", first, count=hidden))
    return rows


class DiffState:
    """Diff bookkeeping attached to a document in the commit editor."""

    def __init__(self, base: list[str], original: list[str]):
        self.base = base
        self.original = original
        self._cache_key = None
        self._diff: LineDiff | None = None
        self._base_highlighter = None

    def base_highlighter(self, lang):
        """Syntax highlighter for the base (pre-commit) text, for the side-by-side view."""
        from .buffer import Buffer
        from .highlight import Highlighter

        if self._base_highlighter is None or self._base_highlighter.lang is not lang:
            self._base_highlighter = Highlighter(Buffer("\n".join(self.base)), lang)
        return self._base_highlighter

    def get(self, buffer) -> LineDiff:
        if self._cache_key != buffer.version or self._diff is None:
            self._diff = compute(self.base, self.original, buffer.lines)
            self._cache_key = buffer.version
        return self._diff

    def reset_original(self, lines: list[str]) -> None:
        self.original = list(lines)
        self._cache_key = None
