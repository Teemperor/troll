"""Copying text to the system clipboard.

`copy(text)` tries the platform's clipboard tool (pbcopy, wl-copy, xclip, ...)
and returns its name, or None if there is none (e.g. over ssh). The TUI then
falls back to an OSC 52 escape sequence, which many terminals (and tmux) turn
into a clipboard write even on a remote machine.
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys


def tools() -> list[list[str]]:
    """Clipboard commands that may work here, best first."""
    if os.environ.get("SSH_CONNECTION") and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        return []  # the remote machine's clipboard is not the user's
    out = []
    if sys.platform == "darwin":
        out.append(["pbcopy"])
    if os.environ.get("WAYLAND_DISPLAY"):
        out.append(["wl-copy"])
    if os.environ.get("DISPLAY"):
        out += [["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]]
    if sys.platform in ("win32", "cygwin") or (hasattr(os, "uname") and "microsoft" in os.uname().release.lower()):
        out.append(["clip.exe"])
    return [argv for argv in out if shutil.which(argv[0])]


def copy(text: str) -> str | None:
    """Put `text` on the clipboard; the name of the tool that did it, or None."""
    for argv in tools():
        try:
            # no pipes for stdout/stderr: xclip keeps running to serve the selection
            proc = subprocess.run(argv, input=text.encode(errors="surrogateescape"), stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, timeout=3)
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode == 0:
            return argv[0]
    return None


def osc52(text: str) -> str:
    """The escape sequence asking the terminal to put `text` on the clipboard."""
    data = base64.b64encode(text.encode(errors="surrogateescape")).decode("ascii")
    seq = f"\x1b]52;c;{data}\x07"
    if os.environ.get("TMUX"):  # pass it through to the outer terminal too
        seq += "\x1bPtmux;\x1b" + seq.replace("\x1b", "\x1b\x1b") + "\x1b\\"
    return seq
