"""Editor options. Every option can be changed at runtime with `set NAME VALUE`."""

from __future__ import annotations

from dataclasses import dataclass, fields, replace


@dataclass
class Settings:
    tab_size: int = 4
    expand_tabs: bool = True
    auto_indent: bool = True
    auto_pair: bool = True
    comment_continue: bool = True
    line_numbers: bool = True
    fill_width: int = 80  # used by justify
    trim_trailing: str = "edited"  # "edited" | "all" | "off"
    final_newline: bool = True
    highlight: bool = True
    show_whitespace: bool = False
    smart_home: bool = True
    help_lines: bool = True
    case_sensitive: bool = False
    regex_search: bool = False
    fold_context: int = 3

    def copy(self) -> "Settings":
        return replace(self)


ALIASES = {
    "tabsize": "tab_size",
    "tabs": "tab_size",
    "ts": "tab_size",
    "expandtab": "expand_tabs",
    "et": "expand_tabs",
    "spaces": "expand_tabs",
    "autoindent": "auto_indent",
    "ai": "auto_indent",
    "autopair": "auto_pair",
    "pairs": "auto_pair",
    "commentcontinue": "comment_continue",
    "linenumbers": "line_numbers",
    "numbers": "line_numbers",
    "nu": "line_numbers",
    "fill": "fill_width",
    "width": "fill_width",
    "wrap": "fill_width",
    "trim": "trim_trailing",
    "finalnewline": "final_newline",
    "eol": "final_newline",
    "syntax": "highlight",
    "whitespace": "show_whitespace",
    "ws": "show_whitespace",
    "smarthome": "smart_home",
    "help": "help_lines",
    "case": "case_sensitive",
    "regex": "regex_search",
    "context": "fold_context",
}

TRUE = {"1", "on", "yes", "true", "y"}
FALSE = {"0", "off", "no", "false", "n"}


def option_names() -> list[str]:
    return [f.name for f in fields(Settings)]


def resolve_option(name: str) -> str | None:
    key = name.strip().lower().replace("-", "_")
    if key in option_names():
        return key
    return ALIASES.get(key.replace("_", ""))


def set_option(settings: Settings, name: str, value: str | None) -> str:
    """Set an option from user text. Returns a human readable confirmation.

    For booleans a missing value toggles. Raises ValueError on bad input.
    """
    key = resolve_option(name)
    if key is None:
        raise ValueError(f"Unknown option '{name}'")
    current = getattr(settings, key)
    if isinstance(current, bool):
        if value is None or value == "":
            new: object = not current
        elif value.lower() in TRUE:
            new = True
        elif value.lower() in FALSE:
            new = False
        else:
            raise ValueError(f"'{value}' is not on/off")
    elif isinstance(current, int):
        if value is None:
            raise ValueError(f"{key} needs a number")
        try:
            new = int(value)
        except ValueError:
            raise ValueError(f"'{value}' is not a number") from None
        if new <= 0:
            raise ValueError(f"{key} must be positive")
    else:
        if key == "trim_trailing":
            if value not in ("edited", "all", "off"):
                raise ValueError("trim must be one of: edited, all, off")
        new = value or ""
    setattr(settings, key, new)
    shown = ("on" if new else "off") if isinstance(new, bool) else new
    return f"{key} = {shown}"
