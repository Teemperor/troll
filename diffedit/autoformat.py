"""Language-aware formatting helpers.

Everything here is a pure function of lines/settings/language so it can be
tested without a document or a terminal. The document applies the results.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .languages import SCISSORS, TRAILER, Language
from .settings import Settings
from .textutil import BRACKET_PAIRS, CLOSERS, OPENERS, display_width, find_matching_bracket, leading_ws

# ---------------------------------------------------------------- indentation


def detect_indent(lines: list[str], sample: int = 2000) -> tuple[bool | None, int | None]:
    """Guess (use_tabs, indent_width) from the file contents."""
    tabs = spaces = 0
    widths: dict[int, int] = {}
    prev = 0
    for line in lines[:sample]:
        if not line.strip():
            continue
        ws = leading_ws(line)
        if ws.startswith("\t"):
            tabs += 1
            prev = 0
            continue
        n = len(ws)
        if n and ws.strip(" ") == "":
            spaces += 1
        diff = abs(n - prev)
        if diff >= 2:
            widths[diff] = widths.get(diff, 0) + 1
        prev = n
    if tabs == 0 and spaces == 0:
        return None, None
    if tabs > spaces:
        return True, None
    width = None
    if widths:
        width = max(widths.items(), key=lambda kv: (kv[1], -kv[0]))[0]
        if width > 8:
            width = None
    return False, width


def indent_unit(settings: Settings) -> str:
    return " " * settings.tab_size if settings.expand_tabs else "\t"


def make_indent(width: int, settings: Settings) -> str:
    if settings.expand_tabs:
        return " " * width
    return "\t" * (width // settings.tab_size) + " " * (width % settings.tab_size)


def dedent_once(ws: str, settings: Settings) -> str:
    width = display_width(ws, settings.tab_size)
    target = max(0, ((width - 1) // settings.tab_size) * settings.tab_size)
    return make_indent(target, settings)


def indent_lines(lines: list[str], settings: Settings) -> list[str]:
    unit = indent_unit(settings)
    return [unit + line if line.strip() else line for line in lines]


def unindent_line(line: str, settings: Settings) -> str:
    if line.startswith("\t"):
        return line[1:]
    n = 0
    while n < settings.tab_size and n < len(line) and line[n] == " ":
        n += 1
    return line[n:]


def unindent_lines(lines: list[str], settings: Settings) -> list[str]:
    return [unindent_line(line, settings) for line in lines]


# ------------------------------------------------------- comments and lists


@dataclass
class Prefix:
    first: str  # prefix of the line itself
    rest: str  # prefix to use for continuation lines
    body: str  # text after the prefix
    kind: str  # "comment", "list", "quote" or "text"
    closed: bool = False  # e.g. a block comment that ends on this line


LIST_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])(\s+)(\[[ xX]\]\s+)?")


def _line_comment_re(lang: Language) -> re.Pattern | None:
    markers = [re.escape(m) for m in lang.all_comment_prefixes()]
    if not markers:
        return None
    alternatives = []
    for m in markers:
        if m == "//":
            alternatives.append(r"//[/!]?")
        elif m == re.escape("#"):
            alternatives.append(r"#+(?![!\[])")
        else:
            alternatives.append(m + "+")
    return re.compile(r"^(\s*)(" + "|".join(alternatives) + r")(\s?)")


def line_prefix(line: str, lang: Language) -> Prefix:
    """Split a line into its structural prefix and body."""
    comment_re = _line_comment_re(lang)
    if comment_re:
        m = comment_re.match(line)
        if m:
            return Prefix(m.group(0), m.group(0) if m.group(3) else m.group(0) + " ", line[m.end():], "comment")
    if lang.block_comment == ("/*", "*/"):
        m = re.match(r"^(\s*)(/\*\*?|\*(?!/))(\s?)", line)
        if m:
            indent = m.group(1)
            closed = line.rstrip().endswith("*/")
            if m.group(2).startswith("/"):
                rest = indent + " * "
            else:
                rest = indent + "* "
            return Prefix(m.group(0), rest, line[m.end():], "comment", closed)
    if lang.list_continuation:
        m = LIST_RE.match(line)
        if m:
            indent, bullet, space, box = m.group(1), m.group(2), m.group(3), m.group(4)
            if bullet[0].isdigit():
                nxt = f"{int(bullet[:-1]) + 1}{bullet[-1]}"
            else:
                nxt = bullet
            rest = indent + nxt + space + ("[ ] " if box else "")
            return Prefix(m.group(0), rest, line[m.end():], "list")
        m = re.match(r"^(\s*>+\s?)", line)
        if m:
            return Prefix(m.group(1), m.group(1), line[m.end():], "quote")
    ws = leading_ws(line)
    return Prefix(ws, ws, line[len(ws):], "text")


# ------------------------------------------------------------------ newline


@dataclass
class NewlinePlan:
    lines: list[str]  # replacement for the current line
    cursor: tuple[int, int]  # (row offset, col) relative to the current row


def plan_newline(
    line: str,
    col: int,
    lang: Language,
    settings: Settings,
    comment_start: int | None = None,
    line_is_comment: bool | None = None,
) -> NewlinePlan:
    """Decide what pressing Enter at `col` of `line` should produce.

    `comment_start` is the column where a (trailing) comment starts, if the
    caller knows it from the highlighter; code-based decisions such as
    "indent after a colon" ignore the comment. `line_is_comment` tells
    whether the line's first non-blank character is inside a comment; when
    None the comment prefix regex alone decides.
    """
    before, after = line[:col], line[col:]
    if not settings.auto_indent:
        return NewlinePlan([before, after], (1, 0))

    indent = leading_ws(line)
    if col < len(indent):
        indent = line[:col]
    after = after.lstrip(" \t")
    first = before.rstrip(" \t") if before.strip() else ""

    if settings.comment_continue:
        prefix = line_prefix(line, lang)
        real = prefix.kind != "comment" or line_is_comment is not False
        if real and prefix.kind != "text" and not prefix.closed and col >= len(prefix.first.rstrip()):
            if not prefix.body.strip() and not after and not _opens_block(prefix):
                # Enter on an empty continuation line ends the comment/list.
                return NewlinePlan([indent], (0, len(indent)))
            return NewlinePlan([first, prefix.rest + after], (1, len(prefix.rest)))

    code = before
    if comment_start is not None and comment_start < col:
        code = before[:comment_start]
    code = code.rstrip()

    new_indent = indent
    if lang.indent_after and code and re.search(lang.indent_after, code):
        new_indent = indent + indent_unit(settings)
        closer = BRACKET_PAIRS.get(code[-1]) if code[-1] in OPENERS else None
        if closer and after.startswith(closer):
            return NewlinePlan([first, new_indent, indent + after], (1, len(new_indent)))
        if lang.name == "html" and code.endswith(">") and after.startswith("</"):
            return NewlinePlan([first, new_indent, indent + after], (1, len(new_indent)))
    elif lang.dedent_after and re.match(lang.dedent_after, code):
        new_indent = dedent_once(indent, settings)
    return NewlinePlan([first, new_indent + after], (1, len(new_indent)))


# ------------------------------------------------------------ electric keys

PY_CLAUSES = {
    "else": ("if", "elif", "for", "while", "try", "except"),
    "elif": ("if", "elif"),
    "except": ("try", "except"),
    "finally": ("try", "except", "else"),
    "case": ("match", "case"),
}


def electric_indent(lines: list[str], row: int, lang: Language, settings: Settings) -> str | None:
    """Re-indent `lines[row]` after a closing character was typed.

    Returns the new line or None if nothing should change.
    """
    line = lines[row]
    stripped = line.strip()
    indent = leading_ws(line)
    if stripped and stripped[0] in CLOSERS and stripped.rstrip(";,") in (")", "]", "}", "})", "});", "),", "];"):
        match = find_matching_bracket(lines, (row, len(indent)))
        if match is None:
            return None
        target = leading_ws(lines[match[0]])
        new = target + stripped
        return new if new != line else None
    if lang.name == "python" and stripped.endswith(":"):
        word = re.match(r"(\w+)", stripped)
        if not word or word.group(1) not in PY_CLAUSES:
            return None
        if word.group(1) in ("else", "finally") and stripped != word.group(1) + ":":
            return None
        cur = display_width(indent, settings.tab_size)
        for r in range(row - 1, -1, -1):
            text = lines[r]
            if not text.strip():
                continue
            ws = leading_ws(text)
            width = display_width(ws, settings.tab_size)
            if width < cur:
                head = re.match(r"(\w+)", text.strip())
                if head and head.group(1) in PY_CLAUSES[word.group(1)]:
                    new = ws + stripped
                    return new if new != line else None
                return None
            if width == cur:
                head = re.match(r"(\w+)", text.strip())
                if head and head.group(1) in PY_CLAUSES[word.group(1)]:
                    return None  # already lined up with its opener
        return None
    return None


# --------------------------------------------------------------- auto pair


def plan_pair(line: str, col: int, ch: str, lang: Language, context: str | None) -> tuple[str, str] | None:
    """What to do when `ch` is typed at `col`.

    Returns ("skip", ch) to step over an existing closer, ("pair", text) to
    insert an opener with its closer (cursor goes between), or None to just
    insert the character.
    """
    pairs = {p[0]: p[1] for p in lang.pairs}
    closers = {p[1] for p in lang.pairs}
    nxt = line[col] if col < len(line) else ""
    prev = line[col - 1] if col > 0 else ""
    if context in ("comment", "string"):
        if ch in closers and nxt == ch and ch in ('"', "'", "`") and context == "string":
            return ("skip", ch)
        return None
    if ch in closers and nxt == ch:
        return ("skip", ch)
    if ch not in pairs:
        return None
    close = pairs[ch]
    if ch == close:  # symmetric quote
        if prev.isalnum() or prev == ch or prev == "\\":
            return None
        if nxt and (nxt.isalnum() or nxt == ch):
            return None
    elif nxt and not (nxt.isspace() or nxt in CLOSERS or nxt in ",;:"):
        return None
    return ("pair", ch + close)


def is_empty_pair(line: str, col: int, lang: Language) -> bool:
    if col <= 0 or col >= len(line):
        return False
    return (line[col - 1] + line[col]) in lang.pairs


# ----------------------------------------------------------------- comments


def toggle_comment(lines: list[str], lang: Language) -> list[str] | None:
    marker = lang.line_comment
    nonblank = [line for line in lines if line.strip()]
    if not nonblank:
        return None
    if marker is None:
        if not lang.block_comment:
            return None
        start, end = lang.block_comment
        if all(line.strip().startswith(start) and line.rstrip().endswith(end) for line in nonblank):
            out = []
            for line in lines:
                if not line.strip():
                    out.append(line)
                    continue
                ws = leading_ws(line)
                body = line.strip()[len(start):-len(end)]
                out.append(ws + body.strip())
            return out
        return [f"{leading_ws(line)}{start} {line.strip()} {end}" if line.strip() else line for line in lines]

    if all(line.lstrip().startswith(marker) for line in nonblank):
        out = []
        for line in lines:
            i = line.find(marker)
            if i < 0 or line[:i].strip():
                out.append(line)
                continue
            rest = line[i + len(marker):]
            if rest.startswith(" "):
                rest = rest[1:]
            out.append(line[:i] + rest)
        return out
    col = min(len(leading_ws(line)) for line in nonblank)
    return [line[:col] + marker + " " + line[col:] if line.strip() else line for line in lines]


def comment_out(lines: list[str], marker: str) -> list[str]:
    """Prefix every non-blank line with `marker`, aligned at the smallest indentation."""
    nonblank = [line for line in lines if line.strip()]
    if not nonblank:
        return list(lines)
    col = min(len(leading_ws(line)) for line in nonblank)
    return [line[:col] + marker + " " + line[col:] if line.strip() else line for line in lines]


# ------------------------------------------------------------------ justify


def _body(p: Prefix) -> str:
    return p.body.strip()


def _similar(a: Prefix, b: Prefix) -> bool:
    """Whether two (non-list) lines belong to the same kind of paragraph."""
    if a.kind != b.kind:
        return False
    if a.kind == "comment":
        return a.rest.rstrip() == b.rest.rstrip()
    if a.kind == "quote":
        return a.first.strip() == b.first.strip()
    return True


def _breaks(p: Prefix, lang: Language) -> bool:
    body = _body(p)
    if not body or body == "*/":
        return True
    if lang.name == "gitcommit":
        text = p.first + body
        return text.startswith("#") or re.match(TRAILER, text) is not None
    return lang.name == "markdown" and body.startswith(("```", "#", "|"))


def message_end(lines: list[str]) -> int:
    """Last row of a commit message that is prose: before git's scissors line
    (and the diff `git commit -v` puts below it)."""
    for r, line in enumerate(lines):
        if re.match(SCISSORS, line):
            return r - 1
    return len(lines) - 1


def is_message_prose(lines: list[str], row: int) -> bool:
    """Whether `row` of a commit message may be wrapped: not the subject,
    a '#' comment, a trailer or below the scissors line."""
    line = lines[row]
    if row == 0 or line.startswith("#") or re.match(TRAILER, line):
        return False
    return row <= message_end(lines[: row + 1])


def _opens_block(p: Prefix) -> bool:
    return p.kind == "comment" and p.first.lstrip().startswith("/*")


def find_paragraph(lines: list[str], row: int, lang: Language, lo: int = 0, hi: int | None = None) -> tuple[int, int] | None:
    """Rows [first, last] of the paragraph containing `row`, or None."""
    hi = len(lines) - 1 if hi is None else hi
    if lang.name == "gitcommit":  # the subject line stands alone; nothing below the scissors counts
        if row == 0 or row > message_end(lines[: row + 1]):
            return None
        lo = max(lo, 1)
    p = line_prefix(lines[row], lang)
    if _breaks(p, lang):
        return None
    first = row
    if p.kind != "list":
        while first > lo and not _opens_block(line_prefix(lines[first], lang)):
            q = line_prefix(lines[first - 1], lang)
            if _breaks(q, lang) or q.closed:
                break
            if q.kind == "list":
                # an indented text line directly below a list item continues it
                if p.kind == "text" and len(leading_ws(lines[first])) > len(leading_ws(lines[first - 1])):
                    first -= 1
                break
            if not _similar(q, p):
                break
            first -= 1
    head = line_prefix(lines[first], lang)
    head_indent = len(leading_ws(lines[first]))
    last = first
    while not head.closed and last < hi:
        q = line_prefix(lines[last + 1], lang)
        if _breaks(q, lang):
            break
        if head.kind == "list":
            if not (q.kind == "text" and len(leading_ws(lines[last + 1])) > head_indent):
                break
        elif q.kind == "list" or _opens_block(q) or not _similar(q, head):
            break
        last += 1
        if q.closed:
            break
    return (first, max(last, row))


def fill(words: list[str], first_prefix: str, rest_prefix: str, width: int, tab_size: int) -> list[str]:
    out: list[str] = []
    prefix = first_prefix
    cur = ""
    for word in words:
        candidate = word if not cur else cur + " " + word
        if cur and display_width(prefix + candidate, tab_size) > width:
            out.append((prefix + cur).rstrip())
            prefix = rest_prefix
            cur = word
        else:
            cur = candidate
    out.append((prefix + cur).rstrip())
    return out


def justify_paragraph(lines: list[str], first: int, last: int, lang: Language, width: int, tab_size: int) -> list[str]:
    head = line_prefix(lines[first], lang)
    words = head.body.split()
    for r in range(first + 1, last + 1):
        if head.kind == "list":
            words += lines[r].split()
        else:
            words += line_prefix(lines[r], lang).body.split()
    if head.kind == "list":
        rest = leading_ws(lines[first + 1]) if last > first else " " * len(head.first)
    elif head.kind == "text":
        rest = leading_ws(lines[first + 1]) if last > first else head.first
    elif head.kind == "comment" and last > first:
        rest = line_prefix(lines[first + 1], lang).first
        if not rest.endswith((" ", "\t")):
            rest += " "
    else:
        rest = head.rest
    return fill(words, head.first, rest, width, tab_size)


def justify(lines: list[str], row: int, lang: Language, width: int, tab_size: int) -> tuple[int, int, list[str]] | None:
    """Reflow the paragraph (plain text, comment block or list item) at `row`."""
    para = find_paragraph(lines, row, lang)
    if para is None:
        return None
    first, last = para
    return first, last, justify_paragraph(lines, first, last, lang, width, tab_size)


def justify_range(lines: list[str], lo: int, hi: int, lang: Language, width: int, tab_size: int) -> list[str]:
    """Reflow every paragraph between rows lo and hi; returns the new lines for that range."""
    out: list[str] = []
    r = lo
    stop = min(hi, message_end(lines)) if lang.name == "gitcommit" else hi
    while r <= stop:
        para = find_paragraph(lines, r, lang, lo, hi)
        if para is None:
            out.append(lines[r])
            r += 1
            continue
        first, last = para
        first = max(first, r)
        out.extend(lines[r:first])
        out.extend(justify_paragraph(lines, first, last, lang, width, tab_size))
        r = last + 1
    out.extend(lines[r : hi + 1])
    return out
