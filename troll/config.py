"""Files in the user's home: the settings file and the cursor position log.

The settings file holds one option per line, in any of these forms:

    set tab_size 2        # nanorc style ('unset NAME' turns a switch off)
    soft_wrap = on
    guide_column 81

Names and values are the same as for the `set` command (aliases work too).
"""

from __future__ import annotations

import os

from .settings import OPTION_INFO, Settings, resolve_option, set_option

MAX_POSITIONS = 500


def config_path() -> str:
    if os.environ.get("TROLL_CONFIG"):
        return os.environ["TROLL_CONFIG"]
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "troll", "config")


def positions_path() -> str:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(base, "troll", "positions")


# ------------------------------------------------------------------ settings


def parse_line(text: str) -> tuple[str, str | None] | None:
    """(option name, value) for a settings line, None for blank/comment lines.

    Raises ValueError for lines that don't name a known option.
    """
    text = text.split("#", 1)[0].strip()
    if not text:
        return None
    if "=" in text:
        name, value = (part.strip() for part in text.split("=", 1))
    else:
        words = text.split(None, 2)
        verb = words[0].lower()
        if verb == "unset" and len(words) == 2:
            name, value = words[1], "off"
        elif verb == "set" and len(words) >= 2:
            name, value = words[1], (words[2] if len(words) > 2 else None)
        else:
            name, value = words[0], (" ".join(words[1:]) or None)
    key = resolve_option(name)
    if key is None:
        raise ValueError(f"Unknown option '{name}'")
    return key, value


def load_config(settings: Settings, path: str | None = None) -> list[str]:
    """Apply the settings file to `settings`; returns error messages (never raises)."""
    path = path or config_path()
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return []
    except OSError as e:
        return [f"{path}: {e.strerror or e}"]
    errors = []
    for n, text in enumerate(lines, 1):
        try:
            parsed = parse_line(text)
            if parsed is None:
                continue
            key, value = parsed
            if value is None and isinstance(getattr(settings, key), bool):
                value = "on"  # 'set NAME' switches on (it doesn't toggle)
            set_option(settings, key, value)
        except ValueError as e:
            errors.append(f"{os.path.basename(path)} line {n}: {e}")
    return errors


def _format(value) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    return str(value)


def save_config(settings: Settings, path: str | None = None) -> tuple[str, int]:
    """Write every option that differs from the default (or is already in the
    file) into the settings file. Other lines and comments are kept.
    Returns (path, number of options written)."""
    path = path or config_path()
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        lines = ["# troll settings (see 'set' in the help screen for the option names)"]
    where: dict[str, int] = {}
    for i, text in enumerate(lines):
        try:
            parsed = parse_line(text)
        except ValueError:
            continue
        if parsed is not None:
            where[parsed[0]] = i
    defaults = Settings()
    written = 0
    for key in OPTION_INFO:
        value = getattr(settings, key)
        if value == getattr(defaults, key) and key not in where:
            continue
        line = f"set {key} {_format(value)}"
        if key in where:
            lines[where[key]] = line
        else:
            lines.append(line)
        written += 1
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, path)
    return path, written


# ----------------------------------------------------------------- positions


def _read_positions(path: str) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    try:
        with open(path, encoding="utf-8", errors="surrogateescape") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t", 2)
                if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
                    out.pop(parts[2], None)  # keep the most recent last
                    out[parts[2]] = (int(parts[0]), int(parts[1]))
    except OSError:
        pass
    return out


def recall_position(file: str) -> tuple[int, int] | None:
    return _read_positions(positions_path()).get(os.path.abspath(file))


def remember_positions(entries: dict[str, tuple[int, int]]) -> None:
    """Record the cursor position of each file (best effort: errors are ignored)."""
    if not entries:
        return
    path = positions_path()
    known = _read_positions(path)
    for file, pos in entries.items():
        file = os.path.abspath(file)
        known.pop(file, None)
        known[file] = pos
    items = list(known.items())[-MAX_POSITIONS:]
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", errors="surrogateescape") as f:
            for file, (r, c) in items:
                f.write(f"{r}\t{c}\t{file}\n")
        os.replace(tmp, path)
    except OSError:
        pass
