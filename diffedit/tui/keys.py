"""Turn raw curses input into key names ("C-k", "M-u", "S-Up", "PasteStart"...).

The decoder only depends on a `getch(timeout)` callable (returning a str,
an int keycode or None on timeout) and a `keyname(code)` callable, so it can
be tested without a terminal.
"""

from __future__ import annotations

from typing import Callable

EXT_BASES = {
    "kUP": "Up", "kDN": "Down", "kLFT": "Left", "kRIT": "Right", "kHOM": "Home", "kEND": "End",
    "kDC": "Delete", "kIC": "Insert", "kPRV": "PageUp", "kNXT": "PageDown",
}
MODIFIERS = {"2": "S-", "3": "M-", "4": "M-S-", "5": "C-", "6": "C-S-", "7": "C-M-", "8": "C-M-S-"}

# Named curses keys (by curses constant name) -> our names
CURSES_NAMES = {
    "KEY_UP": "Up", "KEY_DOWN": "Down", "KEY_LEFT": "Left", "KEY_RIGHT": "Right",
    "KEY_HOME": "Home", "KEY_END": "End", "KEY_PPAGE": "PageUp", "KEY_NPAGE": "PageDown",
    "KEY_DC": "Delete", "KEY_IC": "Insert", "KEY_BACKSPACE": "Backspace", "KEY_ENTER": "Enter",
    "KEY_BTAB": "S-Tab", "KEY_RESIZE": "Resize", "KEY_SLEFT": "S-Left", "KEY_SRIGHT": "S-Right",
    "KEY_SR": "S-Up", "KEY_SF": "S-Down", "KEY_SHOME": "S-Home", "KEY_SEND": "S-End",
    "KEY_SPREVIOUS": "S-PageUp", "KEY_SNEXT": "S-PageDown", "KEY_SDC": "S-Delete",
    "KEY_A1": "Home", "KEY_C1": "End", "KEY_A3": "PageUp", "KEY_C3": "PageDown", "KEY_B2": "Center",
}

CSI_FINAL = {"A": "Up", "B": "Down", "C": "Right", "D": "Left", "H": "Home", "F": "End",
             "P": "F1", "Q": "F2", "R": "F3", "S": "F4", "Z": "S-Tab"}
CSI_TILDE = {
    "1": "Home", "2": "Insert", "3": "Delete", "4": "End", "5": "PageUp", "6": "PageDown", "7": "Home", "8": "End",
    "11": "F1", "12": "F2", "13": "F3", "14": "F4", "15": "F5", "17": "F6", "18": "F7", "19": "F8",
    "20": "F9", "21": "F10", "23": "F11", "24": "F12", "200": "PasteStart", "201": "PasteEnd",
}


def control_name(ch: str) -> str | None:
    o = ord(ch)
    if ch == "\r":
        return "Enter"
    if ch == "\t":
        return "Tab"
    if ch == "\n":
        return "C-j"
    if o == 0:
        return "C-Space"
    if o == 8:
        return "C-h"
    if o == 127:
        return "Backspace"
    if 1 <= o <= 26:
        return "C-" + chr(o + 96)
    return {28: "C-\\", 29: "C-]", 30: "C-^", 31: "C-_"}.get(o)


def parse_csi(body: str) -> str | None:
    """Decode the part of an escape sequence after ESC [ or ESC O."""
    if not body:
        return None
    final = body[-1]
    params = body[:-1].lstrip("[?")
    if final == "~":
        parts = params.split(";")
        base = CSI_TILDE.get(parts[0])
        if base is None:
            return None
        mod = MODIFIERS.get(parts[1], "") if len(parts) > 1 else ""
        return mod + base
    base = CSI_FINAL.get(final)
    if base is None:
        return None
    parts = params.split(";")
    mod = MODIFIERS.get(parts[1], "") if len(parts) > 1 else ""
    return mod + base


class KeyDecoder:
    ESC_TIMEOUT = 0.05

    def __init__(self, getch: Callable[[float | None], object], keyname: Callable[[int], str], codes: dict[int, str] | None = None):
        self.getch = getch
        self.keyname = keyname
        self.codes = codes or {}

    def _code(self, code: int) -> str | None:
        name = self.codes.get(code)
        if name:
            return name
        try:
            kn = self.keyname(code)
        except Exception:
            return None
        if kn.startswith("KEY_F(") and kn.endswith(")"):
            return "F" + kn[6:-1]
        if kn in CURSES_NAMES:
            return CURSES_NAMES[kn]
        for base, name in EXT_BASES.items():
            if kn.startswith(base) and kn[len(base):] in MODIFIERS:
                return MODIFIERS[kn[len(base):]] + name
        return None

    def read(self, timeout: float | None = None) -> str | None:
        ch = self.getch(timeout)
        if ch is None:
            return None
        if isinstance(ch, int):
            return self._code(ch)
        if ch == "\x1b":
            return self._escape()
        name = control_name(ch)
        return name if name is not None else ch

    def _escape(self) -> str | None:
        nxt = self.getch(self.ESC_TIMEOUT)
        if nxt is None:
            return "Esc"
        if isinstance(nxt, int):
            name = self._code(nxt)
            return "M-" + name if name else None
        if nxt == "\x1b":
            return "Esc"
        if nxt in "[O":
            body = ""
            while len(body) < 16:
                c = self.getch(self.ESC_TIMEOUT)
                if c is None or isinstance(c, int):
                    break
                body += c
                if "\x40" <= c <= "\x7e" and not (c == "[" and len(body) == 1):
                    break
            if not body:
                return "M-" + nxt
            return parse_csi(body)
        if nxt in ("\x7f", "\x08"):
            return "M-Backspace"
        if nxt == "\r":
            return "M-Enter"
        if nxt == " ":
            return "M-Space"
        name = control_name(nxt)
        if name is not None:
            return "M-" + name
        return "M-" + nxt
