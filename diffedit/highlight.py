"""Regex based syntax highlighting with multi-line state.

`Tokenizer.tokenize(text, state)` highlights one line given the state at
its start (None, or the index of an open multi-line region) and returns the
spans plus the state at the end of the line. `Highlighter` keeps the chain
of per-line start states up to date as the buffer changes, re-tokenizing
only as far as needed.
"""

from __future__ import annotations

import re
from collections import OrderedDict

from .languages import FENCE_CLOSE, FENCE_OPEN, Language
from .languages import get as get_language

_FENCE_OPEN = re.compile(FENCE_OPEN)
_FENCE_CLOSE = re.compile(FENCE_CLOSE)

Span = tuple[int, int, str]  # start, end, token

UNKNOWN = object()


class Tokenizer:
    def __init__(self, lang: Language):
        self.lang = lang
        parts: list[str] = []
        self._kinds: dict[str, tuple[str, int]] = {}
        for i, region in enumerate(lang.regions):
            name = f"r{i}"
            parts.append(f"(?P<{name}>{region.start})")
            self._kinds[name] = ("region", i)
        for i, (_token, rx) in enumerate(lang.rules):
            name = f"t{i}"
            parts.append(f"(?P<{name}>{rx})")
            self._kinds[name] = ("rule", i)
        self._master = re.compile("|".join(parts)) if parts else None
        self.open_token: str | None = None  # region left open by the last tokenize()
        self._inner: dict[str, Tokenizer | None] = {}  # tokenizers for fenced code blocks
        self._ends = []
        for region in lang.regions:
            if region.escape:
                self._ends.append(re.compile(f"(?:{region.escape})|(?P<end>{region.end})"))
            else:
                self._ends.append(re.compile(f"(?P<end>{region.end})"))

    def _find_end(self, region_index: int, text: str, pos: int) -> int | None:
        pattern = self._ends[region_index]
        while True:
            m = pattern.search(text, pos)
            if m is None:
                return None
            if m.group("end") is not None:
                return m.end()
            pos = max(m.end(), pos + 1)

    def tokenize(self, text: str, state=None) -> tuple[list[Span], object]:
        """Spans of `text` and the state at its end: None, the index of an open
        region, or (fence, language name, inner state) inside a fenced code block."""
        if self.lang.fenced_code and (state is None or isinstance(state, tuple)):
            fenced = self._fenced(text, state)
            if fenced is not None:
                return fenced
        return self._tokenize(text, state)

    def _fenced(self, text: str, state):
        if state is None:
            m = _FENCE_OPEN.match(text)
            if m is None:
                return None
            self.open_token = None
            spans = [(m.start(2), m.end(2), "code")]
            if m.group(3):
                spans.append((m.start(3), m.end(3), "label"))
            lang = get_language(m.group(3)) if m.group(3) else None
            return spans, (m.group(2), lang.name if lang else "", None)
        fence, name, inner = state
        m = _FENCE_CLOSE.match(text)
        self.open_token = None
        if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
            return [(m.start(1), m.end(1), "code")], None
        if name not in self._inner:
            lang = get_language(name) if name else None
            self._inner[name] = Tokenizer(lang) if lang is not None and lang is not self.lang else None
        tok = self._inner[name]
        if tok is None:
            return ([(0, len(text), "code")] if text else []), state
        spans, end = tok.tokenize(text, inner)
        self.open_token = tok.open_token
        return spans, (fence, name, end)

    def _tokenize(self, text: str, state: int | None) -> tuple[list[Span], int | None]:
        spans: list[Span] = []
        pos = 0
        self.open_token = None
        if state is not None:
            region = self.lang.regions[state]
            end = self._find_end(state, text, 0)
            if end is None:
                if text:
                    spans.append((0, len(text), region.token))
                self.open_token = region.token
                return spans, state
            if end > 0:
                spans.append((0, end, region.token))
            pos = end
        if self._master is None:
            return spans, None
        while pos < len(text):
            m = self._master.search(text, pos)
            if m is None:
                break
            start, stop = m.span()
            if stop == start:  # never let an empty match stall us
                pos = start + 1
                continue
            kind, index = self._kinds[m.lastgroup]
            if kind == "region":
                region = self.lang.regions[index]
                end = self._find_end(index, text, stop)
                if end is None:
                    spans.append((start, len(text), region.token))
                    self.open_token = region.token
                    return spans, (index if region.multiline else None)
                spans.append((start, end, region.token))
                pos = end
            else:
                token = self.lang.rules[index][0]
                if token is not None:
                    spans.append((start, stop, token))
                pos = stop
        return spans, None


