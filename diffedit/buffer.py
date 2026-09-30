"""Line-based text buffer with grouped undo/redo.

All mutations go through `insert` and `delete`; everything else (the
document, auto-formatting, search & replace, ...) is built on top of those
two primitives so that undo and change notification are always correct.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

Pos = tuple[int, int]


@dataclass
class Edit:
    kind: str  # "insert" or "delete"
    start: Pos
    text: str


@dataclass
class UndoGroup:
    edits: list[Edit] = field(default_factory=list)
    cursor_before: Pos = (0, 0)
    cursor_after: Pos = (0, 0)
    merge_key: str | None = None
    sealed: bool = False


def order(a: Pos, b: Pos) -> tuple[Pos, Pos]:
    return (a, b) if a <= b else (b, a)


def end_of_text(start: Pos, text: str) -> Pos:
    """Position right after `text` if it were inserted at `start`."""
    parts = text.split("\n")
    if len(parts) == 1:
        return (start[0], start[1] + len(text))
    return (start[0] + len(parts) - 1, len(parts[-1]))


# Listener signature: (first_changed_row, line_count_delta)
Listener = Callable[[int, int], None]


class Buffer:
    def __init__(self, text: str = ""):
        self.lines: list[str] = text.split("\n")
        self.version = 0
        self.undo_stack: list[UndoGroup] = []
        self.redo_stack: list[UndoGroup] = []
        self._listeners: list[Listener] = []
        self._group: UndoGroup | None = None
        self._group_is_new = False
        self._depth = 0
        self._saved_token: UndoGroup | None = None

    # ------------------------------------------------------------------ basics
    def add_listener(self, fn: Listener) -> None:
        self._listeners.append(fn)

    def _changed(self, row: int, delta: int) -> None:
        self.version += 1
        for fn in self._listeners:
            fn(row, delta)

    def __len__(self) -> int:
        return len(self.lines)

    def text(self) -> str:
        return "\n".join(self.lines)

    def set_text(self, text: str) -> None:
        """Replace everything and forget the history (used when loading)."""
        old = len(self.lines)
        self.lines = text.split("\n")
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._saved_token = None
        self._changed(0, len(self.lines) - old)

    def clamp(self, pos: Pos) -> Pos:
        row = max(0, min(pos[0], len(self.lines) - 1))
        col = max(0, min(pos[1], len(self.lines[row])))
        return (row, col)

    def end(self) -> Pos:
        return (len(self.lines) - 1, len(self.lines[-1]))

    def get_text(self, start: Pos, end: Pos) -> str:
        (r1, c1), (r2, c2) = order(start, end)
        if r1 == r2:
            return self.lines[r1][c1:c2]
        parts = [self.lines[r1][c1:]] + self.lines[r1 + 1 : r2] + [self.lines[r2][:c2]]
        return "\n".join(parts)

    # ------------------------------------------------------------- raw edits
    def _raw_insert(self, pos: Pos, text: str) -> Pos:
        r, c = pos
        line = self.lines[r]
        parts = text.split("\n")
        if len(parts) == 1:
            self.lines[r] = line[:c] + text + line[c:]
        else:
            new = [line[:c] + parts[0], *parts[1:-1], parts[-1] + line[c:]]
            self.lines[r : r + 1] = new
        self._changed(r, len(parts) - 1)
        return end_of_text(pos, text)

    def _raw_delete(self, start: Pos, end: Pos) -> str:
        (r1, c1), (r2, c2) = order(start, end)
        text = self.get_text((r1, c1), (r2, c2))
        if r1 == r2:
            line = self.lines[r1]
            self.lines[r1] = line[:c1] + line[c2:]
        else:
            self.lines[r1 : r2 + 1] = [self.lines[r1][:c1] + self.lines[r2][c2:]]
        self._changed(r1, r1 - r2)
        return text

    # ------------------------------------------------------ recorded edits
    def begin_group(self, cursor: Pos, merge_key: str | None = None) -> None:
        """Start an undo group. Nested calls join the outermost group.

        If `merge_key` equals the key of the most recent (unsealed) group,
        the edits are merged into it - this is how consecutive typing ends
        up as a single undo step.
        """
        if self._depth == 0:
            top = self.undo_stack[-1] if self.undo_stack else None
            if (
                merge_key is not None
                and top is not None
                and not top.sealed
                and top.merge_key == merge_key
                and not self.redo_stack
            ):
                self._group = top
                self._group_is_new = False
            else:
                self._group = UndoGroup(cursor_before=cursor, cursor_after=cursor, merge_key=merge_key)
                self._group_is_new = True
        self._depth += 1

    def end_group(self, cursor: Pos) -> None:
        self._depth -= 1
        if self._depth > 0:
            return
        group, self._group = self._group, None
        if group is None or not group.edits:
            return
        group.cursor_after = cursor
        if self._group_is_new:
            self.undo_stack.append(group)
        self.redo_stack.clear()

    def seal(self) -> None:
        """Prevent further merging into the most recent undo group."""
        if self.undo_stack:
            self.undo_stack[-1].sealed = True

    def _record(self, edit: Edit) -> None:
        if self._group is not None:
            self._group.edits.append(edit)
        else:  # stand-alone edit: its own sealed group
            end = end_of_text(edit.start, edit.text) if edit.kind == "insert" else edit.start
            before = edit.start if edit.kind == "insert" else end_of_text(edit.start, edit.text)
            self.undo_stack.append(UndoGroup([edit], before, end, None, True))
            self.redo_stack.clear()

    def insert(self, pos: Pos, text: str) -> Pos:
        if not text:
            return pos
        pos = self.clamp(pos)
        self._record(Edit("insert", pos, text))
        return self._raw_insert(pos, text)

    def delete(self, start: Pos, end: Pos) -> str:
        start, end = order(self.clamp(start), self.clamp(end))
        if start == end:
            return ""
        text = self._raw_delete(start, end)
        self._record(Edit("delete", start, text))
        return text

    def replace(self, start: Pos, end: Pos, text: str) -> Pos:
        start, end = order(start, end)
        if self.get_text(start, end) == text:
            return end_of_text(start, text)
        self.delete(start, end)
        return self.insert(start, text)

    def replace_lines(self, first: int, last: int, new_lines: list[str]) -> None:
        """Replace lines first..last (inclusive) with `new_lines`."""
        start = (first, 0)
        end = (last, len(self.lines[last]))
        self.replace(start, end, "\n".join(new_lines))

    # ------------------------------------------------------------ undo/redo
    def can_undo(self) -> bool:
        return bool(self.undo_stack)

    def can_redo(self) -> bool:
        return bool(self.redo_stack)

    def undo(self) -> Pos | None:
        if not self.undo_stack:
            return None
        group = self.undo_stack.pop()
        for edit in reversed(group.edits):
            if edit.kind == "insert":
                self._raw_delete(edit.start, end_of_text(edit.start, edit.text))
            else:
                self._raw_insert(edit.start, edit.text)
        group.sealed = True
        self.redo_stack.append(group)
        return group.cursor_before

    def redo(self) -> Pos | None:
        if not self.redo_stack:
            return None
        group = self.redo_stack.pop()
        for edit in group.edits:
            if edit.kind == "insert":
                self._raw_insert(edit.start, edit.text)
            else:
                self._raw_delete(edit.start, end_of_text(edit.start, edit.text))
        self.undo_stack.append(group)
        return group.cursor_after

    # ------------------------------------------------------ modified state
    def mark_saved(self) -> None:
        self.seal()
        self._saved_token = self.undo_stack[-1] if self.undo_stack else None

    @property
    def modified(self) -> bool:
        top = self.undo_stack[-1] if self.undo_stack else None
        return top is not self._saved_token
