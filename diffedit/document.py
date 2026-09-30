"""A document: buffer + cursor + selection + file/language info.

This is where editing operations live (typing with auto-pairing, smart
newlines, cutting lines, indenting, justifying, ...). It knows nothing
about terminals; the editor maps keys onto these methods.
"""

from __future__ import annotations

import bisect
import os
import shutil
from contextlib import contextmanager

from . import autoformat as af
from . import languages
from .buffer import Buffer, order
from .diffmodel import DiffState, Row, build_rows, diff_opcodes
from .highlight import Highlighter
from .settings import Settings
from .textutil import (
    display_col,
    display_width,
    find_matching_bracket,
    index_at_display_col,
    is_word_char,
    leading_ws,
    next_word_start,
    prev_word_start,
)

Pos = tuple[int, int]


class ReadOnlyError(Exception):
    pass


def decode(data: bytes) -> tuple[str, str]:
    """Decode file bytes into (text with \\n newlines, newline style)."""
    text = data.decode("utf-8", errors="surrogateescape")
    newline = "\n"
    crlf = text.count("\r\n")
    if crlf and crlf == text.count("\n"):
        newline = "\r\n"
        text = text.replace("\r\n", "\n")
    return text, newline


def encode(text: str, newline: str) -> bytes:
    if newline != "\n":
        text = text.replace("\n", newline)
    return text.encode("utf-8", errors="surrogateescape")


