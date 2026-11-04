"""Files in the user's home: the settings file, the cursor position log, the recently used commands
and, per working directory, the recently opened files and searches (in the cache).

The settings file holds one option per line, in any of these forms:

    set tab_size 2        # nanorc style ('unset NAME' turns a switch off)
    soft_wrap = on
    guide_column 81

Names and values are the same as for the `set` command (aliases work too).
"""

from __future__ import annotations

import hashlib
import json
import os
import time

from .settings import OPTION_INFO, Settings, resolve_option, set_option

MAX_POSITIONS = 500
MAX_RECENT_COMMANDS = 100
MAX_RECENT_FILES = 100
MAX_RECENT_SEARCHES = 100


def config_path() -> str:
    if os.environ.get("TROLL_CONFIG"):
        return os.environ["TROLL_CONFIG"]
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "troll", "config")


def positions_path() -> str:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(base, "troll", "positions")


def recent_commands_path() -> str:
    return os.path.join(os.path.dirname(positions_path()), "recent_commands")


def dir_stats_path(cwd: str) -> str:
    """The cache file with the stats of the editor sessions started in `cwd`."""
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    key = hashlib.sha256(os.path.realpath(cwd).encode(errors="surrogateescape")).hexdigest()[:24]
    return os.path.join(base, "troll", "dirs", key + ".json")


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


# ---------------------------------------------------------- recent commands


def recent_commands() -> list[str]:
    """Command names picked in the palette, most recent first."""
    try:
        with open(recent_commands_path(), encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except (OSError, UnicodeDecodeError):
        return []


def remember_command(name: str) -> None:
    """Move `name` to the front of the recently used commands (best effort: errors are ignored)."""
    names = [name] + [n for n in recent_commands() if n != name]
    path = recent_commands_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("".join(n + "\n" for n in names[:MAX_RECENT_COMMANDS]))
        os.replace(tmp, path)
    except OSError:
        pass


# --------------------------------------------- per-directory stats (cache)


def load_dir_stats(cwd: str) -> dict:
    """{"cwd", "files": [{"path", "line", "col", "time"}], "searches": [...]} for
    sessions started in `cwd`, most recent first (empty lists if there are none)."""
    cwd = os.path.realpath(cwd)
    stats = {"cwd": cwd, "files": [], "searches": []}
    try:
        with open(dir_stats_path(cwd), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return stats
    if not isinstance(data, dict) or data.get("cwd") != cwd:
        return stats
    stats["files"] = [e for e in data.get("files", []) if isinstance(e, dict) and isinstance(e.get("path"), str)]
    stats["searches"] = [q for q in data.get("searches", []) if isinstance(q, str)]
    return stats


def _update_dir_stats(cwd: str, change) -> None:
    """Re-read the stats (another session may have written them), apply `change` and
    write them back atomically. Best effort: errors are ignored."""
    stats = load_dir_stats(cwd)
    change(stats)
    path = dir_stats_path(cwd)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=1)  # ASCII: undecodable file names survive as \udcXX
            f.write("\n")
        os.replace(tmp, path)
    except (OSError, ValueError):
        pass


def record_open_file(cwd: str, file: str, line: int, col: int) -> None:
    """Note that `file` was opened at `line`:`col` (1-based) by a session started in `cwd`."""
    file = os.path.abspath(file)

    def change(stats):
        entry = {"path": file, "line": line, "col": col, "time": int(time.time())}
        stats["files"] = [entry] + [e for e in stats["files"] if e["path"] != file][: MAX_RECENT_FILES - 1]

    _update_dir_stats(cwd, change)


def record_search(cwd: str, query: str) -> None:
    """Note a search made by a session started in `cwd`."""
    def change(stats):
        stats["searches"] = [query] + [q for q in stats["searches"] if q != query][: MAX_RECENT_SEARCHES - 1]

    _update_dir_stats(cwd, change)
