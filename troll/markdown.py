"""GitHub-flavored markdown → styled, wrapped lines (no curses, no layout beyond wrapping).

`render(text, width)` returns `Line`s: (runs, fill) where runs are
`(text, fg_style, bg_style)` and `fill` is the background to pad the rest of
the row with (code blocks), or None. Like GitHub's comments, single newlines
are line breaks. Handles headings, paragraphs, lists (task lists too),
block quotes, fenced code (highlighted in its language), tables, rules,
inline code/bold/italic/strike/links/@mentions/#refs, and the bits of HTML
PR templates use (comments are dropped, <details>/<summary> flattened).
"""

from __future__ import annotations

import re

from .highlight import Tokenizer
from .languages import get as get_language
from .textutil import char_width, text_width

Run = tuple[str, str, "str | None"]
Line = tuple[list[Run], "str | None"]

TEXT, MUTED = "md.text", "md.muted"
BLOCK_BG, CODE_BG = "md.block", "md.code"

INLINE_RE = re.compile(r"""
    (?P<tick>`+)(?P<code>.+?)(?P=tick)
  | \*\*(?P<b1>.+?)\*\* | (?<!\w)__(?P<b2>.+?)__(?!\w)
  | ~~(?P<strike>.+?)~~
  | (?<![\w*])\*(?P<i1>[^*\s](?:[^*]*?[^*\s])?)\*(?![\w*])
  | (?<![\w_])_(?P<i2>[^_\s](?:[^_]*?[^_\s])?)_(?![\w_])
  | (?P<img>!)?\[(?P<ltext>[^\]]*)\]\((?P<lurl>[^)\s]*)(?:\s+"[^"]*")?\)
  | <(?P<aurl>(?:https?|mailto):[^>\s]+)>
  | (?P<url>\b(?:https?|rdar|ftp)://[^\s<>()]*[^\s<>().,;:!?'"*_])
  | (?P<mention>(?<![\w/@.])@[A-Za-z0-9][\w-]*(?:/[\w.-]+)?)
  | (?P<ref>(?<![\w/&#])\#\d+\b)
""", re.X)

FENCE_RE = re.compile(r"^(\s*)(`{3,}|~{3,})\s*([\w+#.-]*)")
HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
RULE_RE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
QUOTE_RE = re.compile(r"^\s{0,3}>\s?(.*)$")
LIST_RE = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$")
TASK_RE = re.compile(r"^\[([ xX])\]\s+")
TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
HTML_TAGS = ("details|summary|p|div|span|br|sub|sup|img|a|b|i|em|strong|code|kbd|hr|ul|ol|li|table|tr|td|th|"
             "thead|tbody|h[1-6]|pre|blockquote|picture|source|ins|del|small|center|u|s|strike|tt|dl|dt|dd")
TAG_RE = re.compile(rf"</?({HTML_TAGS})\b[^>]*?/?>", re.I)
BULLETS = "•◦▪"


# ------------------------------------------------------------------ inline


def _html(line: str) -> str:
    """Flatten the HTML GitHub would render into plain markdown-ish text."""
    if "<" not in line:
        return line
    line = re.sub(r"<summary\b[^>]*>(.*?)</summary>", r"▸ **\1**", line, flags=re.I)
    line = re.sub(r"</?(b|strong)\b[^>]*>", "**", line, flags=re.I)
    line = re.sub(r"</?(i|em)\b[^>]*>", "*", line, flags=re.I)
    line = re.sub(r"</?(code|kbd|tt)\b[^>]*>", "`", line, flags=re.I)
    line = re.sub(r"<img\b[^>]*?alt=\"([^\"]*)\"[^>]*>", r"[image: \1]", line, flags=re.I)
    return TAG_RE.sub("", line)


def inline(text: str, fg: str = TEXT, bg: str | None = None) -> list[Run]:
    """Runs for one line of inline markdown."""
    out: list[Run] = []
    pos = 0
    for m in INLINE_RE.finditer(text):
        if m.start() > pos:
            out.append((text[pos:m.start()], fg, bg))
        pos = m.end()
        g = m.groupdict()
        if g["code"] is not None:
            code = g["code"]
            if code.startswith(" ") and code.endswith(" ") and code.strip():
                code = code[1:-1]
            out.append((code, "md.codetext", CODE_BG))
        elif g["b1"] is not None or g["b2"] is not None:
            out += inline(g["b1"] or g["b2"], "md.bold" if fg in (TEXT, "md.italic") else fg, bg)
        elif g["strike"] is not None:
            out += inline(g["strike"], "md.strike", bg)
        elif g["i1"] is not None or g["i2"] is not None:
            out += inline(g["i1"] or g["i2"], "md.italic" if fg == TEXT else fg, bg)
        elif g["ltext"] is not None:
            label = g["ltext"] or g["lurl"]
            out.append((f"[image: {label}]" if g["img"] else label, "md.link", bg))
        else:
            out.append((m.group(), "md.mention" if g["mention"] else "md.link", bg))
    if pos < len(text):
        out.append((text[pos:], fg, bg))
    return [r for r in out if r[0]]


