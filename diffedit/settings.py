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
    side_by_side: bool = False

    def copy(self) -> "Settings":
        return replace(self)


@dataclass(frozen=True)
class OptionInfo:
    key: str
    label: str
    help: str
    section: str
    choices: tuple[str, ...] = ()  # for string options
    minimum: int = 1  # for numbers
    maximum: int = 999
    editor_wide: bool = False  # not per file (search flags, the help bar)
    all_files: bool = False  # applies to every open file at once (e.g. how commit diffs are shown)


OPTIONS: tuple[OptionInfo, ...] = (
    OptionInfo("tab_size", "Tab / indent width", "Columns per tab and per indentation level", "Indentation", maximum=16),
    OptionInfo("expand_tabs", "Indent with spaces", "Insert spaces instead of tab characters", "Indentation"),
    OptionInfo("auto_indent", "Auto indent", "Keep and adjust indentation when pressing Enter", "Indentation"),
    OptionInfo("smart_home", "Smart Home key", "Home jumps to the first non-blank character first", "Indentation"),
    OptionInfo("auto_pair", "Auto-close brackets", "Insert the closing bracket/quote (never inside comments)", "Formatting"),
    OptionInfo("comment_continue", "Continue comments", "Enter inside a comment or list starts a new comment line", "Formatting"),
    OptionInfo("fill_width", "Justify width", "Line width used by ^J (justify)", "Formatting", minimum=10, maximum=500),
    OptionInfo("trim_trailing", "Trim trailing spaces", "On save: only lines you edited, every line, or never",
               "Saving", choices=("edited", "all", "off")),
    OptionInfo("final_newline", "Ensure final newline", "End the file with a newline when saving (if it had one)", "Saving"),
    OptionInfo("highlight", "Syntax highlighting", "Color the text according to its language", "Display"),
    OptionInfo("line_numbers", "Line numbers", "Show line numbers in the gutter", "Display"),
    OptionInfo("show_whitespace", "Show whitespace", "Make tabs and trailing spaces visible", "Display"),
    OptionInfo("help_lines", "Shortcut bar", "Show the two lines of shortcuts at the bottom", "Display", editor_wide=True),
    OptionInfo("case_sensitive", "Case-sensitive search", "Also toggled with M-C in the search prompt", "Search", editor_wide=True),
    OptionInfo("regex_search", "Regex search", "Also toggled with M-R in the search prompt", "Search", editor_wide=True),
    OptionInfo("side_by_side", "Side-by-side diff",
               "Your editable version on the left, the version before the commit on the right",
               "Commit editing", all_files=True),
    OptionInfo("fold_context", "Context lines", "Unchanged lines shown around each change when editing a commit",
               "Commit editing", maximum=50, all_files=True),
)
OPTION_INFO = {o.key: o for o in OPTIONS}

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
    "sidebyside": "side_by_side",
    "split": "side_by_side",
    "sbs": "side_by_side",
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
        info = OPTION_INFO.get(key)
        lo, hi = (info.minimum, info.maximum) if info else (1, 999)
        if not lo <= new <= hi:
            raise ValueError(f"{key} must be between {lo} and {hi}")
    else:
        info = OPTION_INFO.get(key)
        if info and info.choices and value not in info.choices:
            raise ValueError(f"{key} must be one of: {', '.join(info.choices)}")
        new = value or ""
    setattr(settings, key, new)
    shown = ("on" if new else "off") if isinstance(new, bool) else new
    return f"{key} = {shown}"
