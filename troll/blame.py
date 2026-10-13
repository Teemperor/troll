"""git blame for an open file, and the commit preview opened from it.

`blame(path, data)` runs `git blame --contents -` on the buffer's bytes, so
the result lines up with the buffer even when it has unsaved edits (those
lines belong to `NOT_COMMITTED`). `style_show` turns `git show` output into
styled lines: the header and message, then the diff with each file's code
highlighted in its own language. `CommitView` is the scrollable preview.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from .gitcommit import GitError, repo_root, run_git
from .highlight import Span, Tokenizer
from .languages import detect

NOT_COMMITTED = "0" * 40
NOT_COMMITTED_LABEL = "Not committed yet"
MAX_WIDTH = 30  # of the blame column

_HEADER = re.compile(r"^([0-9a-f]{40}) \d+ (\d+)(?: \d+)?$")


@dataclass
class Blame:
    repo: str
    shas: list[str | None]  # per buffer row (None: no commit, e.g. the "" after a final newline)
    subjects: dict[str, str]
    views: dict[str, list[StyledLine]] = field(default_factory=dict)  # sha -> loaded commit

    def sha(self, row: int) -> str | None:
        return self.shas[row] if 0 <= row < len(self.shas) else None

    def label(self, row: int) -> str:
        sha = self.sha(row)
        if sha is None:
            return ""
        if sha == NOT_COMMITTED:
            return NOT_COMMITTED_LABEL
        return self.subjects.get(sha, sha[:10])

    @property
    def width(self) -> int:
        labels = [self.subjects.get(s, "") for s in set(self.shas) if s and s != NOT_COMMITTED]
        if NOT_COMMITTED in self.shas:
            labels.append(NOT_COMMITTED_LABEL)
        return max(1, min(MAX_WIDTH, max((len(t) for t in labels), default=1)))


def parse_porcelain(out: str, rows: int) -> tuple[list[str | None], dict[str, str]]:
    shas: list[str | None] = [None] * rows
    subjects: dict[str, str] = {}
    sha = None
    for line in out.split("\n"):
        if line.startswith("\t"):
            continue
        m = _HEADER.match(line)
        if m:
            sha = m.group(1)
            row = int(m.group(2)) - 1
            if 0 <= row < rows:
                shas[row] = sha
        elif line.startswith("summary ") and sha is not None:
            subjects.setdefault(sha, line[len("summary "):])
    return shas, subjects


def blame(path: str, data: bytes) -> Blame:
    """Blame the file at `path` with `data` as its contents."""
    repo = repo_root(os.path.dirname(os.path.abspath(path)))
    rel = os.path.relpath(os.path.realpath(path), os.path.realpath(repo))
    if run_git(repo, "ls-files", "--error-unmatch", "--", rel, check=False).returncode != 0:
        raise GitError(f"{rel} is not tracked by git")
    proc = run_git(repo, "blame", "--porcelain", "--contents", "-", "--", rel, input=data, check=False)
    if proc.returncode != 0:
        raise GitError(f"git blame failed: {proc.stderr.decode(errors='replace').strip()}")
    rows = data.count(b"\n") + 1
    shas, subjects = parse_porcelain(proc.stdout.decode("utf-8", errors="surrogateescape"), rows)
    return Blame(repo, shas, subjects)


def show_commit(repo: str, sha: str) -> list[str]:
    out = run_git(repo, "show", "--no-color", "--no-ext-diff", "--format=medium", sha).stdout
    return out.decode("utf-8", errors="surrogateescape").rstrip("\n").split("\n")


StyledLine = tuple[str, list[Span], "str | None"]  # text, (start, end, token) spans, line background


def style_show(lines: list[str]) -> list[StyledLine]:
    """Styles for `git show` output: header, message and a highlighted diff."""
    out: list[StyledLine] = []
    tokenizers: dict[str, Tokenizer] = {}
    tok: Tokenizer | None = None
    old = new = None  # tokenizer states of the two sides of the current hunk
    in_diff = in_hunk = seen_subject = False
    for line in lines:
        n = len(line)
        if line.startswith("diff --git "):
            in_diff, in_hunk = True, False
            lang = detect(line.rsplit(" b/", 1)[-1])
            tok = tokenizers.setdefault(lang.name, Tokenizer(lang))
            out.append((line, [(0, n, "diff_header")], None))
        elif not in_diff:
            if line.startswith("commit "):
                out.append((line, [(0, 6, "keyword"), (7, n, "constant")], None))
            elif re.match(r"^\w+:", line) and not seen_subject:  # Author:, Date:, Merge:
                k = line.index(":") + 1
                out.append((line, [(0, k, "label")], None))
            elif line.strip() and not seen_subject:
                seen_subject = True
                out.append((line, [(0, n, "heading")], None))
            else:
                out.append((line, [], None))
        elif line.startswith("@@"):
            in_hunk = True
            old = new = None
            end = line.find("@@", 2)
            out.append((line, [(0, n if end < 0 else end + 2, "diff_hunk")], None))
        elif in_hunk and line[:1] in ("+", "-", " ") and tok is not None:
            code = line[1:]
            if line[0] == "+":
                spans, new = tok.tokenize(code, new)
                marker, bg = "diff_add", "add"
            elif line[0] == "-":
                spans, old = tok.tokenize(code, old)
                marker, bg = "diff_del", "del"
            else:
                spans, new = tok.tokenize(code, new)
                _, old = tok.tokenize(code, old)
                marker, bg = "text", None
            out.append((line, [(0, 1, marker)] + [(s + 1, e + 1, t) for s, e, t in spans], bg))
        elif in_hunk and line.startswith("\\"):
            out.append((line, [(0, n, "comment")], None))
        else:  # index, ---/+++, mode and rename lines
            in_hunk = False
            out.append((line, [(0, n, "diff_header")], None))
    return out


class CommitView:
    """Read-only, scrollable view of a commit; Esc closes it."""

    kind = "commit"

    def __init__(self, title: str, lines: list[StyledLine]):
        self.title = title
        self.lines = lines
        self.scroll = 0
        self.left = 0  # horizontal scroll, in display columns
        self.done = False
        self.height = 20

    def handle(self, key: str) -> None:
        page = max(1, self.height - 1)
        if key in ("Up", "C-p", "WheelUp"):
            self.scroll -= 1 if key != "WheelUp" else 3
        elif key in ("Down", "C-n", "Enter", "WheelDown"):
            self.scroll += 1 if key != "WheelDown" else 3
        elif key in ("PageUp", "C-y", "M-Up"):
            self.scroll -= page
        elif key in ("PageDown", "C-v", " ", "M-Down"):
            self.scroll += page
        elif key in ("Home", "M-\\", "C-Home"):
            self.scroll, self.left = 0, 0
        elif key in ("End", "M-/", "C-End"):
            self.scroll = len(self.lines)
        elif key in ("Left", "C-b"):
            self.left = max(0, self.left - 8)
        elif key in ("Right", "C-f"):
            self.left += 8
        elif key in ("Esc", "q", "C-x", "C-c", "C-g"):
            self.done = True
        self.scroll = max(0, min(self.scroll, max(0, len(self.lines) - page)))