def recolor(runs: list[Run], mapping: dict[str, str]) -> list[Run]:
    return [(t, mapping.get(fg, fg), bg) for t, fg, bg in runs]


def runs_width(runs: list[Run]) -> int:
    return sum(text_width(t) for t, _, _ in runs)


# ------------------------------------------------------------------ wrapping


def _split_long(pieces: list[Run], width: int) -> list[list[Run]]:
    """Cut a word wider than `width` into chunks."""
    chunks: list[list[Run]] = [[]]
    used = 0
    for text, fg, bg in pieces:
        buf = ""
        for ch in text:
            cw = char_width(ch)
            if used + cw > width and used > 0:
                if buf:
                    chunks[-1].append((buf, fg, bg))
                chunks.append([])
                buf, used = "", 0
            buf += ch
            used += cw
        if buf:
            chunks[-1].append((buf, fg, bg))
    return chunks


def wrap(runs: list[Run], width: int) -> list[list[Run]]:
    """Word-wrap runs to `width` cells; runs of spaces become one space (non-breaking ones stay)."""
    width = max(1, width)
    words: list[tuple[list[Run], bool]] = []  # (pieces, is_space)
    for text, fg, bg in runs:
        for part in re.findall(r"[ \t\n]+|[^ \t\n]+", text):
            space = part[0] in " \t\n"
            if words and words[-1][1] == space:
                if not space:
                    words[-1][0].append((part, fg, bg))
            else:
                words.append(([(" " if space else part, fg, bg)], space))
    lines: list[list[Run]] = [[]]
    used = 0
    pending: Run | None = None
    for pieces, space in words:
        if space:
            pending = pieces[0] if used else None
            continue
        w = runs_width(pieces)
        if used and used + 1 + w > width:
            lines.append([])
            used, pending = 0, None
        if pending is not None and used:
            lines[-1].append(pending)
            used += 1
        pending = None
        if w > width - used:
            chunks = _split_long(pieces, width)
            for i, chunk in enumerate(chunks):
                if i:
                    lines.append([])
                lines[-1] += chunk
            used = runs_width(lines[-1])
        else:
            lines[-1] += pieces
            used += w
    return lines


def _char_wrap(runs: list[Run], width: int) -> list[list[Run]]:
    """Wrap code: break anywhere, keep spaces."""
    if not runs:
        return [[]]
    return _split_long(runs, max(1, width))


# ------------------------------------------------------------------ blocks


def _code_block(code: list[str], lang_name: str, width: int) -> list[Line]:
    lang = get_language(lang_name) if lang_name else None
    tok = Tokenizer(lang) if lang is not None else None
    state = None
    out: list[Line] = [([], BLOCK_BG)]
    for text in code:
        text = text.replace("\t", "    ")
        if tok is not None:
            spans, state = tok.tokenize(text, state)
            fg = ["md.codetext"] * len(text)
            for s, e, token in spans:
                fg[s:e] = [token] * (min(e, len(text)) - s)
            runs: list[Run] = []
            for i, ch in enumerate(text):
                if runs and runs[-1][1] == fg[i]:
                    runs[-1] = (runs[-1][0] + ch, fg[i], BLOCK_BG)
                else:
                    runs.append((ch, fg[i], BLOCK_BG))
        else:
            runs = [(text, "md.codetext", BLOCK_BG)] if text else []
        for chunk in _char_wrap(runs, width - 2):
            out.append(([(" ", "md.codetext", BLOCK_BG)] + chunk, BLOCK_BG))
    out.append(([], BLOCK_BG))
    return out


