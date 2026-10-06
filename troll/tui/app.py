"""curses front end: paints `Frame`s and feeds decoded keys to the editor."""

from __future__ import annotations

import curses
import locale
import os
import signal
import sys

from ..textutil import text_width
from ..view import build_frame
from . import theme
from .keys import KeyDecoder

ATTRS = {
    theme.BOLD: "A_BOLD",
    theme.DIM: "A_DIM",
    theme.REVERSE: "A_REVERSE",
    theme.UNDERLINE: "A_UNDERLINE",
    theme.ITALIC: "A_ITALIC",
}


class Painter:
    def __init__(self, stdscr):
        self.scr = stdscr
        self.pairs: dict[tuple[int, int], int] = {}
        self.cache: dict[tuple[str, str | None], int] = {}
        self.colors = 0
        if curses.has_colors():
            curses.start_color()
            try:
                curses.use_default_colors()
            except curses.error:
                pass
            self.colors = curses.COLORS

    def _pair(self, fg: int, bg: int) -> int:
        key = (fg, bg)
        if key not in self.pairs:
            n = len(self.pairs) + 1
            if n >= curses.COLOR_PAIRS:
                return 0
            try:
                curses.init_pair(n, fg, bg)
            except curses.error:
                return 0
            self.pairs[key] = n
        return curses.color_pair(self.pairs[key])

    def attr(self, style: str, bg_style: str | None) -> int:
        key = (style, bg_style)
        if key in self.cache:
            return self.cache[key]
        fg256, fg8, flags = theme.STYLES.get(style, theme.STYLES["text"])
        bg = (-1, -1)
        if bg_style is not None:
            bg = theme.BACKGROUNDS.get(bg_style, (-1, -1))
        elif style in theme.STATUS_BG:
            bg = theme.STATUS_BG[style]
        a = 0
        for flag, name in ATTRS.items():
            if flags & flag:
                a |= getattr(curses, name, 0)
        if self.colors >= 256:
            a |= self._pair(fg256, bg[0])
        elif self.colors >= 8:
            fg, b = fg8, bg[1]
            if fg == b and fg != -1:
                fg = 0 if b != 0 else 7
            a |= self._pair(fg, b)
            if bg_style in ("selection", "title", "help.keybg") and b == -1:
                a |= curses.A_REVERSE
        else:
            if bg_style or style.startswith(("title", "status", "help.key")):
                a |= curses.A_REVERSE
        self.cache[key] = a
        return a

    def paint(self, frame) -> None:
        scr = self.scr
        scr.erase()
        for y, row in enumerate(frame.rows):
            x = 0
            for text, fg, bg in row:
                if x >= frame.width:
                    break
                try:
                    scr.addstr(y, x, text, self.attr(fg, bg))
                except curses.error:
                    pass  # writing the bottom-right cell raises; harmless
                x += text_width(text)
        if frame.cursor is not None:
            try:
                curses.curs_set(1)
                scr.move(*frame.cursor)
            except curses.error:
                pass
        else:
            try:
                curses.curs_set(0)
            except curses.error:
                pass
        scr.refresh()


def _set_bracketed_paste(on: bool) -> None:
    try:
        sys.stdout.write("\x1b[?2004h" if on else "\x1b[?2004l")
        sys.stdout.flush()
    except OSError:
        pass


# wheel "buttons" (ncurses 6 values as a fallback for builds that don't export them)
WHEEL_UP = getattr(curses, "BUTTON4_PRESSED", 0x10000)
WHEEL_DOWN = getattr(curses, "BUTTON5_PRESSED", 0x200000)
CLICKS = curses.BUTTON1_PRESSED | curses.BUTTON1_CLICKED | curses.BUTTON1_DOUBLE_CLICKED


def _set_mouse(on: bool) -> None:
    curses.mousemask(CLICKS | WHEEL_UP | WHEEL_DOWN if on else 0)
    curses.mouseinterval(0)  # report presses right away instead of waiting to detect clicks


def _mouse_key() -> str | None:
    """The pending mouse event as a key name ("Click:Y:X", "WheelUp:Y:X", ...)."""
    try:
        _id, x, y, _z, state = curses.getmouse()
    except curses.error:
        return None
    if state & WHEEL_UP:
        return f"WheelUp:{y}:{x}"
    if state & WHEEL_DOWN:
        return f"WheelDown:{y}:{x}"
    if state & CLICKS:
        return f"Click:{y}:{x}"
    return None


def _curses_codes() -> dict[int, str]:
    from .keys import CURSES_NAMES

    codes = {}
    for const, name in CURSES_NAMES.items():
        value = getattr(curses, const, None)
        if isinstance(value, int):
            codes[value] = name
    for n in range(1, 25):
        codes[curses.KEY_F0 + n] = f"F{n}"
    return codes


def _main(stdscr, editor) -> None:
    curses.raw()
    curses.nonl()
    curses.noecho()
    stdscr.keypad(True)
    _set_bracketed_paste(True)
    painter = Painter(stdscr)

    def getch(timeout):
        stdscr.timeout(-1 if timeout is None else int(timeout * 1000))
        try:
            return stdscr.get_wch()
        except curses.error:
            return None

    def keyname(code):
        return curses.keyname(code).decode(errors="replace")

    decoder = KeyDecoder(getch, keyname, _curses_codes())

    def read(timeout):
        key = decoder.read(timeout)
        return _mouse_key() if key == "Mouse" else key

    mouse = False
    try:
        while not editor.quit_requested:
            if editor.settings.mouse != mouse:
                mouse = editor.settings.mouse
                _set_mouse(mouse)
            h, w = stdscr.getmaxyx()
            painter.paint(build_frame(editor, h, w))
            if editor.task is not None:  # animate the spinner until the task is done
                read(0.1)  # input is ignored meanwhile
                editor.poll_task()
                continue
            key = read(None)
            if key is None:
                continue
            editor.handle_key(key)
            # handle everything that's already queued (e.g. a paste) before repainting
            while not editor.quit_requested and not editor.suspend_requested:
                key = read(0)
                if key is None:
                    break
                editor.handle_key(key)
            if editor.suspend_requested:
                editor.suspend_requested = False
                _set_bracketed_paste(False)
                curses.endwin()
                os.kill(os.getpid(), signal.SIGTSTP)
                stdscr.refresh()
                _set_bracketed_paste(True)
    finally:
        _set_bracketed_paste(False)


def run(editor) -> None:
    os.environ.setdefault("ESCDELAY", "25")
    locale.setlocale(locale.LC_ALL, "")
    curses.wrapper(_main, editor)
