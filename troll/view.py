"""View model: turns editor state into a `Frame` of styled text segments.

Nothing in here talks to the terminal. The curses layer only paints the
frame, so rendering decisions (gutters, folding, ghost lines, highlight
overlays, scrolling) are testable as plain data.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from . import __version__
from .commit_session import STATUS_NAMES
from .prompt import Choice, HelpScreen, Picker, Prompt
from .settings_screen import SettingsScreen
from .textutil import (
    char_display,
    char_width,
    display_col,
    find_matching_bracket,
    index_at_display_col,
    leading_ws,
    row_of,
    word_at,
    wrap_starts,
)

Seg = tuple[str, str, "str | None"]  # (text, fg style, bg style)


@dataclass
class Frame:
    width: int
    height: int
    rows: list[list[Seg]]
    cursor: tuple[int, int] | None

    def text(self) -> str:
        """Plain text of the frame (for tests and debugging)."""
        return "\n".join("".join(seg[0] for seg in row) for row in self.rows)


def _w(text: str) -> int:
    return sum(char_width(c) for c in text)


def fit(text: str, width: int) -> str:
    """Truncate/pad `text` to exactly `width` cells."""
    out = []
    used = 0
    for ch in text:
        cw = char_width(ch)
        if used + cw > width:
            break
        out.append(ch)
        used += cw
    return "".join(out) + " " * (width - used)


def truncate_left(text: str, width: int) -> str:
    if _w(text) <= width:
        return text
    return "…" + text[-(width - 1):] if width > 1 else text[-width:]


class RowBuilder:
    """Accumulates segments, merging neighbours with equal style."""

    def __init__(self, width: int):
        self.width = width
        self.used = 0
        self.segs: list[Seg] = []

    def add(self, text: str, fg: str = "text", bg: str | None = None) -> None:
        if not text or self.used >= self.width:
            return
        room = self.width - self.used
        if _w(text) > room:
            text = fit(text, room)
        self.used += _w(text)
        if self.segs and self.segs[-1][1] == fg and self.segs[-1][2] == bg:
            self.segs[-1] = (self.segs[-1][0] + text, fg, bg)
        else:
            self.segs.append((text, fg, bg))

    def pad(self, fg: str = "text", bg: str | None = None) -> None:
        if self.used < self.width:
            self.add(" " * (self.width - self.used), fg, bg)

    def row(self) -> list[Seg]:
        return self.segs


# ---------------------------------------------------------------- layout


def body_height(ed, height: int) -> int:
    help_rows = 2 if ed.settings.help_lines and height >= 8 else 0
    return max(1, height - 2 - help_rows)


def number_width(doc) -> int:
    if doc.settings.line_numbers or doc.settings.relative_numbers:
        return max(3, len(str(len(doc.lines))))
    return 0


def number_label(doc, row: int, num_w: int) -> str:
    cur = doc.cursor[0]
    if doc.settings.relative_numbers and row != cur:
        n = abs(row - cur)
    elif doc.settings.relative_numbers and not doc.settings.line_numbers:
        n = 0
    else:
        n = row + 1
    return f"{n:>{num_w}}"


def gutter_width(doc) -> int:
    """Width of what doc_rows draws before the text: line number, diff markers
    (edited + added), then a single separating space."""
    w = number_width(doc)
    if doc.diff is not None:
        w += 2
    return w + 1 if w else 0


def _item_height(doc, idx: int, text_width: int) -> int:
    """Screen rows used by display row `idx` when soft wrapping."""
    layout = doc.layout()
    if layout is None:
        row = idx
    elif idx < len(layout) and layout[idx].kind == "line":
        row = layout[idx].row
    else:
        return 1
    if row >= len(doc.lines):
        return 1
    return len(wrap_starts(doc.lines[row], text_width, doc.settings.tab_size))


def last_visible_index(doc, height: int, width: int) -> int:
    """Last display row that fits on screen from doc.scroll_row (soft wrap aware)."""
    top = doc.scroll_row
    if not doc.settings.soft_wrap or (doc.diff is not None and doc.settings.side_by_side):
        return top + max(1, height) - 1
    text_w = max(1, width - gutter_width(doc))
    used, idx = 0, top
    while idx < doc.display_count():
        used += _item_height(doc, idx, text_w)
        if used > height:
            break
        idx += 1
    return max(top, idx - 1)


def adjust_scroll(doc, height: int, text_width: int, wrap: bool = False) -> None:
    idx = doc.display_index(doc.cursor[0])
    count = doc.display_count()
    margin = min(doc.settings.scroll_margin, (height - 1) // 2)
    if doc.center_pending:
        doc.scroll_row = idx - height // 2
        doc.center_pending = False
    elif idx < doc.scroll_row + margin:
        doc.scroll_row = idx - margin
        # show ghost (removed) lines right above the cursor line too
        rows = doc.layout()
        while rows and doc.scroll_row > 0 and rows[doc.scroll_row - 1].kind == "ghost" and idx - doc.scroll_row < height - 1:
            doc.scroll_row -= 1
    elif idx >= doc.scroll_row + height - margin:
        # the margin doesn't push the last line of the file up from the bottom
        doc.scroll_row = max(idx - height + 1, min(idx - height + 1 + margin, count - height))
    doc.scroll_row = max(0, min(doc.scroll_row, max(0, count - 1)))
    r, c = doc.cursor
    if wrap:
        # wrapped lines take several rows: scroll further until the cursor's row fits
        tab = doc.settings.tab_size
        below = margin if idx + margin < count else 0
        need = sum(_item_height(doc, i, text_width) for i in range(doc.scroll_row, idx))
        need += row_of(wrap_starts(doc.lines[r], text_width, tab), c) + 1 + below
        while need > height and doc.scroll_row < idx:
            need -= _item_height(doc, doc.scroll_row, text_width)
            doc.scroll_row += 1
        doc.scroll_col = 0
        return
    col = display_col(doc.lines[r], c, doc.settings.tab_size)
    margin = min(8, max(0, text_width // 4))
    if col < doc.scroll_col:
        doc.scroll_col = max(0, col - margin)
    elif col >= doc.scroll_col + text_width:
        doc.scroll_col = col - text_width + 1 + margin
    if col < text_width - 1:
        doc.scroll_col = 0


# ------------------------------------------------------------- text rows


@dataclass
class Marks:
    """Per-frame highlight inputs shared by every rendered line."""

    brackets: set
    word: re.Pattern | None = None  # other occurrences of the word under the cursor
    search: re.Pattern | None = None  # every match of the last search


def _marks(ed, doc) -> Marks:
    marks = Marks(_bracket_cells(doc))
    r, c = doc.cursor
    if doc.settings.highlight_word and doc.selection() is None:
        word = word_at(doc.lines[r], c)
        if word:
            marks.word = re.compile(r"(?<!\w)" + re.escape(word) + r"(?!\w)")
    marks.search = ed.search_highlight_pattern()
    return marks


def _line_styles(ed, doc, row: int, upto: int, marks: Marks) -> tuple[list[str], list[str | None]]:
    line = doc.lines[row]
    n = min(len(line), upto)
    fg = ["text"] * n
    bg: list[str | None] = [None] * n
    if doc.settings.highlight:
        for s, e, token in doc.highlighter.spans(row):
            if s >= n:
                break
            e = min(e, n)
            fg[s:e] = [token] * (e - s)
    sel = doc.selection()
    if sel is not None:
        (r1, c1), (r2, c2) = sel
        if r1 <= row <= r2:
            s = c1 if row == r1 else 0
            e = c2 if row == r2 else len(line)
            for i in range(s, min(e, n)):
                bg[i] = "selection"
    if ed.highlight_match is not None:
        (mr, ms), (_mr2, me) = ed.highlight_match
        if mr == row:
            for i in range(ms, min(me, n)):
                bg[i] = "match"
    for pattern, style in ((marks.search, "match.other"), (marks.word, "occurrence")):
        if pattern is None:
            continue
        for m in pattern.finditer(line, 0, len(line)):
            if m.start() >= n:
                break
            for i in range(m.start(), min(m.end(), n)):
                if bg[i] is None:
                    bg[i] = style
    for br, bc in marks.brackets:
        if br == row and bc < n and bg[bc] in (None, "occurrence", "match.other"):
            bg[bc] = "bracket"
    if doc.settings.show_whitespace:
        stripped = len(line.rstrip())
        for i in range(stripped, n):
            fg[i] = "whitespace"
            if bg[i] is None:
                bg[i] = "trailing"
    return fg, bg


def _guide(doc) -> int | None:
    """Display column of the guide stripe (0-based), or None."""
    return doc.guide_column - 1 if doc.guide_column > 0 else None


def render_text(ed, doc, row: int, rb: RowBuilder, width: int, base_bg: str | None, marks: Marks) -> None:
    line = doc.lines[row]
    tab = doc.settings.tab_size
    upto = index_at_display_col(line, doc.scroll_col + width, tab) + 1
    fg, bg = _line_styles(ed, doc, row, upto, marks)
    emit_cells(line, fg, bg, rb, width, doc.scroll_col, tab, doc.settings.show_whitespace, base_bg,
               guide=_guide(doc), indent_guides=doc.settings.indent_guides)


def emit_cells(line: str, fg: list[str], bg: list[str | None], rb: RowBuilder, width: int, left: int,
               tab: int, show_ws: bool, base_bg: str | None, guide: int | None = None,
               indent_guides: bool = False) -> None:
    """Draw `line` (styled per character) from display column `left`, `width` cells wide.

    Only the first len(fg) characters are drawn. `guide` is the display column
    of the guide stripe; `indent_guides` marks each indent stop in the leading
    whitespace.
    """
    col = 0
    lead = len(leading_ws(line)) if indent_guides else 0
    for i in range(min(len(line), len(fg))):
        ch = line[i]
        disp = char_display(ch, col, tab)
        style = fg[i]
        cell_bg = bg[i] or base_bg
        if ch == "\t" and show_ws:
            disp = "›" + disp[1:]
            style = "whitespace"
        elif ch == " " and show_ws and fg[i] == "whitespace":
            disp = "·"
        elif i < lead and col % tab == 0:
            disp = "│" + disp[1:]
            style = "indent_guide"
        elif ord(ch) < 32 or ord(ch) == 127:
            style = "control"
        cw = len(disp) if ch == "\t" or ord(ch) < 32 or ord(ch) == 127 else char_width(ch)
        start, col = col, col + cw
        if guide is not None and start <= guide < col and bg[i] is None:
            cell_bg = "guide"
        if col <= left:
            continue
        if start < left:
            disp = " " * (col - left)
        if col > left + width:
            disp = " " * max(0, left + width - start)
            rb.add(disp, style, cell_bg)
            break
        rb.add(disp, style, cell_bg)
    if guide is not None and col <= guide < left + width:
        rb.add(" " * (guide - max(col, left)), "text", base_bg)
        rb.add(" ", "text", "guide")


def _bracket_cells(doc) -> set:
    r, c = doc.cursor
    line = doc.lines[r]
    if c < len(line) and line[c] in "()[]{}":
        m = find_matching_bracket(doc.lines, (r, c))
        if m is not None:
            return {(r, c), m}
    return set()


def _line_bg(doc, diff, row: int) -> str | None:
    """Background of a whole text line: added by the commit, or the cursor line."""
    if diff is not None and row in diff.added:
        return "add"
    if doc.settings.cursor_line and row == doc.cursor[0] and doc.selection() is None:
        return "cursorline"
    return None


def _line_gutter(doc, diff, rb: RowBuilder, row: int, num_w: int, gw: int, first: bool = True) -> None:
    """Line number and diff markers (blank on soft-wrapped continuation rows)."""
    if not first:
        rb.add(" " * gw, "gutter", None)
        return
    if num_w:
        rb.add(number_label(doc, row, num_w), "gutter.current" if row == doc.cursor[0] else "gutter", None)
    if diff is not None:
        edited = row in diff.edited or row in diff.edit_deletions
        rb.add("*" if edited else " ", "gutter.edit", None)
        rb.add("+" if row in diff.added else " ", "gutter.add", None)
    if gw:
        rb.add(" ", "gutter", None)


def doc_rows(ed, doc, height: int, width: int) -> tuple[list[list[Seg]], tuple[int, int] | None]:
    if doc.diff is not None and doc.settings.side_by_side:
        return side_by_side_rows(ed, doc, height, width)
    gw = gutter_width(doc)
    text_w = max(1, width - gw)
    wrap = doc.settings.soft_wrap
    adjust_scroll(doc, height, text_w, wrap)
    layout = doc.layout()
    diff = doc.diff.get(doc.buffer) if doc.diff is not None else None
    num_w = number_width(doc)
    marks = _marks(ed, doc)
    tab = doc.settings.tab_size
    guide = _guide(doc)
    out: list[list[Seg]] = []
    cursor = None
    idx = doc.scroll_row
    while len(out) < height:
        rb = RowBuilder(width)
        if layout is None:
            if idx >= len(doc.lines):
                out.append(rb.row())
                idx += 1
                continue
            kind, row, text, count = "line", idx, "", 0
        else:
            if idx >= len(layout):
                out.append(rb.row())
                idx += 1
                continue
            item = layout[idx]
            kind, row, text, count = item.kind, item.row, item.text, item.count
        idx += 1
        if kind == "line":
            line = doc.lines[row]
            base_bg = _line_bg(doc, diff, row)
            if not wrap:
                _line_gutter(doc, diff, rb, row, num_w, gw)
                render_text(ed, doc, row, rb, text_w, base_bg, marks)
                if base_bg:
                    rb.pad("text", base_bg)
                if row == doc.cursor[0]:
                    x = gw + display_col(line, doc.cursor[1], tab) - doc.scroll_col
                    cursor = (len(out), max(gw, min(width - 1, x)))
                ed.click_map[len(out)] = (row, doc.scroll_col, gw)
                out.append(rb.row())
                continue
            fg, bg = _line_styles(ed, doc, row, len(line), marks)
            starts = wrap_starts(line, text_w, tab)
            cursor_part = row_of(starts, doc.cursor[1]) if row == doc.cursor[0] else None
            for k, s in enumerate(starts):
                if len(out) >= height:
                    break
                e = starts[k + 1] if k + 1 < len(starts) else len(line)
                left = display_col(line, s, tab)
                rb = RowBuilder(width)
                _line_gutter(doc, diff, rb, row, num_w, gw, first=k == 0)
                emit_cells(line, fg[:e], bg[:e], rb, text_w, left, tab, doc.settings.show_whitespace, base_bg,
                           guide=None if guide is None else left + guide, indent_guides=doc.settings.indent_guides)
                if base_bg:
                    rb.pad("text", base_bg)
                if k == cursor_part:
                    x = gw + display_col(line, doc.cursor[1], tab) - left
                    cursor = (len(out), max(gw, min(width - 1, x)))
                ed.click_map[len(out)] = (row, left, gw)
                out.append(rb.row())
        elif kind == "ghost":
            if num_w:
                rb.add(" " * num_w, "gutter", None)
            rb.add(" -", "gutter.del", None)
            rb.add(" ", "gutter", None)
            expanded = "".join(char_display(ch, 0, 1) if ch != "\t" else " " * tab for ch in text)
            rb.add(expanded[doc.scroll_col:], "ghost", "del")
            rb.pad("ghost", "del")
            out.append(rb.row())
        else:  # fold
            label = f" ⋯ {count} unchanged line{'s' if count != 1 else ''} (M-Z shows all) ⋯"
            rb.add(" " * gw, "fold", None)
            rb.add(label, "fold", None)
            out.append(rb.row())
    return out, cursor


def _left_line(ed, doc, diff, row: int, width: int, num_w: int, gw: int, marks: Marks) -> RowBuilder:
    """Gutter + text of a current line (shared by the unified and side-by-side views)."""
    rb = RowBuilder(width)
    base_bg = _line_bg(doc, diff, row)
    _line_gutter(doc, diff, rb, row, num_w, gw)
    render_text(ed, doc, row, rb, max(1, width - gw), base_bg, marks)
    rb.pad("text", base_bg)
    return rb


def _right_line(doc, diff, i: int | None, width: int, num_w: int) -> RowBuilder:
    """Gutter + text of a base (pre-commit) line for the right pane."""
    rb = RowBuilder(width)
    if i is None:
        rb.pad("text", None)
        return rb
    removed = i in diff.removed
    bg = "del" if removed else None
    if num_w:
        rb.add(f"{i + 1:>{num_w}}", "gutter", None)
    rb.add("-" if removed else " ", "gutter.del", None)
    rb.add(" ", "gutter", None)
    line = diff.base[i]
    tab = doc.settings.tab_size
    upto = index_at_display_col(line, doc.scroll_col + width, tab) + 1
    n = min(len(line), upto)
    fg = ["text"] * n
    if doc.settings.highlight:
        for s, e, token in doc.diff.base_highlighter(doc.lang).spans(i):
            if s >= n:
                break
            fg[s:min(e, n)] = [token] * (min(e, n) - s)
    text_w = max(0, width - rb.used)
    emit_cells(line, fg, [None] * n, rb, text_w, doc.scroll_col, tab, doc.settings.show_whitespace, bg)
    rb.pad("text", bg)
    return rb


def side_by_side_rows(ed, doc, height: int, width: int) -> tuple[list[list[Seg]], tuple[int, int] | None]:
    """Commit diff with your editable version on the left and the parent version on the right."""
    left_w = max(8, (width - 1) // 2)
    right_w = max(0, width - left_w - 1)
    numbers = doc.settings.line_numbers or doc.settings.relative_numbers
    num_w = number_width(doc)
    gw = gutter_width(doc)
    diff = doc.diff.get(doc.buffer)
    base_num_w = max(3, len(str(len(diff.base)))) if numbers else 0
    body_h = max(1, height - 1)
    adjust_scroll(doc, body_h, max(1, left_w - gw))
    layout = doc.layout()
    marks = _marks(ed, doc)

    header = RowBuilder(width)
    header.add(fit(" your version (editable)", left_w), "label", None)
    header.add("│", "palette.border", None)
    header.add(fit(" before this commit" if diff.base else " (new file)", right_w), "label", None)
    out: list[list[Seg]] = [header.row()]
    cursor = None
    for i in range(body_h):
        idx = doc.scroll_row + i
        rb = RowBuilder(width)
        item = layout[idx] if idx < len(layout) else None
        if item is not None and item.kind == "fold":
            label = f" ⋯ {item.count} unchanged line{'s' if item.count != 1 else ''} (M-Z shows all) ⋯"
            rb.add(" " * gw, "fold", None)
            rb.add(label, "fold", None)
            out.append(rb.row())
            continue
        if item is not None and item.kind == "line":
            left = _left_line(ed, doc, diff, item.row, left_w, num_w, gw, marks)
            ed.click_map[i + 1] = (item.row, doc.scroll_col, gw)
            if item.row == doc.cursor[0]:
                r, c = doc.cursor
                x = gw + display_col(doc.lines[r], c, doc.settings.tab_size) - doc.scroll_col
                cursor = (i + 1, max(gw, min(left_w - 1, x)))
        else:
            left = RowBuilder(left_w)
            left.pad("text", None)
        right = _right_line(doc, diff, item.right if item is not None else None, right_w, base_num_w)
        for seg in left.row():
            rb.add(*seg)
        rb.add("│", "palette.border", None)
        for seg in right.row():
            rb.add(*seg)
        out.append(rb.row())
    return out, cursor


def definition_rows(ed, panel, height: int, width: int, tab: int) -> list[list[Seg]]:
    """The show-definition panel: a title row, then the definition's lines (soft-wrapped)."""
    body_h = max(0, height - 1)
    panel.scroll = max(0, min(panel.scroll, len(panel.rows) - 1))
    title = RowBuilder(width)
    title.add(fit(" " + panel.title, width), "palette.label", "prompt")
    out = [title.row()]
    numbers = [r.number for r in panel.rows if r.number is not None]
    num_w = len(str(max(numbers))) if numbers else 0
    gw = num_w + 2
    text_w = max(1, width - gw)
    k = panel.scroll
    while len(out) < height and k < len(panel.rows):
        prow = panel.rows[k]
        k += 1
        if prow.number is None:
            rb = RowBuilder(width)
            rb.add(" " + prow.text, "fold", None)
            out.append(rb.row())
            continue
        bg = "cursorline" if prow.current else None
        line = prow.text
        n = len(line)
        fg = ["text"] * n
        if ed.settings.highlight:
            for a, b, token in prow.spans:
                fg[a:min(b, n)] = [token] * (min(b, n) - a)
        starts = wrap_starts(line, text_w, tab)
        for j, st in enumerate(starts):
            if len(out) >= height:
                break
            e = starts[j + 1] if j + 1 < len(starts) else n
            rb = RowBuilder(width)
            label = f" {prow.number:>{num_w}} " if j == 0 else " " * gw
            rb.add(label, "gutter.current" if prow.current else "gutter", bg)
            emit_cells(line, fg[:e], [None] * e, rb, text_w, display_col(line, st, tab), tab, False, bg)
            if bg:
                rb.pad("text", bg)
            out.append(rb.row())
    if k < len(panel.rows) and len(out) == height and height > 1:
        rb = RowBuilder(width)
        rest = len(panel.rows) - k + 1
        rb.add(f" ↓ {rest} more line{'s' if rest != 1 else ''} (M-PgDn)", "fold", None)
        out[-1] = rb.row()
    while len(out) < height:
        out.append([])
    return out