def _table(rows: list[str], width: int) -> list[Line] | None:
    def cells(row: str) -> list[str]:
        row = row.strip()
        if row.startswith("|"):
            row = row[1:]
        if row.endswith("|") and not row.endswith("\\|"):
            row = row[:-1]
        return [c.strip() for c in re.split(r"(?<!\\)\|", row)]

    header = cells(rows[0])
    body = [cells(r) for r in rows[2:]]
    n = len(header)
    grid = [[inline(_html(c), "md.bold") for c in header]]
    grid += [[inline(_html(c)) for c in (r + [""] * n)[:n]] for r in body]
    widths = [max(runs_width(row[i]) for row in grid) for i in range(n)]
    if sum(widths) + 3 * n + 1 > width:
        return None
    out: list[Line] = []

    def border(left: str, mid: str, right: str) -> Line:
        return ([(left + mid.join("─" * (w + 2) for w in widths) + right, "md.rule", None)], None)

    out.append(border("┌", "┬", "┐"))
    for r, row in enumerate(grid):
        line: list[Run] = [("│", "md.rule", None)]
        for i, runs in enumerate(row):
            line += [(" ", TEXT, None)] + runs + [(" " * (widths[i] - runs_width(runs) + 1), TEXT, None)]
            line.append(("│", "md.rule", None))
        out.append((line, None))
        if r == 0:
            out.append(border("├", "┼", "┤"))
    out.append(border("└", "┴", "┘"))
    return out


def render(text: str, width: int) -> list[Line]:
    """Markdown → lines at most `width` cells wide (code blocks are padded with `fill`)."""
    width = max(4, width)
    text = re.sub(r"<!--.*?(-->|\Z)", "", text.replace("\r\n", "\n"), flags=re.S)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    src = text.split("\n")
    out: list[Line] = []
    blank = True  # collapse runs of blank lines (and leading ones)
    list_indent: int | None = None  # continuation lines of the last list item go here
    i = 0

    def emit(lines: list[Line]) -> None:
        nonlocal blank
        out.extend(lines)
        blank = False

    while i < len(src):
        line = src[i]
        if not line.strip():
            if not blank:
                out.append(([], None))
            blank = True
            list_indent = None
            i += 1
            continue
        m = FENCE_RE.match(line)
        if m:
            fence, lang = m.group(2), m.group(3)
            code = []
            i += 1
            while i < len(src) and not src[i].strip().startswith(fence[0] * len(fence)):
                code.append(src[i][len(m.group(1)):] if src[i].startswith(m.group(1)) else src[i].lstrip())
                i += 1
            i += 1
            emit(_code_block(code, lang, width))
            list_indent = None
            continue
        line = _html(line)
        if not line.strip():  # only tags
            i += 1
            continue
        if (m := HEADING_RE.match(line)):
            level = len(m.group(1))
            runs = inline(m.group(2), "md.h1" if level <= 2 else "md.h")
            emit([(r, None) for r in wrap(runs, width)])
            if level <= 2:
                emit([([("─" * width, "md.rule", None)], None)])
            list_indent = None
            i += 1
            continue
        if RULE_RE.match(line):
            emit([([("─" * width, "md.rule", None)], None)])
            i += 1
            continue
        if line.lstrip().startswith("|") and i + 1 < len(src) and TABLE_SEP_RE.match(src[i + 1]):
            rows = [line]
            j = i + 1
            while j < len(src) and "|" in src[j]:
                rows.append(src[j])
                j += 1
            table = _table(rows, width)
            if table is not None:
                emit(table)
                i = j
                continue
        if QUOTE_RE.match(line):
            inner = []
            while i < len(src) and (m := QUOTE_RE.match(src[i])):
                inner.append(m.group(1))
                i += 1
            quoted = render("\n".join(inner), width - 2)
            while quoted and not quoted[-1][0]:
                quoted.pop()
            emit([([("▎ ", "md.quote", None)] + recolor(runs, {TEXT: MUTED}), fill) for runs, fill in quoted])
            continue
        if (m := LIST_RE.match(line)):
            depth = len(m.group(1).expandtabs(4)) // 2
            marker, content = m.group(2), m.group(3)
            if marker[0].isdigit():
                bullet = [(marker + " ", MUTED, None)]
            else:
                bullet = [(BULLETS[min(depth, len(BULLETS) - 1)] + " ", MUTED, None)]
            if (t := TASK_RE.match(content)):
                done = t.group(1) != " "
                bullet = [("☑ " if done else "☐ ", "md.check" if done else MUTED, None)]
                content = content[t.end():]
            indent = min(depth * 2, width // 2)
            pw = indent + runs_width(bullet)
            for k, runs in enumerate(wrap(inline(content), width - pw)):
                prefix = [(" " * indent, TEXT, None)] + bullet if k == 0 else [(" " * pw, TEXT, None)]
                emit([(prefix + runs, None)])
            list_indent = pw
            i += 1
            continue
        pw = list_indent if list_indent is not None and line[:1].isspace() else 0
        for runs in wrap(inline(line.strip()), width - pw):
            emit([([(" " * pw, TEXT, None)] + runs if pw else runs, None)])
        if not pw:
            list_indent = None
        i += 1
    while out and not out[-1][0] and out[-1][1] is None:
        out.pop()
    return out
