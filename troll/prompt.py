"""Modal UI state machines: line prompts, choices, pickers and the help screen.

These are pure logic objects driven by key names; the view renders them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .textutil import is_word_char

Handler = Callable[..., Any]


def is_printable(key: str) -> bool:
    return len(key) == 1 and (key.isprintable() or key == "\t")


class LineEdit:
    """A single-line text field with emacs/nano-ish editing keys."""

    def __init__(self, text: str = ""):
        self.text = text
        self.cursor = len(text)

    def set(self, text: str) -> None:
        self.text = text
        self.cursor = len(text)

    def handle(self, key: str) -> bool:
        t, c = self.text, self.cursor
        if is_printable(key) and key != "\t":
            self.text = t[:c] + key + t[c:]
            self.cursor += 1
        elif key in ("Backspace", "C-h"):
            if c > 0:
                self.text = t[: c - 1] + t[c:]
                self.cursor -= 1
        elif key in ("Delete", "C-d"):
            self.text = t[:c] + t[c + 1 :]
        elif key in ("Left", "C-b"):
            self.cursor = max(0, c - 1)
        elif key in ("Right", "C-f"):
            self.cursor = min(len(t), c + 1)
        elif key in ("Home", "C-a"):
            self.cursor = 0
        elif key in ("End", "C-e"):
            self.cursor = len(t)
        elif key == "C-k":
            self.text = t[:c]
        elif key == "C-u":
            self.text = t[c:]
            self.cursor = 0
        elif key in ("M-Backspace", "C-w"):
            i = c
            while i > 0 and not is_word_char(t[i - 1]):
                i -= 1
            while i > 0 and is_word_char(t[i - 1]):
                i -= 1
            self.text = t[:i] + t[c:]
            self.cursor = i
        elif key.startswith("Paste:"):
            chunk = key[6:].replace("\n", " ")
            self.text = t[:c] + chunk + t[c:]
            self.cursor += len(chunk)
        else:
            return False
        return True


@dataclass
class PromptOption:
    key: str
    label: str
    action: Handler  # called with the Prompt


class Prompt:
    """nano-style prompt on the status line."""

    kind = "prompt"

    def __init__(
        self,
        label: str,
        on_submit: Handler,
        initial: str = "",
        on_cancel: Handler | None = None,
        history: list[str] | None = None,
        completer: Callable[[str], list[str]] | None = None,
        options: list[PromptOption] | None = None,
        status: Callable[[], str] | None = None,
    ):
        self.label = label
        self.field = LineEdit(initial)
        self.on_submit = on_submit
        self.on_cancel = on_cancel
        self.history = history if history is not None else []
        self._hist_pos = len(self.history)
        self.completer = completer
        self.options = options or []
        self.status = status
        self.done = False

    @property
    def text(self) -> str:
        return self.field.text

    def handle(self, key: str) -> None:
        for opt in self.options:
            if opt.key == key:
                opt.action(self)
                return
        if key == "Enter":
            self.done = True
            text = self.field.text
            if text and (not self.history or self.history[-1] != text):
                self.history.append(text)
            self.on_submit(text)
        elif key in ("C-c", "Esc"):
            self.done = True
            if self.on_cancel:
                self.on_cancel()
        elif key == "Up" and self.history:
            self._hist_pos = max(0, self._hist_pos - 1)
            self.field.set(self.history[self._hist_pos])
        elif key == "Down" and self.history:
            self._hist_pos = min(len(self.history), self._hist_pos + 1)
            self.field.set(self.history[self._hist_pos] if self._hist_pos < len(self.history) else "")
        elif key == "Tab" and self.completer:
            matches = self.completer(self.field.text)
            if len(matches) == 1:
                self.field.set(matches[0])
            elif matches:
                self.field.set(common_prefix(matches) or self.field.text)
        else:
            self.field.handle(key)


class Choice:
    """A yes/no/all style question answered with a single key."""

    kind = "choice"

    def __init__(self, question: str, options: list[tuple[str, str, Handler]], on_cancel: Handler | None = None):
        # options: (keys, label, action) e.g. ("yY", "Yes", fn)
        self.label = question
        self.options = options
        self.on_cancel = on_cancel
        self.done = False

    def handle(self, key: str) -> None:
        for keys, _label, action in self.options:
            if key in keys:
                self.done = True
                action()
                return
        if key in ("C-c", "Esc"):
            self.done = True
            if self.on_cancel:
                self.on_cancel()


def common_prefix(items: list[str]) -> str:
    if not items:
        return ""
    first, last = min(items), max(items)
    i = 0
    while i < len(first) and i < len(last) and first[i] == last[i]:
        i += 1
    return first[:i]


def fuzzy_score(query: str, text: str) -> int | None:
    """Subsequence match score (higher is better), None if no match."""
    if not query:
        return 0
    q, t = query.lower(), text.lower()
    if t.startswith(q):
        return 1000 - len(t)
    idx = t.find(q)
    if idx >= 0:
        return 500 - idx - len(t)
    score = 0
    pos = 0
    prev = -2
    for ch in q:
        found = t.find(ch, pos)
        if found < 0:
            return None
        if found == prev + 1:
            score += 5
        if found == 0 or not t[found - 1].isalnum():
            score += 3
        score -= found - pos
        prev = found
        pos = found + 1
    return score


@dataclass
class PickerItem:
    label: str
    detail: str = ""
    value: Any = None
    hint: str = ""  # right aligned (e.g. a key binding)
    search_text: str = ""


@dataclass
class Picker:
    """Filterable list overlay (command palette, commit picker, buffers...)."""

    title: str
    items: list[PickerItem]
    on_choose: Handler  # (item | None, text) -> None
    allow_free_text: bool = False
    placeholder: str = ""
    filter_key: Callable[[str], str] | None = None  # maps typed text to the filter query
    kind: str = "picker"
    field: LineEdit = field(default_factory=LineEdit)
    selected: int = 0
    scroll: int = 0
    done: bool = False
    on_cancel: Handler | None = None

    def query(self) -> str:
        text = self.field.text
        return self.filter_key(text) if self.filter_key else text

    def filtered(self) -> list[PickerItem]:
        q = self.query().strip()
        if not q:
            return list(self.items)
        scored = []
        for i, item in enumerate(self.items):
            s = fuzzy_score(q, item.label)
            if s is not None and item.label.lower() == q.lower():
                s += 2000
            if item.search_text:  # aliases etc. match too, but rank below the label
                alt = fuzzy_score(q, item.search_text)
                if alt is not None and (s is None or alt - 100 > s):
                    s = alt - 100
            if s is not None:
                scored.append((-s, i, item))
        scored.sort(key=lambda x: (x[0], x[1]))
        return [item for _, _, item in scored]

    def current(self) -> PickerItem | None:
        items = self.filtered()
        if not items:
            return None
        return items[max(0, min(self.selected, len(items) - 1))]

    def ensure_visible(self, height: int) -> None:
        if self.selected < self.scroll:
            self.scroll = self.selected
        elif self.selected >= self.scroll + height:
            self.scroll = self.selected - height + 1

    def handle(self, key: str) -> None:
        n = len(self.filtered())
        if key == "Enter":
            self.done = True
            self.on_choose(self.current(), self.field.text)
        elif key in ("C-c", "Esc", "C-x"):
            self.done = True
            if self.on_cancel:
                self.on_cancel()
        elif key in ("Up", "C-p"):
            self.selected = (self.selected - 1) % n if n else 0
        elif key in ("Down", "C-n"):
            self.selected = (self.selected + 1) % n if n else 0
        elif key in ("PageUp", "C-y"):
            self.selected = max(0, self.selected - 10)
        elif key in ("PageDown", "C-v"):
            self.selected = min(max(0, n - 1), self.selected + 10)
        elif key == "Tab":
            item = self.current()
            if item is not None:
                self.field.set(item.value if isinstance(item.value, str) and self.allow_free_text else item.label)
                if self.allow_free_text and not self.field.text.endswith(" "):
                    self.field.set(self.field.text + " ")
        else:
            before = self.field.text
            self.field.handle(key)
            if self.field.text != before:
                self.selected = 0
                self.scroll = 0


class HelpScreen:
    kind = "help"

    def __init__(self, title: str, lines: list[str]):
        self.title = title
        self.lines = lines
        self.scroll = 0
        self.done = False
        self.height = 20

    def handle(self, key: str) -> None:
        page = max(1, self.height - 1)
        if key in ("Up", "C-p"):
            self.scroll -= 1
        elif key in ("Down", "C-n", "Enter"):
            self.scroll += 1
        elif key in ("PageUp", "C-y", "M-Up"):
            self.scroll -= page
        elif key in ("PageDown", "C-v", " ", "M-Down"):
            self.scroll += page
        elif key in ("Home", "M-\\"):
            self.scroll = 0
        elif key in ("End", "M-/"):
            self.scroll = len(self.lines)
        elif key in ("C-x", "C-g", "Esc", "q", "C-c", "F1"):
            self.done = True
        self.scroll = max(0, min(self.scroll, max(0, len(self.lines) - page)))
