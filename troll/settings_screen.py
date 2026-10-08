"""The settings panel: browse and change options with the keyboard.

Pure logic (driven by key names); view.py renders it. Changes apply the
same way as the `set` command (see Editor.option_targets).
"""

from __future__ import annotations

from .prompt import LineEdit, Picker, PickerItem
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
        self.dropdown: Picker | None = None  # choices of a `listed` option
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
        if self.is_text(info) and not v:
            return "(empty)"
        return str(v)

    def is_text(self, info: OptionInfo) -> bool:
        """A free-form string (typed in, not cycled through choices)."""
        return isinstance(self.value(info), str) and not info.choices

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
        if self.is_text(info):
            self.set(info, text)
            self.editing = None
            return
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

    def open_dropdown(self, choices: list[str]) -> None:
        """Pick the current option's value from `choices` (typing one that isn't listed works too)."""
        info = self.current
        value = self.value(info)

        def chosen(item, text):
            self.set(info, item.value if item is not None else text.strip())

        items = [PickerItem("(auto)", "the default", "")]
        items += [PickerItem(c, "current" if c == value else "", c) for c in sorted(choices)]
        p = Picker(info.label, items, chosen, allow_free_text=True, placeholder="type to filter")
        p.selected = next((i for i, it in enumerate(items) if it.value == value), 0)
        self.dropdown = p

    def choices_failed(self, error: str) -> None:
        """The choices couldn't be fetched: type the value in instead."""
        self.error = f"{error} (type the value instead)"
        self.editing = LineEdit(str(self.value(self.current)))

    # ---------------------------------------------------------------- keys
    def handle(self, key: str) -> None:
        if self.dropdown is not None:
            self.dropdown.handle(key)
            if self.dropdown.done:
                self.dropdown = None
            return
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
            if self.current.listed:
                self.error = None
                self.ed.option_dropdown(self)
            elif is_number or self.is_text(self.current):
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
        elif key in ("s", "S", "C-s", "C-o"):
            self.ed.save_settings()
        elif key in ("Esc", "C-x", "C-c", "q", "C-g"):
            self.done = True

    def ensure_visible(self, height: int) -> None:
        if self.selected < self.scroll:
            self.scroll = self.selected
        elif self.selected >= self.scroll + height:
            self.scroll = self.selected - height + 1
