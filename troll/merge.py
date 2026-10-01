"""Line-based three-way merge used to replay later commits over an edit.

Unlike git's merge, changes that merely touch *adjacent* lines are merged
cleanly; only truly overlapping changes conflict. That's what you want when
propagating small cleanups (e.g. a reworded comment directly above a line a
later commit modified).
"""

from __future__ import annotations

from dataclasses import dataclass

from .diffmodel import diff_opcodes


@dataclass
class Change:
    i1: int  # base range [i1, i2) replaced by `lines`
    i2: int
    lines: list[str]
    side: str


def _changes(base: list[str], other: list[str], side: str) -> list[Change]:
    return [Change(i1, i2, other[j1:j2], side) for tag, i1, i2, j1, j2 in diff_opcodes(base, other) if tag != "equal"]


def _overlaps(c: Change, lo: int, hi: int) -> bool:
    if c.i1 == c.i2:  # insertion at a point
        return lo < c.i1 < hi or lo == hi == c.i1
    if lo == hi:
        return c.i1 < lo < c.i2
    return c.i1 < hi and lo < c.i2


def merge3(base: list[str], ours: list[str], theirs: list[str]) -> list[str] | None:
    """Merge two edits of `base`. Returns None on conflict."""
    if ours == base or ours == theirs:
        return list(theirs)
    if theirs == base:
        return list(ours)
    changes = sorted(_changes(base, ours, "ours") + _changes(base, theirs, "theirs"), key=lambda c: (c.i1, c.i2, c.side))
    out: list[str] = []
    pos = 0
    i = 0
    while i < len(changes):
        first = changes[i]
        lo, hi = first.i1, first.i2
        group = [first]
        j = i + 1
        while j < len(changes) and _overlaps(changes[j], lo, hi):
            group.append(changes[j])
            lo, hi = min(lo, changes[j].i1), max(hi, changes[j].i2)
            j += 1
        if len({c.side for c in group}) > 1:
            if not (len(group) == 2 and (group[0].i1, group[0].i2, group[0].lines) == (group[1].i1, group[1].i2, group[1].lines)):
                return None
            group = group[:1]  # both sides made the same change
        out.extend(base[pos:lo])
        for c in group:
            out.extend(c.lines)
        pos = hi
        i = j
    out.extend(base[pos:])
    return out


def merge3_bytes(base: bytes, ours: bytes, theirs: bytes) -> bytes | None:
    if b"\0" in base[:8000] or b"\0" in ours[:8000] or b"\0" in theirs[:8000]:
        return ours if base == theirs else theirs if base == ours else None
    dec = lambda b: b.decode("utf-8", errors="surrogateescape").split("\n")  # noqa: E731
    merged = merge3(dec(base), dec(ours), dec(theirs))
    if merged is None:
        return None
    return "\n".join(merged).encode("utf-8", errors="surrogateescape")
