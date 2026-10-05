"""IDE mode: browse a directory, find files by name and search their contents.

`Browser` is the pure state of the directory overview screen (driven by the
editor, drawn by `view.browser_rows`); the functions below do the file
system work.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field

from .prompt import LineEdit, fuzzy_score

HIDDEN = {".git", ".hg", ".svn"}  # never listed
SKIP_DIRS = HIDDEN | {"node_modules", "__pycache__", ".mypy_cache", ".pytest_cache", ".tox", ".venv", "venv"}
MAX_FILES = 50_000
MAX_SEARCH_SIZE = 2 << 20  # bigger files are skipped when searching
MAX_HITS = 5_000


@dataclass
class Entry:
    name: str
    path: str  # absolute
    is_dir: bool


@dataclass
class Hit:
    path: str  # relative to the root
    row: int
    col: int
    end: int
    text: str
    lines: list[str] = field(default_factory=list, repr=False)  # the whole file (shared by its hits), for context


def list_dir(path: str) -> list[Entry]:
    """Directories first, then files, both sorted case-insensitively."""
    out = []
    with os.scandir(path) as it:
        for e in it:
            if e.name in HIDDEN:
                continue
            try:
                is_dir = e.is_dir()
            except OSError:
                is_dir = False
            out.append(Entry(e.name, os.path.join(path, e.name), is_dir))
    out.sort(key=lambda e: (not e.is_dir, e.name.lower(), e.name))
    return out


def _git_files(root: str) -> list[str] | None:
    try:
        proc = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=root, capture_output=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    names = [os.fsdecode(n) for n in proc.stdout.split(b"\0") if n]
    return sorted({n for n in names if os.path.isfile(os.path.join(root, n))})


def list_files(root: str) -> list[str]:
    """Every file below `root` (relative paths). Inside a git work tree this is
    what git knows about (ignored files are left out)."""
    files = _git_files(root)
    if files is not None:
        return files[:MAX_FILES]
    out = []
    for d, dirs, names in os.walk(root):
        dirs[:] = sorted(n for n in dirs if n not in SKIP_DIRS)
        rel = os.path.relpath(d, root)
        for n in sorted(names):
            out.append(n if rel == "." else os.path.join(rel, n))
            if len(out) >= MAX_FILES:
                return out
    return out


def search_files(root: str, files: list[str], pattern: re.Pattern, max_hits: int = MAX_HITS) -> list[Hit]:
    """Every (non-empty) match of `pattern` in the text files among `files`."""
    hits = []
    for rel in files:
        full = os.path.join(root, rel)
        try:
            if os.path.getsize(full) > MAX_SEARCH_SIZE:
                continue
            with open(full, "rb") as f:
                data = f.read()
        except OSError:
            continue
        if b"\0" in data[:8192]:
            continue  # binary
        text = data.decode("utf-8", "surrogateescape")
        if not pattern.search(text):
            continue
        lines = text.replace("\r\n", "\n").split("\n")
        for row, line in enumerate(lines):
            for m in pattern.finditer(line):
                if m.end() > m.start():
                    hits.append(Hit(rel, row, m.start(), m.end(), line, lines))
                    break  # one hit per line
            if len(hits) >= max_hits:
                return hits
    return hits


def context_ranges(hits: list[Hit], context: int) -> list[tuple[int, int]]:
    """For each hit, the rows [start, end) to show around it with `context` lines
    of context. Neighbouring hits in the same file share lines instead of
    repeating them: a line belongs to the earlier hit."""
    out = []
    for i, h in enumerate(hits):
        n = len(h.lines) or h.row + 1
        start, end = max(0, h.row - context), min(n, h.row + context + 1)
        prev = hits[i - 1] if i else None
        if prev is not None and prev.path == h.path and prev.row < h.row:
            start = max(start, min(out[-1][1], h.row))
        nxt = hits[i + 1] if i + 1 < len(hits) else None
        if nxt is not None and nxt.path == h.path and h.row < nxt.row:
            end = min(end, nxt.row)
        out.append((start, end))
    return out


class Browser:
    """The directory overview: a listing of `dir` (somewhere below `root`) or,
    after a search, the matching lines. Typing filters what is shown."""

    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        self.dir = self.root
        self.entries: list[Entry] = []
        self.hits: list[Hit] | None = None  # search results mode
        self.query = ""
        self.filter = LineEdit()
        self.selected = 0
        self.scroll = 0
        self.visible = True
        self.refresh()

    @property
    def rel_dir(self) -> str:
        rel = os.path.relpath(self.dir, self.root)
        return "" if rel == "." else rel

    def refresh(self) -> None:
        self.entries = list_dir(self.dir)

    def items(self) -> list[Entry] | list[Hit]:
        q = self.filter.text.strip()
        items = self.entries if self.hits is None else self.hits
        if not q:
            return list(items)
        scored = []
        for i, item in enumerate(items):
            key = item.name if isinstance(item, Entry) else f"{item.path}:{item.row + 1} {item.text}"
            s = fuzzy_score(q, key)
            if s is not None:
                scored.append((-s, i, item))
        if self.hits is not None:  # keep the file order of search results
            scored.sort(key=lambda x: x[1])
        else:
            scored.sort(key=lambda x: (x[0], x[1]))
        return [item for _, _, item in scored]

    def current(self) -> Entry | Hit | None:
        items = self.items()
        if not items:
            return None
        self.selected = max(0, min(self.selected, len(items) - 1))
        return items[self.selected]

    def move(self, delta: int) -> None:
        n = len(self.items())
        if n:
            self.selected = max(0, min(n - 1, self.selected + delta))

    def wrap(self, delta: int) -> None:
        n = len(self.items())
        if n:
            self.selected = (self.selected + delta) % n

    def _reset(self) -> None:
        self.filter.set("")
        self.selected = 0
        self.scroll = 0

    def enter(self, path: str, select: str | None = None) -> None:
        """Show directory `path` (which must be below the root)."""
        entries = list_dir(path)
        self.dir = os.path.abspath(path)
        self.entries = entries
        self.hits = None
        self._reset()
        if select is not None:
            self.selected = next((i for i, e in enumerate(entries) if e.name == select), 0)

    def parent(self) -> bool:
        if self.dir == self.root:
            return False
        self.enter(os.path.dirname(self.dir), select=os.path.basename(self.dir))
        return True

    def show_hits(self, query: str, hits: list[Hit]) -> None:
        self.query = query
        self.hits = hits
        self._reset()

    def close_hits(self) -> None:
        self.hits = None
        self._reset()
        self.refresh()