class Highlighter:
    """Incremental highlighter bound to a buffer."""

    CACHE_SIZE = 4096

    def __init__(self, buffer, lang: Language, first_line_limit=None):
        self.buffer = buffer
        self.first_line_limit = first_line_limit  # callable overriding lang.first_line_limit
        self.set_language(lang)
        buffer.add_listener(self._on_change)

    def set_language(self, lang: Language) -> None:
        self.lang = lang
        self.tokenizer = Tokenizer(lang)
        # states[i] is the tokenizer state at the start of line i.
        #  - states[0..valid] are correct.
        #  - states[0..computed] were computed at some point; beyond
        #    `dirty_end` (the last edited row, -1 if none) they still form a
        #    consistent chain, so recomputation can stop once it rejoins it.
        self.states: list[object] = [None] + [UNKNOWN] * (len(self.buffer.lines) - 1)
        self.valid = 0
        self.computed = 0
        self.dirty_end = -1
        self._cache: OrderedDict = OrderedDict()

    def _on_change(self, row: int, delta: int) -> None:
        if delta > 0:
            self.states[row + 1 : row + 1] = [UNKNOWN] * delta
        elif delta < 0:
            del self.states[row + 1 : row + 1 - delta]
        if self.dirty_end >= row:
            self.dirty_end = max(row, self.dirty_end + delta)
        self.dirty_end = max(self.dirty_end, row + max(delta, 0))
        if self.computed > row:
            self.computed = max(row, self.computed + delta)
        self.valid = min(self.valid, row)
        n = len(self.buffer.lines)
        if len(self.states) != n:  # defensive: keep in sync with the buffer
            del self.states[n:]
            self.states.extend([UNKNOWN] * (n - len(self.states)))
            self.computed = min(self.computed, n - 1)

    def _tokenize(self, row: int, state) -> tuple[list[Span], int | None]:
        text = self.buffer.lines[row]
        limit = (self.first_line_limit() if self.first_line_limit else self.lang.first_line_limit) \
            if row == 0 and self.lang.first_line_token else 0
        key = (text, state, row == 0, limit)
        hit = self._cache.get(key)
        if hit is not None:
            self._cache.move_to_end(key)
            return hit
        spans, end = self.tokenizer.tokenize(text, state)
        if row == 0 and self.lang.first_line_token and not text.startswith("#"):
            limit = limit or len(text)
            spans = [(0, min(len(text), limit), self.lang.first_line_token)]
            if len(text) > limit:
                spans.append((limit, len(text), "error"))
        self._cache[key] = (spans, end)
        if len(self._cache) > self.CACHE_SIZE:
            self._cache.popitem(last=False)
        return spans, end

    def state_at(self, row: int):
        while self.valid < row:
            i = self.valid
            _, end = self._tokenize(i, self.states[i])
            old = self.states[i + 1]
            self.states[i + 1] = end
            self.valid = i + 1
            if i + 1 > self.dirty_end:
                self.dirty_end = -1
                if old is not UNKNOWN and old == end and self.computed > self.valid:
                    # Rejoined the old chain: everything up to `computed` holds.
                    self.valid = self.computed
            self.computed = max(self.computed, self.valid)
        return self.states[row]

    def spans(self, row: int) -> list[Span]:
        if row < 0 or row >= len(self.buffer.lines):
            return []
        return self._tokenize(row, self.state_at(row))[0]

    def context_at(self, row: int, col: int) -> str | None:
        """Token the cursor at (row, col) is inside of: "comment", "string" or None.

        Computed by tokenizing the text before the cursor, so an unterminated
        string or comment right before the cursor counts as "inside".
        """
        if row < 0 or row >= len(self.buffer.lines):
            return None
        text = self.buffer.lines[row][:col]
        spans, _ = self.tokenizer.tokenize(text, self.state_at(row))
        if self.tokenizer.open_token is not None:
            return self.tokenizer.open_token
        if spans and spans[-1][1] == len(text) and spans[-1][2] == "comment":
            return "comment"
        return None
