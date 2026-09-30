"""View model: turns editor state into a `Frame` of styled text segments.

Nothing in here talks to the terminal. The curses layer only paints the
frame, so rendering decisions (gutters, folding, ghost lines, highlight
overlays, scrolling) are testable as plain data.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from . import __version__
from .commit_session import STATUS_NAMES
from .prompt import Choice, HelpScreen, Picker, Prompt
from .textutil import char_display, char_width, display_col, find_matching_bracket, index_at_display_col

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


def gutter_width(doc) -> int:
    w = 0
    if doc.settings.line_numbers:
        w = max(3, len(str(len(doc.lines)))) + 1
    if doc.diff is not None:
        w += 3
    return w


def adjust_scroll(doc, height: int, text_width: int) -> None:
    idx = doc.display_index(doc.cursor[0])
    count = doc.display_count()
    if doc.center_pending:
        doc.scroll_row = idx - height // 2
        doc.center_pending = False
    elif idx < doc.scroll_row:
        doc.scroll_row = idx
        # show ghost (removed) lines right above the cursor line too
        rows = doc.layout()
        while rows and doc.scroll_row > 0 and rows[doc.scroll_row - 1].kind == "ghost" and idx - doc.scroll_row < height - 1:
            doc.scroll_row -= 1
    elif idx >= doc.scroll_row + height:
        doc.scroll_row = idx - height + 1
    doc.scroll_row = max(0, min(doc.scroll_row, max(0, count - 1)))
    r, c = doc.cursor
    col = display_col(doc.lines[r], c, doc.settings.tab_size)
    margin = min(8, max(0, text_width // 4))
    if col < doc.scroll_col:
        doc.scroll_col = max(0, col - margin)
    elif col >= doc.scroll_col + text_width:
        doc.scroll_col = col - text_width + 1 + margin
    if col < text_width - 1:
        doc.scroll_col = 0


# ------------------------------------------------------------- text rows


def _line_styles(ed, doc, row: int, upto: int, bracket_cells: set) -> tuple[list[str], list[str | None]]:
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
    for br, bc in bracket_cells:
        if br == row and bc < n and bg[bc] is None:
            bg[bc] = "bracket"
    if doc.settings.show_whitespace:
        stripped = len(line.rstrip())
        for i in range(stripped, n):
            fg[i] = "whitespace"
            if bg[i] is None:
                bg[i] = "trailing"
    return fg, bg


def render_text(ed, doc, row: int, rb: RowBuilder, width: int, base_bg: str | None, bracket_cells: set) -> None:
    line = doc.lines[row]
    tab = doc.settings.tab_size
    left = doc.scroll_col
    upto = index_at_display_col(line, left + width, tab) + 1
    fg, bg = _line_styles(ed, doc, row, upto, bracket_cells)
    show_ws = doc.settings.show_whitespace
    col = 0
    for i in range(min(len(line), upto)):
        ch = line[i]
        disp = char_display(ch, col, tab)
        style = fg[i]
        if ch == "\t" and show_ws:
            disp = "›" + disp[1:]
            style = "whitespace"
        elif ch == " " and show_ws and fg[i] == "whitespace":
            disp = "·"
        elif ord(ch) < 32 or ord(ch) == 127:
            style = "control"
        cw = len(disp) if ch == "\t" or ord(ch) < 32 or ord(ch) == 127 else char_width(ch)
        start, col = col, col + cw
        if col <= left:
            continue
        if start < left:
            disp = " " * (col - left)
        if col > left + width:
            disp = " " * max(0, left + width - start)
            rb.add(disp, style, bg[i] or base_bg)
            break
        rb.add(disp, style, bg[i] or base_bg)


def _bracket_cells(doc) -> set:
    r, c = doc.cursor
    line = doc.lines[r]
    if c < len(line) and line[c] in "()[]{}":
        m = find_matching_bracket(doc.lines, (r, c))
        if m is not None:
            return {(r, c), m}
    return set()


def doc_rows(ed, doc, height: int, width: int) -> tuple[list[list[Seg]], tuple[int, int] | None]:
    gw = gutter_width(doc)
    text_w = max(1, width - gw)
    adjust_scroll(doc, height, text_w)
    layout = doc.layout()
    diff = doc.diff.get(doc.buffer) if doc.diff is not None else None
    num_w = max(3, len(str(len(doc.lines)))) if doc.settings.line_numbers else 0
    brackets = _bracket_cells(doc)
    out: list[list[Seg]] = []
    cursor = None
    for i in range(height):
        idx = doc.scroll_row + i
        rb = RowBuilder(width)
        if layout is None:
            if idx >= len(doc.lines):
                out.append(rb.row())
                continue
            kind, row, text, count = "line", idx, "", 0
        else:
            if idx >= len(layout):
                out.append(rb.row())
                continue
            item = layout[idx]
            kind, row, text, count = item.kind, item.row, item.text, item.count
        if kind == "line":
            base_bg = "add" if diff is not None and row in diff.added else None
            if num_w:
                cur = row == doc.cursor[0]
                rb.add(f"{row + 1:>{num_w}}", "gutter.current" if cur else "gutter", None)
            if diff is not None:
                edited = row in diff.edited or row in diff.edit_deletions
                rb.add("*" if edited else " ", "gutter.edit", None)
                rb.add("+" if row in diff.added else " ", "gutter.add", None)
            if gw:
                rb.add(" ", "gutter", None)
            render_text(ed, doc, row, rb, text_w, base_bg, brackets)
            if base_bg:
                rb.pad("text", base_bg)
            if row == doc.cursor[0]:
                r, c = doc.cursor
                x = gw + display_col(doc.lines[r], c, doc.settings.tab_size) - doc.scroll_col
                cursor = (i, max(gw, min(width - 1, x)))
        elif kind == "ghost":
            if num_w:
                rb.add(" " * num_w, "gutter", None)
            rb.add(" -", "gutter.del", None)
            rb.add(" ", "gutter", None)
            expanded = "".join(char_display(ch, 0, 1) if ch != "\t" else " " * doc.settings.tab_size for ch in text)
            rb.add(expanded[doc.scroll_col:], "ghost", "del")
            rb.pad("ghost", "del")
        else:  # fold
            label = f" ⋯ {count} unchanged line{'s' if count != 1 else ''} (M-Z shows all) ⋯"
            rb.add(" " * gw, "fold", None)
            rb.add(label, "fold", None)
        out.append(rb.row())
    return out, cursor


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


# ------------------------------------------------------------ bars


def title_row(ed, width: int) -> list[Seg]:
    rb = RowBuilder(width)
    doc = ed.doc
    if ed.commit is not None:
        s = ed.commit
        left = f" diffedit · commit {s.commit.short} "
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
        left = f" diffedit {__version__} "
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
    if ed.message is not None:
        text = f"[ {ed.message.text} ]"
        if _w(text) > width:
            text = fit(text, width)
        pad = max(0, (width - _w(text)) // 2)
        rb.add(" " * pad, "text", None)
        rb.add(text, "status.error" if ed.message.kind == "error" else "status.info", None)
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


def help_items(ed) -> list[tuple[str, str]]:
    if isinstance(ed.overlay, Picker):
        return HELP_PICKER
    if isinstance(ed.overlay, HelpScreen):
        return HELP_HELP
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
    rows: list[list[Seg]] = [title_row(ed, width)]
    cursor = None
    if isinstance(ed.overlay, HelpScreen):
        body = help_rows(ed.overlay, body_h, width)
    elif ed.in_overview:
        body = overview_rows(ed, body_h, width)
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
    if isinstance(ed.overlay, HelpScreen) or ed.in_overview:
        cursor = cursor if status_x is not None else None
    if ed.settings.help_lines and height >= 8:
        rows.extend(help_bar_rows(help_items(ed), width))
    rows = rows[:height]
    while len(rows) < height:
        rows.append([])
    return Frame(width, height, rows, cursor)