def panel_width(width: int) -> int:
    """Columns taken by the definition panel (0 if the screen is too narrow)."""
    if width < 50:
        return 0
    return min(max(30, width * 2 // 5), width - 25)


def join_columns(left: list[Seg], left_w: int, right: list[Seg], width: int) -> list[Seg]:
    lb = RowBuilder(left_w)
    for seg in left:
        lb.add(*seg)
    lb.pad()
    rb = RowBuilder(width)
    for seg in lb.row():
        rb.add(*seg)
    rb.add("│", "palette.border", None)
    for seg in right:
        rb.add(*seg)
    return rb.row()


# --------------------------------------------------------- commit overview


def _fmt_date(date: str) -> str:
    try:
        ts, tz = date.split()
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(int(ts))) + f" {tz}"
    except ValueError:
        return date


def overview_rows(ed, height: int, width: int) -> list[list[Seg]]:
    s = ed.commit
    c = s.commit
    lines: list[list[Seg]] = []

    def line(*parts: tuple[str, str], bg: str | None = None):
        rb = RowBuilder(width)
        for text, style in parts:
            rb.add(text, style, bg)
        if bg:
            rb.pad("text", bg)
        lines.append(rb.row())

    line((" commit ", "label"), (c.sha, "diff_hunk"))
    line((" Author: ", "label"), (f"{c.author.name} <{c.author.email}>", "text"), ("   ", "text"), (_fmt_date(c.author.date), "comment"))
    line()
    for n, msg_line in enumerate(c.message.rstrip("\n").split("\n")[:4]):
        line(("     " + msg_line, "heading" if n == 0 else "text"))
    line()
    first_entry = len(lines)
    name_w = max([len(e.label) for e in s.entries] + [20])
    name_w = min(name_w, max(20, width - 34))
    for i, e in enumerate(s.entries):
        sel = i == s.selected
        bg = "selection" if sel else None
        pointer = " ▸ " if sel else "   "
        if e.kind == "message":
            state = "edited" if e.changed else ""
            line((pointer, "label"), ("✎  ", "keyword"), (fit("Commit message", name_w), "text"), ("  " + state, "gutter.edit"), bg=bg)
            continue
        status = e.status or " "
        style = {"A": "diff_add", "D": "diff_del", "M": "diff_hunk", "R": "keyword"}.get(status, "text")
        label = e.label if not e.old_path or e.old_path == e.label else f"{e.old_path} → {e.label}"
        parts = [(pointer, "label"), (f"{status}  ", style), (fit(truncate_left(label, name_w), name_w), "text" if e.editable else "comment")]
        if e.editable:
            a, r = s.original_stats(e)
            parts.append((f"  +{a:<4}", "diff_add"))
            parts.append((f"-{r:<4}", "diff_del"))
            if e.changed:
                na, nr = s.stats(e)
                parts.append(("  edited", "gutter.edit"))
                parts.append((f" → +{na} -{nr}", "comment"))
        else:
            parts.append((f"  ({e.reason}, {STATUS_NAMES.get(status, status)}; not editable)", "comment"))
        line(*parts, bg=bg)
    line()
    if s.problem:
        line(("  ⚠ ", "error"), (s.problem, "error"))
    elif s.descendants:
        n = len(s.descendants)
        line((f"  {n} later commit{'s' if n != 1 else ''} will be replayed on top when you rewrite.", "comment"))
    else:
        line(("  This is the HEAD commit.", "comment"))
    line(("  Enter", "help.key"), (" open   ", "text"), ("^S", "help.key"), (" rewrite commit   ", "text"),
         ("^X", "help.key"), (" leave   ", "text"), ("^T", "help.key"), (" commands", "text"))
    # keep the selected entry visible
    offset = 0
    sel_line = first_entry + s.selected
    if sel_line >= height:
        offset = sel_line - height + 1
    rows = lines[offset : offset + height]
    while len(rows) < height:
        rows.append([])
    return rows


# ------------------------------------------------------------- overlays


def picker_rows(p: Picker, width: int, max_rows: int) -> tuple[list[list[Seg]], tuple[int, int]]:
    items = p.filtered()
    list_h = max(1, min(len(items) or 1, max_rows - 2))
    p.selected = max(0, min(p.selected, max(0, len(items) - 1)))
    p.ensure_visible(list_h)
    rows: list[list[Seg]] = []
    rb = RowBuilder(width)
    title = f" {p.title} "
    rb.add(" › ", "palette.prompt", "palette")
    if p.field.text:
        rb.add(p.field.text, "palette.input", "palette")
    else:
        rb.add(p.placeholder, "palette.placeholder", "palette")
    shown = _w(p.field.text) if p.field.text else _w(p.placeholder)
    room = width - 3 - shown - _w(title)
    if room > 0:
        rb.add(" " * room, "palette", "palette")
        rb.add(title, "palette.title", "palette")
    rb.pad("palette", "palette")
    rows.append(rb.row())
    cursor = (0, min(width - 1, 3 + _w(p.field.text[: p.field.cursor])))
    if not items:
        rb = RowBuilder(width)
        rb.add("   no matches — Enter runs what you typed" if p.allow_free_text else "   no matches", "palette.detail", "palette")
        rb.pad("palette", "palette")
        rows.append(rb.row())
    label_w = min(max((len(i.label) for i in items), default=10) + 2, max(12, width // 2))
    for idx in range(p.scroll, min(len(items), p.scroll + list_h)):
        item = items[idx]
        bg = "palette.sel" if idx == p.selected else "palette"
        rb = RowBuilder(width)
        rb.add("   ", "palette", bg)
        rb.add(fit(item.label, label_w), "palette.label", bg)
        hint = f" {item.hint} " if item.hint else ""
        detail_w = max(0, width - 3 - label_w - _w(hint) - 1)
        rb.add(fit(item.detail, detail_w), "palette.detail", bg)
        rb.add(" ", "palette", bg)
        rb.add(hint, "palette.hint", bg)
        rb.pad("palette", bg)
        rows.append(rb.row())
    rb = RowBuilder(width)
    rb.add("─" * width, "palette.border", None)
    rows.append(rb.row())
    return rows, cursor


def help_rows(h: HelpScreen, height: int, width: int) -> list[list[Seg]]:
    h.height = height
    rows = []
    for i in range(height):
        idx = h.scroll + i
        rb = RowBuilder(width)
        if idx < len(h.lines):
            text = h.lines[idx]
            style = "heading" if text and text == text.upper() and text.strip() and not text.startswith(" ") else "text"
            rb.add(text, style, None)
        rows.append(rb.row())
    return rows


def settings_rows(sc: SettingsScreen, ed, height: int, width: int) -> tuple[list[list[Seg]], tuple[int, int] | None]:
    """The settings panel: grouped options, the selected one's description at the bottom."""
    sc.height = height
    lines: list[list[Seg]] = []
    option_line: dict[int, int] = {}
    cursor_col = None
    label_w = max(len(o.label) for o in sc.options) + 2
    doc = ed.doc
    scope = f"{doc.name} and files opened later" if doc is not None else "files opened from now on"

    rb = RowBuilder(width)
    rb.add(" Settings", "heading")
    rb.add(f"   changes apply to {scope}", "comment")
    lines.append(rb.row())
    section = None
    for i, info in enumerate(sc.options):
        if info.section != section:
            section = info.section
            lines.append([])
            rb = RowBuilder(width)
            rb.add("  " + section.upper(), "label")
            lines.append(rb.row())
        sel = i == sc.selected
        bg = "selection" if sel else None
        rb = RowBuilder(width)
        rb.add(" ▸ " if sel else "   ", "label", bg)
        rb.add(fit(info.label, label_w), "text", bg)
        value = sc.value(info)
        changed = "keyword" if not sc.is_default(info) else "text"
        if sel and sc.editing is not None:
            rb.add("[", "label", bg)
            cursor_col = rb.used + sc.editing.cursor
            rb.add(sc.editing.text or " ", "palette.input", "prompt")
            rb.add("]", "label", bg)
            rb.add(f"  {info.minimum}-{info.maximum}, Enter to accept", "comment", bg)
        elif isinstance(value, bool):
            rb.add("[x] on " if value else "[ ] off", changed, bg)
        else:
            rb.add(f"‹ {sc.display(info)} ›", changed, bg)
        if not sc.is_default(info) and not (sel and sc.editing is not None):
            rb.add("  (changed)", "comment", bg)
        rb.pad("text", bg)
        option_line[i] = len(lines)
        lines.append(rb.row())

    footer: list[list[Seg]] = []
    info = sc.current
    rb = RowBuilder(width)
    rb.add("─" * width, "palette.border")
    footer.append(rb.row())
    rb = RowBuilder(width)
    note = "  (editor-wide)" if info.editor_wide else ""
    rb.add(f" {info.label}: ", "prompt.label")
    rb.add(info.help + note, "text")
    footer.append(rb.row())
    if sc.error:
        rb = RowBuilder(width)
        rb.add(" " + sc.error, "status.error")
        footer.append(rb.row())

    list_h = max(1, height - len(footer))
    target = option_line[sc.selected]
    if target < sc.scroll:
        sc.scroll = max(0, target - 1)  # keep the section heading in view when scrolling up
    elif target >= sc.scroll + list_h:
        sc.scroll = target - list_h + 1
    sc.scroll = max(0, min(sc.scroll, max(0, len(lines) - list_h)))
    visible = lines[sc.scroll : sc.scroll + list_h]
    while len(visible) < list_h:
        visible.append([])
    rows = (visible + footer)[:height]
    cursor = None
    if cursor_col is not None:
        cursor = (target - sc.scroll, min(width - 1, cursor_col))
    return rows, cursor


# ------------------------------------------------------------ bars


def title_row(ed, width: int) -> list[Seg]:
    rb = RowBuilder(width)
    doc = ed.doc
    if ed.commit is not None:
        s = ed.commit
        left = f" troll · commit {s.commit.short} "
        if s.current is None:
            center = s.commit.subject
            right = f"{len(s.entries) - 1} files" + (" · edited" if s.dirty else "")
        else:
            e = s.entry
            center = e.label
            idx = s.editable_indices()
            pos = idx.index(s.current) + 1 if s.current in idx else 0
            a, r = s.stats(e)
            right = f"[{pos}/{len(idx)}] +{a} -{r}" + (" · edited" if e.changed else "")
    else:
        left = f" troll {__version__} "
        center = doc.name if doc else ""
        right = ""
        if doc is not None:
            if doc.readonly:
                right = "[Read Only]"
            elif doc.modified:
                right = "Modified"
            if len(ed.docs) > 1:
                right = f"[{ed.index + 1}/{len(ed.docs)}] " + right
    right = right + " "
    avail = width - _w(left) - _w(right)
    center = truncate_left(center, max(0, avail - 2))
    pad_l = max(0, (width - _w(center)) // 2 - _w(left))
    rb.add(left, "title.brand", "title")
    rb.add(" " * pad_l, "title", "title")
    rb.add(center, "title.name", "title")
    rb.add(" " * max(0, width - rb.used - _w(right)), "title", "title")
    rb.add(right, "title.flag", "title")
    return rb.row()


SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def status_row(ed, width: int) -> tuple[list[Seg], int | None]:
    rb = RowBuilder(width)
    p = ed.prompt
    if isinstance(p, Prompt):
        label = p.label + ": "
        text = p.field.text
        room = max(1, width - _w(label) - 1)
        start = max(0, p.field.cursor - room + 1)
        rb.add(label, "prompt.label", "prompt")
        rb.add(text[start : start + room], "prompt", "prompt")
        rb.pad("prompt", "prompt")
        return rb.row(), min(width - 1, _w(label) + _w(text[start : p.field.cursor]))
    if isinstance(p, Choice):
        rb.add(p.label + " ", "prompt.label", "prompt")
        for keys, label, _ in p.options:
            rb.add(f" {keys[0].upper()} ", "help.key", "prompt")
            rb.add(label + " ", "prompt", "prompt")
        rb.add(" ^C ", "help.key", "prompt")
        rb.add("Cancel", "prompt", "prompt")
        rb.pad("prompt", "prompt")
        return rb.row(), min(width - 1, _w(p.label) + 1)
    if ed.task is not None:
        spinner = SPINNER[int(time.monotonic() * 10) % len(SPINNER)]
        text = f"{spinner} {ed.task.label}..."
        if _w(text) > width:
            text = fit(text, width)
        rb.add(" " * max(0, (width - _w(text)) // 2), "text", None)
        rb.add(text, "status.info", None)
        rb.pad("text", None)
        return rb.row(), None
    if ed.message is not None:
        text = f"[ {ed.message.text} ]"
        if _w(text) > width:
            text = fit(text, width)
        pad = max(0, (width - _w(text)) // 2)
        rb.add(" " * pad, "text", None)
        rb.add(text, "status.error" if ed.message.kind == "error" else "status.info", None)
    doc = ed.doc
    if ed.settings.show_position and doc is not None and ed.overlay is None and not ed.in_overview:
        r, c = doc.cursor
        pos = f" line {r + 1}/{len(doc.lines)}, col {c + 1} "
        if rb.used + _w(pos) <= width:
            rb.add(" " * (width - rb.used - _w(pos)), "text", None)
            rb.add(pos, "label", None)
    return rb.row(), None


HELP_NORMAL = [
    ("^G", "Help"), ("^O", "Write Out"), ("^W", "Where Is"), ("^K", "Cut"), ("^T", "Commands"),
    ("^C", "Location"), ("M-U", "Undo"), ("M-A", "Set Mark"), ("M-]", "To Bracket"), ("M-3", "Comment"),
    ("^X", "Exit"), ("^R", "Read File"), ("^\\", "Replace"), ("^U", "Paste"), ("^J", "Justify"),
    ("^_", "Go To Line"), ("M-E", "Redo"), ("M-6", "Copy"), ("M-W", "Next"), ("M-Q", "Previous"),
]
HELP_COMMIT_FILE = [
    ("^G", "Help"), ("^S", "Rewrite"), ("^W", "Where Is"), ("^K", "Cut"), ("^T", "Commands"),
    ("M-↓", "Next Change"), ("M-Z", "Fold"), ("M-U", "Undo"), ("M-3", "Comment"), ("M->", "Next File"),
    ("^X", "File List"), ("^R", "Read File"), ("^\\", "Replace"), ("^U", "Paste"), ("^J", "Justify"),
    ("M-↑", "Prev Change"), ("^_", "Go To Line"), ("M-E", "Redo"), ("M-A", "Set Mark"), ("M-<", "Prev File"),
]
HELP_OVERVIEW = [
    ("Enter", "Open"), ("^S", "Rewrite Commit"), ("↑↓", "Select"), ("M->", "Next File"), ("^T", "Commands"),
    ("^X", "Leave"), ("^G", "Help"), ("m", "Message"), ("M-<", "Prev File"), ("", ""),
]
HELP_SEARCH = [
    ("^G", "Help"), ("M-C", "Case Sens"), ("M-R", "Reg.exp."), ("M-B", "Backwards"), ("↑", "History"),
    ("^C", "Cancel"), ("Enter", "Search"), ("^\\", "Replace"), ("Tab", "Complete"), ("", ""),
]
HELP_PROMPT = [("Enter", "Accept"), ("Tab", "Complete"), ("↑↓", "History"), ("^C", "Cancel")]
HELP_PICKER = [("↑↓", "Select"), ("Enter", "Run"), ("Tab", "Complete"), ("Esc", "Cancel"), ("type", "to filter")]
HELP_HELP = [("^X", "Close"), ("^Y", "Prev Page"), ("^V", "Next Page"), ("↑↓", "Scroll")]
HELP_SETTINGS = [("↑↓", "Select"), ("Enter", "Change"), ("←→", "Adjust"), ("D", "Default"), ("S", "Save"),
                 ("Esc", "Close")]
HELP_SETTINGS_EDIT = [("0-9", "Type a number"), ("Enter", "Accept"), ("Esc", "Cancel")]


def help_items(ed) -> list[tuple[str, str]]:
    if isinstance(ed.overlay, Picker):
        return HELP_PICKER
    if isinstance(ed.overlay, HelpScreen):
        return HELP_HELP
    if isinstance(ed.overlay, SettingsScreen):
        return HELP_SETTINGS_EDIT if ed.overlay.editing is not None else HELP_SETTINGS
    if isinstance(ed.prompt, Choice):
        items = [(keys[0].upper(), label) for keys, label, _ in ed.prompt.options]
        return items + [("^C", "Cancel")]
    if isinstance(ed.prompt, Prompt):
        return HELP_SEARCH if ed.prompt.options else HELP_PROMPT
    if ed.in_overview:
        return HELP_OVERVIEW
    if ed.commit is not None:
        return HELP_COMMIT_FILE
    return HELP_NORMAL


def help_bar_rows(items: list[tuple[str, str]], width: int) -> list[list[Seg]]:
    """Two nano-style shortcut rows. The first half of `items` is the top row;
    columns are dropped from the right when the terminal is narrow."""
    per_row = (len(items) + 1) // 2
    columns = max(1, min(per_row, width // 16))
    col_w = max(1, width // columns)
    rows = []
    for r in range(2):
        chunk = items[r * per_row : r * per_row + columns]
        rb = RowBuilder(width)
        for key, label in chunk:
            cell = RowBuilder(col_w)
            if key:
                cell.add(key, "help.key", "help.keybg")
                cell.add(" " + label, "help.label", None)
            cell.pad("help.label", None)
            for text, fg, bg in cell.row():
                rb.add(text, fg, bg)
        rows.append(rb.row())
    return rows


# ------------------------------------------------------------- the frame


def build_frame(ed, height: int, width: int) -> Frame:
    width = max(1, width)
    body_h = body_height(ed, height)
    ed.body_height = body_h
    ed.body_width = width
    ed.click_map = {}
    ed.panel_x = None
    rows: list[list[Seg]] = [title_row(ed, width)]
    cursor = None
    settings_cursor = None
    if isinstance(ed.overlay, HelpScreen):
        body = help_rows(ed.overlay, body_h, width)
    elif isinstance(ed.overlay, SettingsScreen):
        body, settings_cursor = settings_rows(ed.overlay, ed, body_h, width)
    elif ed.in_overview:
        body = overview_rows(ed, body_h, width)
    elif ed.doc is not None and ed.definition is not None and panel_width(width):
        pw = panel_width(width)
        left_w = width - pw - 1
        left, cursor = doc_rows(ed, ed.doc, body_h, left_w)
        right = definition_rows(ed, ed.definition, body_h, pw, ed.doc.settings.tab_size)
        body = [join_columns(left[i] if i < len(left) else [], left_w, right[i], width) for i in range(body_h)]
        ed.panel_x = left_w + 1
    elif ed.doc is not None:
        body, cursor = doc_rows(ed, ed.doc, body_h, width)
    else:
        body = [[] for _ in range(body_h)]
    if cursor is not None:
        cursor = (cursor[0] + 1, cursor[1])
    if isinstance(ed.overlay, Picker):
        overlay, pc = picker_rows(ed.overlay, width, max(3, min(body_h, 14)))
        overlay = overlay[:body_h]
        body = overlay + body[len(overlay):]
        cursor = (pc[0] + 1, pc[1])
    rows.extend(body[:body_h])
    while len(rows) < 1 + body_h:
        rows.append([])
    status, status_x = status_row(ed, width)
    rows.append(status)
    if status_x is not None:
        cursor = (len(rows) - 1, status_x)
    if isinstance(ed.overlay, (HelpScreen, SettingsScreen)) or ed.in_overview:
        cursor = cursor if status_x is not None else None
    if settings_cursor is not None:
        cursor = (settings_cursor[0] + 1, settings_cursor[1])
    if ed.task is not None:
        cursor = None
    if ed.settings.help_lines and height >= 8:
        rows.extend(help_bar_rows(help_items(ed), width))
    rows = rows[:height]
    while len(rows) < height:
        rows.append([])
    return Frame(width, height, rows, cursor)
