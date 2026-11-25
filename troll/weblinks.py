"""Opening web pages: pull requests named in commit subjects.

`pull_request(subject)` finds the PR number a merge tool put in a commit
subject ("Fix the frobnicator (#1234)"); `pr_link(template, number)` turns it
into a URL; `open_url(url)` hands it to the system's browser (`open` on macOS,
`xdg-open` and friends elsewhere) without blocking or writing to the terminal.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import threading

PR_RE = re.compile(r"\(#(\d+)\)")


def pull_request(subject: str) -> int | None:
    """The PR number in a commit subject: the last "(#N)" (GitHub's squash merges append it)."""
    found = PR_RE.findall(subject)
    return int(found[-1]) if found else None


def pr_link(template: str, number: int) -> str:
    """The URL of PR `number`: `{}` in the template is replaced by it, otherwise it's appended."""
    template = template.strip()
    if "{}" in template:
        return template.replace("{}", str(number))
    return template.rstrip("/") + f"/{number}"


def openers() -> list[list[str]]:
    """Commands that may open a URL here, best first."""
    if sys.platform == "darwin":
        out = [["open"]]
    elif sys.platform in ("win32", "cygwin"):
        out = [["explorer.exe"]]
    else:
        out = [["xdg-open"], ["gio", "open"], ["wslview"], ["sensible-browser"]]
    return [argv for argv in out if shutil.which(argv[0])]


def open_url(url: str) -> str | None:
    """Open `url` in the browser; the name of the tool that did it, or None."""
    for argv in openers():
        try:
            # a new session and no inherited terminal: the browser mustn't draw over (or read from) the TUI
            proc = subprocess.Popen([*argv, url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            continue
        threading.Thread(target=proc.wait, daemon=True).start()  # reap it (xdg-open may take a while)
        return argv[0]
    return None