class Document:
    def __init__(
        self,
        text: str = "",
        path: str | None = None,
        settings: Settings | None = None,
        lang: languages.Language | None = None,
        title: str | None = None,
        detect_indentation: bool = True,
    ):
        self.settings = (settings or Settings()).copy()
        self.buffer = Buffer(text)
        self.path = path
        self.title = title
        self.eol = "\n"  # line ending style on disk
        self.cursor: Pos = (0, 0)
        self.goal_col: int | None = None
        self.mark: Pos | None = None
        self.shift_select = False
        self.scroll_row = 0
        self.scroll_col = 0
        self.center_pending = False
        self.readonly = False
        self.lang = lang or languages.detect(path, self.buffer.lines[0])
        self.highlighter = Highlighter(self.buffer, self.lang)
        self.original_lines = list(self.buffer.lines)
        self.diff: DiffState | None = None
        self._layout_key = None
        self._layout: list[Row] | None = None
        self._apply_language_defaults(detect_indentation)

    # ------------------------------------------------------------ creation
    @classmethod
    def from_bytes(cls, data: bytes, path: str | None = None, settings: Settings | None = None, **kw) -> "Document":
        text, newline = decode(data)
        doc = cls(text, path=path, settings=settings, **kw)
        doc.eol = newline
        return doc

    @classmethod
    def from_file(cls, path: str, settings: Settings | None = None) -> "Document":
        if os.path.exists(path):
            with open(path, "rb") as f:
                data = f.read()
            doc = cls.from_bytes(data, path=path, settings=settings)
            if not os.access(path, os.W_OK):
                doc.readonly = True
            return doc
        return cls("", path=path, settings=settings)

    def _apply_language_defaults(self, detect_indentation: bool) -> None:
        if self.lang.tab_size:
            self.settings.tab_size = self.lang.tab_size
        if self.lang.use_tabs is not None:
            self.settings.expand_tabs = not self.lang.use_tabs
        if detect_indentation:
            use_tabs, width = af.detect_indent(self.buffer.lines)
            if use_tabs is not None:
                self.settings.expand_tabs = not use_tabs
            if width and not use_tabs:
                self.settings.tab_size = width

    def set_language(self, lang: languages.Language) -> None:
        self.lang = lang
        self.highlighter.set_language(lang)

    # ---------------------------------------------------------- properties
    @property
    def lines(self) -> list[str]:
        return self.buffer.lines

    @property
    def name(self) -> str:
        if self.title:
            return self.title
        return self.path or "New Buffer"

    @property
    def modified(self) -> bool:
        return self.buffer.modified

    @property
    def changed_from_original(self) -> bool:
        return self.buffer.lines != self.original_lines

    def text(self) -> str:
        return self.buffer.text()

    def to_bytes(self) -> bytes:
        return encode(self.text(), self.eol)

    # ------------------------------------------------------------- cursor
    def set_cursor(self, pos: Pos, keep_goal: bool = False) -> None:
        self.cursor = self.buffer.clamp(pos)
        if not keep_goal:
            self.goal_col = None

    def selection(self) -> tuple[Pos, Pos] | None:
        if self.mark is None:
            return None
        mark = self.buffer.clamp(self.mark)
        if mark == self.cursor:
            return None
        return order(mark, self.cursor)

    def selected_rows(self) -> tuple[int, int]:
        """Rows touched by the selection (or the cursor row)."""
        sel = self.selection()
        if sel is None:
            return self.cursor[0], self.cursor[0]
        (r1, _), (r2, c2) = sel
        if c2 == 0 and r2 > r1:
            r2 -= 1
        return r1, r2

    def set_mark(self) -> None:
        self.mark = self.cursor
        self.shift_select = False

    def clear_mark(self) -> None:
        self.mark = None
        self.shift_select = False

    def _before_move(self, select: bool) -> None:
        self.buffer.seal()
        if select:
            if self.mark is None or not self.shift_select:
                if self.mark is None:
                    self.mark = self.cursor
                self.shift_select = True
        elif self.shift_select:
            self.clear_mark()

    def _cur_display_col(self) -> int:
        r, c = self.cursor
        return display_col(self.lines[r], c, self.settings.tab_size)

    # ----------------------------------------------------------- layout
    def layout(self) -> list[Row] | None:
        """Display rows (with ghost/fold rows) for diff documents, else None."""
        if self.diff is None:
            return None
        side = self.settings.side_by_side
        fold = self.settings.only_changes
        key = (self.buffer.version, fold, self.cursor[0] if fold else None, self.settings.fold_context, side)
        if key != self._layout_key:
            d = self.diff.get(self.buffer)
            self._layout = build_rows(d, len(self.lines), fold, self.settings.fold_context, self.cursor[0], side)
            self._layout_key = key
        return self._layout

    def visible_line_rows(self) -> list[int] | None:
        rows = self.layout()
        if rows is None:
            return None
        return [r.row for r in rows if r.kind == "line"]

    def display_index(self, row: int) -> int:
        """Index of buffer row `row` in the display rows."""
        rows = self.layout()
        if rows is None:
            return row
        for i, r in enumerate(rows):
            if r.kind == "line" and r.row >= row:
                return i
        return max(0, len(rows) - 1)

    def display_count(self) -> int:
        rows = self.layout()
        return len(self.lines) if rows is None else len(rows)

    # ----------------------------------------------------------- movement
    def move_left(self, select: bool = False) -> None:
        self._before_move(select)
        r, c = self.cursor
        if c > 0:
            self.cursor = (r, c - 1)
        elif r > 0:
            self.cursor = (r - 1, len(self.lines[r - 1]))
        self.goal_col = None

    def move_right(self, select: bool = False) -> None:
        self._before_move(select)
        r, c = self.cursor
        if c < len(self.lines[r]):
            self.cursor = (r, c + 1)
        elif r + 1 < len(self.lines):
            self.cursor = (r + 1, 0)
        self.goal_col = None

    def move_vertical(self, n: int, select: bool = False) -> None:
        self._before_move(select)
        r = self.cursor[0]
        rows = self.visible_line_rows()
        if rows is None:
            target = max(0, min(len(self.lines) - 1, r + n))
        else:
            i = bisect.bisect_left(rows, r)
            target = rows[max(0, min(len(rows) - 1, i + n))]
        if self.goal_col is None:
            self.goal_col = self._cur_display_col()
        if target == r and n != 0:
            # at the first/last line: go to the start/end like nano does
            col = 0 if n < 0 else len(self.lines[r])
            if (n < 0 and r == 0) or (n > 0 and r == len(self.lines) - 1):
                self.cursor = (r, col)
                self.goal_col = None
            return
        col = index_at_display_col(self.lines[target], self.goal_col, self.settings.tab_size)
        self.cursor = (target, col)

    def move_up(self, select: bool = False) -> None:
        self.move_vertical(-1, select)

    def move_down(self, select: bool = False) -> None:
        self.move_vertical(1, select)

    def move_home(self, select: bool = False) -> None:
        self._before_move(select)
        r, c = self.cursor
        first = len(leading_ws(self.lines[r]))
        if self.settings.smart_home and c != first:
            self.cursor = (r, first)
        else:
            self.cursor = (r, 0)
        self.goal_col = None

    def move_end(self, select: bool = False) -> None:
        self._before_move(select)
        r = self.cursor[0]
        self.cursor = (r, len(self.lines[r]))
        self.goal_col = None

    def move_word_right(self, select: bool = False) -> None:
        self._before_move(select)
        self.cursor = next_word_start(self.lines, self.cursor)
        self.goal_col = None

    def move_word_left(self, select: bool = False) -> None:
        self._before_move(select)
        self.cursor = prev_word_start(self.lines, self.cursor)
        self.goal_col = None

    def move_top(self, select: bool = False) -> None:
        self._before_move(select)
        self.cursor = (0, 0)
        self.goal_col = None

    def move_bottom(self, select: bool = False) -> None:
        self._before_move(select)
        self.cursor = self.buffer.end()
        self.goal_col = None

    def goto(self, row: int, col: int = 0) -> None:
        self._before_move(False)
        self.set_cursor((row, col))
        self.center_pending = True

    def goto_matching_bracket(self) -> bool:
        m = find_matching_bracket(self.lines, self.cursor)
        if m is None:
            return False
        self._before_move(False)
        self.cursor = m
        self.goal_col = None
        return True

    # ------------------------------------------------------------ editing
    @contextmanager
    def edit(self, merge_key: str | None = None):
        if self.readonly:
            raise ReadOnlyError("File is read-only")
        self.buffer.begin_group(self.cursor, merge_key)
        try:
            yield
        finally:
            self.buffer.end_group(self.cursor)

    def _delete_selection(self) -> bool:
        sel = self.selection()
        if sel is None:
            return False
        self.buffer.delete(*sel)
        self.cursor = sel[0]
        self.clear_mark()
        return True

    def insert_text(self, text: str) -> None:
        """Insert text verbatim (used for pasting and by commands)."""
        with self.edit():
            if self.shift_select:
                self._delete_selection()
            self.cursor = self.buffer.insert(self.cursor, text)
        self.goal_col = None

    def type_char(self, ch: str) -> None:
        """Insert a typed character with auto-pairing and electric indent."""
        key = "type-space" if ch.isspace() else "type"
        with self.edit(merge_key=key):
            if self.shift_select and self.selection():
                sel = self.selection()
                if self.settings.auto_pair and ch in {p[0] for p in self.lang.pairs}:
                    # wrap the selection in the pair
                    close = {p[0]: p[1] for p in self.lang.pairs}[ch]
                    (s, e) = sel
                    self.buffer.insert(e, close)
                    self.buffer.insert(s, ch)
                    self.clear_mark()
                    self.cursor = (e[0], e[1] + (2 if s[0] == e[0] else 1))
                    return
                self._delete_selection()
            r, c = self.cursor
            line = self.lines[r]
            plan = None
            if self.settings.auto_pair:
                context = self.highlighter.context_at(r, c) if self.settings.highlight else None
                plan = af.plan_pair(line, c, ch, self.lang, context)
            if plan and plan[0] == "skip":
                self.cursor = (r, c + 1)
            elif plan and plan[0] == "pair":
                self.buffer.insert((r, c), plan[1])
                self.cursor = (r, c + 1)
            else:
                self.cursor = self.buffer.insert((r, c), ch)
            if self.settings.auto_indent and (ch in ")]}" or ch == ":"):
                new = af.electric_indent(self.lines, r, self.lang, self.settings)
                if new is not None:
                    old = self.lines[r]
                    delta = len(new) - len(old)
                    self.buffer.replace_lines(r, r, [new])
                    self.cursor = (r, max(0, self.cursor[1] + delta))
            if self.settings.hard_wrap and not ch.isspace():
                self._hard_wrap(r)
        self.goal_col = None

    def _hard_wrap(self, r: int) -> None:
        """Break row `r` at the last blank that fits in fill_width (nano's breaklonglines)."""
        line = self.lines[r]
        width, tab = self.settings.fill_width, self.settings.tab_size
        if display_width(line, tab) <= width:
            return
        prefix = af.line_prefix(line, self.lang)
        lo = len(prefix.first)
        b = None
        for i in range(len(line) - 1, lo - 1, -1):
            if line[i] in " \t" and display_col(line, i, tab) <= width:
                b = i
                break
        if b is None or not line[lo:b].strip():
            return  # a single word longer than the line: leave it
        head = line[:b].rstrip(" \t")
        t = b
        while t < len(line) and line[t] in " \t":
            t += 1
        if prefix.kind in ("comment", "quote"):
            rest = prefix.rest
        elif prefix.kind == "list":
            rest = leading_ws(prefix.first) + " " * len(prefix.first.lstrip(" \t"))
        else:
            rest = prefix.first
        c = self.cursor[1]
        self.buffer.replace_lines(r, r, [head, rest + line[t:]])
        if c >= t:
            self.cursor = (r + 1, len(rest) + c - t)
        else:
            self.cursor = (r, min(c, len(head)))

    def _comment_info(self, row: int) -> tuple[int | None, bool | None]:
        """(column where a comment starts, whether the line is a comment line)."""
        if not self.settings.highlight:
            return None, None
        line = self.lines[row]
        first = len(leading_ws(line))
        start = None
        is_comment = False
        for s, e, token in self.highlighter.spans(row):
            if token == "comment":
                if s <= first < max(e, first + 1):
                    is_comment = True
                if start is None:
                    start = s
        return start, is_comment

    def newline(self, raw: bool = False) -> None:
        with self.edit():
            if self.shift_select:
                self._delete_selection()
            r, c = self.cursor
            if raw or not self.settings.auto_indent:
                self.cursor = self.buffer.insert((r, c), "\n")
                return
            comment_start, is_comment = self._comment_info(r)
            context = self.highlighter.context_at(r, c) if self.settings.highlight else None
            plan = af.plan_newline(self.lines[r], c, self.lang, self.settings, comment_start, is_comment)
            if context == "string" and comment_start is None:
                # inside a multi-line string: just keep the indentation
                indent = leading_ws(self.lines[r])[:c]
                plan = af.NewlinePlan([self.lines[r][:c], indent + self.lines[r][c:]], (1, len(indent)))
            self.buffer.replace_lines(r, r, plan.lines)
            self.cursor = (r + plan.cursor[0], plan.cursor[1])
        self.goal_col = None

    def backspace(self) -> None:
        with self.edit(merge_key="backspace"):
            if self._delete_selection():
                return
            r, c = self.cursor
            if c == 0:
                if r == 0:
                    return
                prev_len = len(self.lines[r - 1])
                self.buffer.delete((r - 1, prev_len), (r, 0))
                self.cursor = (r - 1, prev_len)
                return
            line = self.lines[r]
            if self.settings.auto_pair and af.is_empty_pair(line, c, self.lang):
                self.buffer.delete((r, c - 1), (r, c + 1))
                self.cursor = (r, c - 1)
                return
            before = line[:c]
            if self.settings.expand_tabs and before and not before.strip(" ") and len(before) > 1:
                # smart backspace: remove spaces back to the previous indent stop
                width = len(before)
                target = ((width - 1) // self.settings.tab_size) * self.settings.tab_size
                self.buffer.delete((r, target), (r, c))
                self.cursor = (r, target)
                return
            self.buffer.delete((r, c - 1), (r, c))
            self.cursor = (r, c - 1)
        self.goal_col = None

    def delete_forward(self) -> None:
        with self.edit(merge_key="delete"):
            if self._delete_selection():
                return
            r, c = self.cursor
            if c < len(self.lines[r]):
                self.buffer.delete((r, c), (r, c + 1))
            elif r + 1 < len(self.lines):
                self.buffer.delete((r, c), (r + 1, 0))
        self.goal_col = None

    def delete_word_back(self) -> None:
        with self.edit():
            if self._delete_selection():
                return
            start = prev_word_start(self.lines, self.cursor)
            self.buffer.delete(start, self.cursor)
            self.cursor = start

    def delete_word_forward(self) -> None:
        with self.edit():
            if self._delete_selection():
                return
            r, c = self.cursor
            line = self.lines[r]
            e = c
            if e < len(line) and is_word_char(line[e]):
                while e < len(line) and is_word_char(line[e]):
                    e += 1
            else:
                end = next_word_start(self.lines, self.cursor)
                self.buffer.delete(self.cursor, end)
                return
            while e < len(line) and line[e] in " \t":
                e += 1
            self.buffer.delete((r, c), (r, e))

    def tab(self) -> None:
        if self.selection() is not None:
            self.indent_selection()
            return
        with self.edit(merge_key="type"):
            r, c = self.cursor
            if self.settings.expand_tabs:
                col = display_col(self.lines[r], c, self.settings.tab_size)
                n = self.settings.tab_size - col % self.settings.tab_size
                self.cursor = self.buffer.insert((r, c), " " * n)
            else:
                self.cursor = self.buffer.insert((r, c), "\t")
        self.goal_col = None

    # ------------------------------------------------------ line operations
    def _replace_rows(self, first: int, last: int, new: list[str], keep_selection: bool = True) -> None:
        old_cursor_row, old_cursor_col = self.cursor
        old_len = len(self.lines[old_cursor_row]) if first <= old_cursor_row <= last else None
        self.buffer.replace_lines(first, last, new)
        if old_len is not None and old_cursor_row - first < len(new):
            delta = len(self.lines[old_cursor_row]) - old_len
            self.cursor = self.buffer.clamp((old_cursor_row, max(0, old_cursor_col + delta)))
        else:
            self.cursor = self.buffer.clamp(self.cursor)
        if self.mark is not None and keep_selection:
            self.mark = (first, 0)
            end_row = first + len(new) - 1
            self.cursor = (end_row, len(self.lines[end_row]))
            if self.mark == self.cursor:
                self.clear_mark()

    def indent_selection(self) -> None:
        r1, r2 = self.selected_rows()
        with self.edit():
            self._replace_rows(r1, r2, af.indent_lines(self.lines[r1 : r2 + 1], self.settings))

    def unindent_selection(self) -> None:
        r1, r2 = self.selected_rows()
        with self.edit():
            self._replace_rows(r1, r2, af.unindent_lines(self.lines[r1 : r2 + 1], self.settings))

    def toggle_comment(self) -> bool:
        r1, r2 = self.selected_rows()
        new = af.toggle_comment(self.lines[r1 : r2 + 1], self.lang)
        if new is None:
            return False
        with self.edit():
            self._replace_rows(r1, r2, new, keep_selection=self.selection() is not None)
        return True

    def comment_out_rows(self, first: int, last: int, marker: str) -> bool:
        """Comment out rows first..last with `marker`; False if they already are."""
        old = self.lines[first : last + 1]
        if all(line.lstrip().startswith(marker) for line in old if line.strip()):
            return False
        with self.edit():
            self._replace_rows(first, last, af.comment_out(old, marker), keep_selection=False)
        return True

    def justify(self, whole: bool = False) -> bool:
        """Reflow the paragraph at the cursor, the selection, or everything."""
        width = self.settings.fill_width
        tab = self.settings.tab_size
        if whole or self.selection() is not None:
            r1, r2 = (0, len(self.lines) - 1) if whole else self.selected_rows()
            new = af.justify_range(self.lines, r1, r2, self.lang, width, tab)
            if new == self.lines[r1 : r2 + 1]:
                return False
            with self.edit():
                self.buffer.replace_lines(r1, r2, new)
                self.clear_mark()
                end = r1 + len(new) - 1
                self.cursor = (end, len(self.lines[end]))
            return True
        result = af.justify(self.lines, self.cursor[0], self.lang, width, tab)
        if result is None:
            return False
        first, last, new = result
        if new == self.lines[first : last + 1]:
            # already justified: still move past it like nano
            self.set_cursor((min(last + 1, len(self.lines) - 1), 0))
            return True
        with self.edit():
            self.buffer.replace_lines(first, last, new)
            end = first + len(new) - 1
            self.cursor = (min(end + 1, len(self.lines) - 1), 0)
        self.goal_col = None
        return True

    def transform_selection(self, fn) -> bool:
        """Apply `fn(text) -> text` to the selection (or the current line)."""
        sel = self.selection()
        if sel is None:
            r = self.cursor[0]
            sel = ((r, 0), (r, len(self.lines[r])))
        text = self.buffer.get_text(*sel)
        new = fn(text)
        if new == text:
            return False
        with self.edit():
            end = self.buffer.replace(sel[0], sel[1], new)
            if self.mark is not None:
                self.mark = sel[0]
                self.cursor = end
            else:
                self.cursor = self.buffer.clamp(self.cursor)
        return True

    def transform_rows(self, fn) -> bool:
        """Apply `fn(list_of_lines) -> list_of_lines` to the selected rows."""
        r1, r2 = self.selected_rows()
        if self.selection() is None:
            r1, r2 = 0, len(self.lines) - 1
            if self.lines[r2] == "" and r2 > 0:
                r2 -= 1
        old = self.lines[r1 : r2 + 1]
        new = fn(list(old))
        if new == old:
            return False
        with self.edit():
            self.buffer.replace_lines(r1, r2, new)
            self.cursor = self.buffer.clamp(self.cursor)
            self.clear_mark()
        return True

    def duplicate_line(self) -> None:
        r1, r2 = self.selected_rows()
        block = self.lines[r1 : r2 + 1]
        with self.edit():
            self.buffer.insert((r2, len(self.lines[r2])), "\n" + "\n".join(block))
            self.cursor = (self.cursor[0] + len(block), self.cursor[1])
            self.clear_mark()

    def move_lines(self, direction: int) -> bool:
        r1, r2 = self.selected_rows()
        if (direction < 0 and r1 == 0) or (direction > 0 and r2 >= len(self.lines) - 1):
            return False
        with self.edit():
            if direction < 0:
                block = self.lines[r1 - 1 : r2 + 1]
                new = block[1:] + block[:1]
                self.buffer.replace_lines(r1 - 1, r2, new)
            else:
                block = self.lines[r1 : r2 + 2]
                new = block[-1:] + block[:-1]
                self.buffer.replace_lines(r1, r2 + 1, new)
            self.cursor = (self.cursor[0] + direction, self.cursor[1])
            if self.mark is not None:
                self.mark = (self.mark[0] + direction, self.mark[1])
        return True

    def join_lines(self) -> bool:
        r1, r2 = self.selected_rows()
        if r2 == r1:
            r2 = r1 + 1
        if r2 >= len(self.lines):
            return False
        parts = [self.lines[r1].rstrip()]
        for line in self.lines[r1 + 1 : r2 + 1]:
            stripped = line.strip()
            prefix = af.line_prefix(line, self.lang)
            if prefix.kind == "comment" and af.line_prefix(self.lines[r1], self.lang).kind == "comment":
                stripped = prefix.body.strip()
            if stripped:
                parts.append(stripped)
        with self.edit():
            self.buffer.replace_lines(r1, r2, [" ".join(p for p in parts if p) if len(parts) > 1 else parts[0]])
            self.cursor = (r1, len(parts[0]))
            self.clear_mark()
        return True

    def trim_trailing(self, rows: list[int] | None = None) -> int:
        rows = list(range(len(self.lines))) if rows is None else rows
        targets = [r for r in rows if self.lines[r] != self.lines[r].rstrip()]
        if not targets:
            return 0
        with self.edit():
            for r in targets:
                line = self.lines[r]
                self.buffer.delete((r, len(line.rstrip())), (r, len(line)))
            self.cursor = self.buffer.clamp(self.cursor)
        return len(targets)

    # ------------------------------------------------------------ cut/paste
    def cut_line(self) -> str:
        """nano ^K without a mark: remove the current line and return it."""
        r = self.cursor[0]
        with self.edit():
            if r + 1 < len(self.lines):
                text = self.buffer.delete((r, 0), (r + 1, 0))
            else:
                text = self.buffer.delete((r, 0), (r, len(self.lines[r])))
            self.cursor = (min(r, len(self.lines) - 1), 0)
        self.goal_col = None
        return text

    def cut_to_end(self) -> str:
        r, c = self.cursor
        with self.edit():
            if c == len(self.lines[r]) and r + 1 < len(self.lines):
                return self.buffer.delete((r, c), (r + 1, 0))
            return self.buffer.delete((r, c), (r, len(self.lines[r])))

    def cut_selection(self) -> str | None:
        sel = self.selection()
        if sel is None:
            self.clear_mark()
            return None
        with self.edit():
            text = self.buffer.delete(*sel)
            self.cursor = sel[0]
            self.clear_mark()
        return text

    def copy_selection(self) -> str | None:
        sel = self.selection()
        if sel is None:
            return None
        text = self.buffer.get_text(*sel)
        self.clear_mark()
        return text

    # -------------------------------------------------------------- undo
    def undo(self) -> bool:
        if self.readonly:
            raise ReadOnlyError("File is read-only")
        pos = self.buffer.undo()
        if pos is None:
            return False
        self.clear_mark()
        self.set_cursor(pos)
        return True

    def redo(self) -> bool:
        if self.readonly:
            raise ReadOnlyError("File is read-only")
        pos = self.buffer.redo()
        if pos is None:
            return False
        self.clear_mark()
        self.set_cursor(pos)
        return True

    # ------------------------------------------------------------- saving
    def edited_rows(self) -> list[int]:
        rows: list[int] = []
        for tag, _i1, _i2, j1, j2 in diff_opcodes(self.original_lines, self.lines):
            if tag in ("replace", "insert"):
                rows.extend(range(j1, j2))
        return rows

    def prepare_for_save(self) -> None:
        """Apply save-time cleanups (as a single undoable step)."""
        if self.readonly:
            return
        mode = self.settings.trim_trailing
        if mode == "all":
            self.trim_trailing()
        elif mode == "edited":
            # Only lines you touched: never pollute a diff with whitespace noise.
            self.trim_trailing(self.edited_rows())
        lines = self.lines
        if (
            self.settings.final_newline
            and lines[-1] != ""
            and (self.original_lines[-1] == "" or self.original_lines == [""])
        ):
            with self.edit():
                self.buffer.insert(self.buffer.end(), "\n")

    def save(self, path: str | None = None) -> int:
        """Write the document; returns the number of lines written."""
        path = path or self.path
        if not path:
            raise ValueError("No file name")
        self.prepare_for_save()
        data = self.to_bytes()
        if self.settings.backup and os.path.isfile(path):
            shutil.copy2(path, path + "~")
        with open(path, "wb") as f:
            f.write(data)
        if self.path != path:
            self.path = path
            if self.lang is languages.TEXT:
                self.set_language(languages.detect(path, self.lines[0]))
        self.readonly = False
        self.buffer.mark_saved()
        self.original_lines = list(self.lines)
        n = len(self.lines)
        return n - 1 if self.lines[-1] == "" else n

    def reload_from(self, text: str) -> None:
        """Replace the whole text as an undoable edit (e.g. after formatting)."""
        with self.edit():
            end = self.buffer.end()
            self.buffer.replace((0, 0), end, text)
            self.cursor = self.buffer.clamp(self.cursor)
        self.clear_mark()
