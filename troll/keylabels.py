"""Key labels as shown to the user, in the notation of the host OS.

Internally keys are named "C-x", "M-z", "M-S-Up", ...; UI strings are written
in nano's notation ("^X", "M-Z", "M-↓"). Both are turned into what you have
to press: "⌃ X" / "⌥ Z" on macOS, "Ctrl+X" / "Alt+Z" elsewhere. STYLE "nano"
keeps nano's notation (the tests use it).
"""

from __future__ import annotations

import re
import sys

STYLE = "mac" if sys.platform == "darwin" else "pc"

ARROWS = {"Up": "↑", "Down": "↓", "Left": "←", "Right": "→"}
_ORDER = "CMS"  # Ctrl, Alt/Option, Shift (macOS orders its symbols the same way)
_MODS = {
    # many terminal fonts draw these wider than the one cell they are counted as
    "mac": {"C": "⌃ ", "M": "⌥ ", "S": "⇧ "},
    "pc": {"C": "Ctrl+", "M": "Alt+", "S": "Shift+"},
    "nano": {"C": "^", "M": "M-", "S": "Sh-"},
}


def _render(mods: list[str], base: str, style: str | None) -> str:
    style = style or STYLE
    base = ARROWS.get(base, base)
    if len(base) == 1:
        base = base.upper()
    if style != "nano":
        mods = sorted(set(mods), key=_ORDER.index)
    return "".join(_MODS[style][m] for m in mods) + base


def key_label(key: str, style: str | None = None) -> str:
    """Label for an internal key name: "C-x" → "⌃ X" / "Ctrl+X" / "^X"."""
    mods = []
    while len(key) > 2 and key[:2] in ("C-", "M-", "S-"):
        mods.append(key[0])
        key = key[2:]
    return _render(mods, key, style)


_NANO_KEY = re.compile(
    r"(?<![\w^-])(?:(\^)|M-(S-(?=\S))?)"
    r"(Space|PgUp|PgDn|PageUp|PageDown|Up|Down|Left|Right|Enter|Tab|Backspace|Delete|Home|End|[^\s])"
)


def localize(text: str, style: str | None = None) -> str:
    """Rewrite the nano-notation keys in a UI string ("^X quits, M-Z folds")."""
    style = style or STYLE
    if style == "nano":
        return text

    def repl(m: re.Match) -> str:
        mods = ["C"] if m.group(1) else ["M", "S"] if m.group(2) else ["M"]
        return _render(mods, m.group(3), style)

    return _NANO_KEY.sub(repl, text)


def modifier_note(style: str | None = None) -> str:
    """A line for the help screen explaining the modifier keys."""
    style = style or STYLE
    if style == "mac":
        return ("⌃ is Control, ⌥ is Option, ⇧ is Shift. Let your terminal use Option as Meta\n"
                "(or type ⌥ key as Esc, then the key).")
    if style == "pc":
        return "Alt+key can also be typed as Esc, then the key."
    return "^ means Ctrl, M- means Alt (or Esc then the key)."
