"""The settings panel: browse and change options with the keyboard.

Pure logic (driven by key names); view.py renders it. Changes apply the
same way as the `set` command (see Editor.option_targets).
"""

from __future__ import annotations

from .prompt import LineEdit
from .settings import OPTIONS, OptionInfo, Settings

DEFAULTS = Settings()


class SettingsScreen:
    kind = "settings"

    def __init__(self, ed):
        self.ed = ed
        self.selected = 0
        self.scroll = 0
        self.height = 20
        self.editing: LineEdit | None = None  # number being typed in
        self.error: str | None = None
        self.done = False

    # -------------------------------------------------------------- values
    @property
    def options(self) -> tuple[OptionInfo, ...]:
        return OPTIONS

    @property
    def current(self) -> OptionInfo:
        return OPTIONS[self.selected]

    def _source(self, info: OptionInfo) -> Settings:
        doc = self.ed.doc
        if info.editor_wide or doc is None:
            return self.ed.settings
        return doc.settings

    def value(self, info: OptionInfo):
        return getattr(self._source(info), info.key)

    def is_default(self, info: OptionInfo) -> bool:
        return self.value(info) == getattr(DEFAULTS, info.key)

    def display(self, info: OptionInfo) -> str:
        v = self.value(info)
        if isinstance(v, bool):
            return "on" if v else "off"
        return str(v)

    def set(self, info: OptionInfo, value) -> None:
        for s in self.ed.option_targets(info.key):
            setattr(s, info.key, value)
        self.error = None

    # ------------------------------------------------------------- changes
    def change(self, step: int) -> None:
        """Toggle a switch, cycle a choice, or step a number by `step`."""
        info = self.current
        v = self.value(info)
        if isinstance(v, bool):
            self.set(info, not v)
        elif isinstance(v, int):
            self.set(info, max(info.minimum, min(info.maximum, v + step)))
        elif info.choices:
            i = info.choices.index(v) if v in info.choices else 0
            self.set(info, info.choices[(i + step) % len(info.choices)])

    def reset(self) -> None:
        self.set(self.current, getattr(DEFAULTS, self.current.key))

    def _commit_number(self) -> None:
        info = self.current
        text = self.editing.text.strip()
        try:
            n = int(text)
        except ValueError:
            self.error = f"'{text}' is not a number"
            return
        if not info.minimum <= n <= info.maximum:
            self.error = f"{info.label} must be between {info.minimum} and {info.maximum}"
            return
        self.set(info, n)
        self.editing = None

    # ---------------------------------------------------------------- keys
    def handle(self, key: str) -> None:
        if self.editing is not None:
            if key == "Enter":
                self._commit_number()
            elif key in ("Esc", "C-c"):
                self.editing = None
                self.error = None
            else:
                self.editing.handle(key)
            return
        n = len(OPTIONS)
        is_number = isinstance(self.value(self.current), int) and not isinstance(self.value(self.current), bool)
        if key in ("Up", "C-p", "k"):
            self.selected = (self.selected - 1) % n
        elif key in ("Down", "C-n", "j", "Tab"):
            self.selected = (self.selected + 1) % n
        elif key in ("S-Tab",):
            self.selected = (self.selected - 1) % n
        elif key in ("PageUp", "C-y", "Home", "M-\\"):
            self.selected = 0
        elif key in ("PageDown", "C-v", "End", "M-/"):
            self.selected = n - 1
        elif key in ("Enter", " "):
            if is_number:
                self.editing = LineEdit(str(self.value(self.current)))
            else:
                self.change(1)
        elif key in ("Right", "+", "="):
            self.change(1)
        elif key in ("Left", "-"):
            self.change(-1)
        elif len(key) == 1 and key.isdigit() and is_number:
            self.editing = LineEdit(key)
        elif key in ("d", "D", "Delete", "Backspace"):
            self.reset()
        elif key in ("Esc", "C-x", "C-c", "q", "C-g"):
            self.done = True

    def ensure_visible(self, height: int) -> None:
        if self.selected < self.scroll:
            self.scroll = self.selected
        elif self.selected >= self.scroll + height:
            self.scroll = self.selected - height + 1
