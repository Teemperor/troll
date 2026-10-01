"""Command registry: every action has a name, a help text and optional keys.

The same table drives the key bindings, the command palette (^T) and the
help screen, so they can never disagree. Commands take `(editor, args)`.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from . import languages
from .search import count_words
from .settings import FALSE, TRUE, resolve_option, set_option

if TYPE_CHECKING:  # pragma: no cover
    from .editor import Editor


@dataclass
class Command:
    name: str
    func: Callable[["Editor", str], None]
    help: str
    keys: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    args: str = ""  # argument hint, e.g. "LINE[:COL]"
    prompt: str | None = None  # ask for args with this label when none given
    needs_doc: bool = True
    overview: bool = False  # also usable on the commit overview screen
    category: str = "Editing"
    commit_only: bool = False
    hidden: bool = False  # not listed in the palette (plain movement keys)


COMMANDS: dict[str, Command] = {}
CATEGORIES = ["File", "Editing", "Search", "Movement", "Formatting", "Commit", "Display", "Info"]


def command(name: str, help: str, keys=(), aliases=(), args="", prompt=None, needs_doc=True, overview=False,
            category="Editing", commit_only=False, hidden=False):
    def deco(fn):
        COMMANDS[name] = Command(name, fn, help, tuple(keys), tuple(aliases), args, prompt, needs_doc, overview,
                                 category, commit_only, hidden)
        return fn

    return deco


SPECIAL_KEYS = {
    "Enter", "Tab", "S-Tab", "Backspace", "Delete", "Insert", "Home", "End", "PageUp", "PageDown",
    "Up", "Down", "Left", "Right", "Esc", "Resize", "PasteStart", "PasteEnd",
}


def build_keymap() -> dict[str, str]:
    keymap: dict[str, str] = {}
    for cmd in COMMANDS.values():
        for key in cmd.keys:
            keymap[key] = cmd.name
    return keymap


def key_label(key: str) -> str:
    """Human readable key name in nano's notation (^W, M-U, ...)."""
    if key.startswith("C-") and len(key) == 3:
        return "^" + key[2].upper()
    if key in ("C-\\", "C-]", "C-^", "C-_"):
        return "^" + key[2]
    if key == "C-Space":
        return "^Space"
    if key.startswith("M-") and len(key) == 3:
        return "M-" + key[2].upper()
    arrows = {"Up": "↑", "Down": "↓", "Left": "←", "Right": "→"}
    for name, arrow in arrows.items():
        if key.endswith(name) and key != name:
            return key[: -len(name)].replace("C-", "^").replace("S-", "Sh-") + arrow
    return key.replace("C-", "^").replace("S-", "Sh-")


def find_command(word: str) -> Command | None:
    word = word.strip().lower()
    if not word:
        return None
    if word in COMMANDS:
        return COMMANDS[word]
    for cmd in COMMANDS.values():
        if word in cmd.aliases:
            return cmd
    matches = [c for c in COMMANDS.values() if c.name.startswith(word) and not c.hidden]
    if len(matches) == 1:
        return matches[0]
    return None


SED_RE = re.compile(r"s([/|#,])(.*?)(?<!\\)\1(.*?)(?:\1([gi]*))?")


def is_special_line(text: str) -> bool:
    return bool(text) and (text[0] in "!/:+-" or text[0].isdigit() or SED_RE.fullmatch(text) is not None)


def run_command_line(ed: "Editor", line: str) -> None:
    line = line.strip()
    if line.startswith(":"):
        line = line[1:].strip()
    if not line:
        return
    if line[0] == "!":
        if ed.doc is None:
            ed.error("No file open")
            return
        ed.run_shell(line[1:].strip())
        return
    if line[0] == "/":
        ed.last_search = line[1:]
        ed.find_next()
        return
    if re.fullmatch(r"[-+]?\d+(?:[,:]\d+)?", line):
        ed.goto_text(line)
        return
    m = SED_RE.fullmatch(line)
    if m and ed.doc is not None:
        sep, old, new, flags = m.group(1), m.group(2), m.group(3), m.group(4) or ""
        old = old.replace("\\" + sep, sep)
        new = new.replace("\\" + sep, sep)
        ed.sed_replace(old, new, ignore_case="i" in flags)
        return
    word, _, args = line.partition(" ")
    cmd = find_command(word)
    if cmd is None:
        ed.error(f"Unknown command '{word}' (press ^T to browse commands)")
        return
    if cmd.commit_only and ed.commit is None:
        ed.error(f"'{cmd.name}' only works while editing a commit")
        return
    args = args.strip()
    if not args and cmd.prompt:
        invoke_interactive(ed, cmd)
        return
    ed.run(cmd.name, args)


def invoke_interactive(ed: "Editor", cmd: Command) -> None:
    """Run a command picked in the palette, asking for arguments if needed."""
    if cmd.prompt:
        ed.ask(cmd.prompt, lambda text: cmd.func(ed, text.strip()) if text.strip() else ed.info("Cancelled"))
    else:
        ed.run(cmd.name, "")


def parse_goto(text: str, doc) -> tuple[int, int] | None:
    """nano-style "LINE[,COL]" (1-based, negative counts from the end)."""
    text = text.strip()
    m = re.fullmatch(r"([-+]?\d+)?\s*(?:[,:\s]\s*([-+]?\d+))?", text)
    if not text or not m or (m.group(1) is None and m.group(2) is None):
        return None
    n = len(doc.lines) - (1 if len(doc.lines) > 1 and doc.lines[-1] == "" else 0)
    if m.group(1) is None:
        row = doc.cursor[0]
    else:
        line = int(m.group(1))
        row = line - 1 if line > 0 else n + line if line < 0 else 0
    row = max(0, min(row, len(doc.lines) - 1))
    col = 0
    if m.group(2) is not None:
        c = int(m.group(2))
        length = len(doc.lines[row])
        col = c - 1 if c > 0 else length + c + 1 if c < 0 else 0
        col = max(0, min(col, length))
    return row, col


# ======================================================================
# File
# ======================================================================


@command("help", "Show the help screen", keys=("C-g", "F1"), needs_doc=False, overview=True, category="File")
def _help(ed, args):
    ed.show_help()


@command("commands", "Open the command palette", keys=("C-t", "M-x"), needs_doc=False, overview=True,
         aliases=("palette", "execute"), category="File")
def _palette(ed, args):
    ed.palette(args)


@command("exit", "Close the buffer (asks to save); in a commit: back to the file list / leave",
         keys=("C-x", "F2"), needs_doc=False, overview=True, aliases=("close",), category="File")
def _exit(ed, args):
    ed.exit()


@command("write-out", "Write the file, asking for a name (in a commit: rewrite it)", keys=("C-o", "F3"),
         overview=True, needs_doc=False, category="File")
def _write_out(ed, args):
    if ed.commit is None and ed.doc is None:
        return
    ed.save(prompt_name=True)


@command("save", "Save the file (in a commit: rewrite the commit)", keys=("C-s",), aliases=("w", "write"),
         args="[FILE]", overview=True, needs_doc=False, category="File")
def _save(ed, args):
    if ed.commit is not None:
        ed.apply_commit()
    elif ed.doc is None:
        return
    elif args:
        ed._save_as(ed.doc, args)
    else:
        ed.save()


@command("save-as", "Save under a new file name", args="FILE", prompt="File Name to Write", category="File")
def _save_as(ed, args):
    ed._save_as(ed.doc, args)


@command("insert-file", "Insert a file at the cursor", keys=("C-r", "F5"), args="FILE", category="File",
         aliases=("read",))
def _insert_file(ed, args):
    if args:
        ed.insert_file(args)
    else:
        ed.insert_file_prompt()


@command("open", "Open a file in a new buffer", args="FILE", prompt="File to open", needs_doc=False,
         aliases=("e", "edit", "o"), category="File")
def _open(ed, args):
    if ed.commit is not None:
        ed.error("Leave the commit editor first (^X)")
        return
    ed.open_file(args)


@command("new", "Open a new empty buffer", needs_doc=False, category="File")
def _new(ed, args):
    if ed.commit is not None:
        ed.error("Leave the commit editor first (^X)")
        return
    ed.new_doc()


@command("next-buffer", "Switch to the next buffer / commit file", keys=("M->", "M-."), needs_doc=False,
         overview=True, aliases=("bn",), category="File")
def _next_buffer(ed, args):
    ed.switch_doc(1)


@command("prev-buffer", "Switch to the previous buffer / commit file", keys=("M-<", "M-,"), needs_doc=False,
         overview=True, aliases=("bp",), category="File")
def _prev_buffer(ed, args):
    ed.switch_doc(-1)


@command("buffers", "Pick an open buffer (or a file of the commit)", needs_doc=False, overview=True,
         aliases=("ls", "files"), category="File")
def _buffers(ed, args):
    ed.buffer_picker()


@command("quit", "Quit, asking about each unsaved buffer", needs_doc=False, aliases=("q",), overview=True,
         category="File")
def _quit(ed, args):
    ed.quit_all(force=False)


@command("quit!", "Quit immediately, discarding all unsaved changes", needs_doc=False, aliases=("q!",),
         overview=True, category="File")
def _quit_force(ed, args):
    ed.quit_all(force=True)


@command("suspend", "Suspend the editor (fg to resume)", keys=("C-z",), needs_doc=False, category="File")
def _suspend(ed, args):
    ed.suspend_requested = True


# ======================================================================
# Editing
# ======================================================================


@command("enter", "Insert a newline (with smart indentation)", keys=("Enter", "C-m"), hidden=True)
def _enter(ed, args):
    ed.doc.newline()


@command("tab", "Indent (selection) or insert a tab", keys=("Tab", "C-i"), hidden=True)
def _tab(ed, args):
    ed.doc.tab()


@command("backspace", "Delete the character before the cursor", keys=("Backspace", "C-h"), hidden=True)
def _backspace(ed, args):
    ed.doc.backspace()


@command("delete", "Delete the character under the cursor", keys=("Delete", "C-d"), hidden=True)
def _delete(ed, args):
    ed.doc.delete_forward()


@command("delete-word-back", "Delete the word before the cursor", keys=("M-Backspace", "C-Backspace"))
def _dwb(ed, args):
    ed.doc.delete_word_back()


@command("delete-word", "Delete the word after the cursor", keys=("C-Delete", "M-Delete"))
def _dw(ed, args):
    ed.doc.delete_word_forward()


@command("cut", "Cut the current line (or the selection) into the cutbuffer", keys=("C-k", "F9"))
def _cut(ed, args):
    ed.cut()


@command("copy", "Copy the current line (or the selection)", keys=("M-6", "M-^"))
def _copy(ed, args):
    ed.copy()


@command("paste", "Paste the cutbuffer", keys=("C-u", "F10"), aliases=("uncut",))
def _paste(ed, args):
    ed.paste()


@command("cut-to-end", "Cut from the cursor to the end of the line", keys=("M-t",))
def _cut_to_end(ed, args):
    text = ed.doc.cut_to_end()
    if ed.last_command == "cut-to-end" and ed.cutbuffer:
        ed.cutbuffer += text
    else:
        ed.cutbuffer = text


@command("mark", "Set/unset the mark to select text (or use Shift+arrows)", keys=("M-a", "C-^", "C-6"),
         aliases=("select",))
def _mark(ed, args):
    doc = ed.doc
    if doc.mark is not None:
        doc.clear_mark()
        ed.info("Mark Unset")
    else:
        doc.set_mark()
        ed.info("Mark Set")


@command("select-all", "Select the whole buffer", aliases=("all",))
def _select_all(ed, args):
    doc = ed.doc
    doc.mark = (0, 0)
    doc.shift_select = True
    doc.cursor = doc.buffer.end()


@command("undo", "Undo the last change", keys=("M-u",))
def _undo(ed, args):
    if not ed.doc.undo():
        ed.info("Nothing to undo")


@command("redo", "Redo the last undone change", keys=("M-e",))
def _redo(ed, args):
    if not ed.doc.redo():
        ed.info("Nothing to redo")


@command("indent", "Indent the current line / selection", keys=("M-}",))
def _indent(ed, args):
    ed.doc.indent_selection()


@command("unindent", "Unindent the current line / selection", keys=("M-{", "S-Tab"))
def _unindent(ed, args):
    ed.doc.unindent_selection()


@command("comment", "Comment/uncomment the current line / selection", keys=("M-3",), aliases=("toggle-comment",))
def _comment(ed, args):
    if not ed.doc.toggle_comment():
        ed.error(f"Don't know how to comment {ed.doc.lang.name}")


@command("duplicate", "Duplicate the current line / selected lines", aliases=("dup",))
def _duplicate(ed, args):
    ed.doc.duplicate_line()


@command("move-up", "Move the current line / selected lines up", keys=("M-S-Up",))
def _move_up(ed, args):
    ed.doc.move_lines(-1)


@command("move-down", "Move the current line / selected lines down", keys=("M-S-Down",))
def _move_down(ed, args):
    ed.doc.move_lines(1)


@command("join", "Join the next line (or the selected lines) onto this one", aliases=("join-lines",))
def _join(ed, args):
    if not ed.doc.join_lines():
        ed.info("Nothing to join")


@command("shell", "Insert the output of a shell command, or filter the selection through it (also: !cmd)",
         args="COMMAND", prompt="Command to execute", aliases=("!", "run", "filter"))
def _shell(ed, args):
    ed.run_shell(args)


@command("replace-all", "Replace every match without asking", args="OLD NEW", prompt="Replace all (OLD NEW)",
         category="Search")
def _replace_all(ed, args):
    parts = args.split(" ", 1)
    if len(parts) != 2:
        ed.error("Usage: replace-all OLD NEW")
        return
    ed.start_replace(parts[0], parts[1], confirm=False)


# ======================================================================
# Formatting
# ======================================================================


@command("justify", "Reflow the paragraph / comment block / selection", keys=("C-j", "F4"), category="Formatting",
         aliases=("reflow", "wrap"))
def _justify(ed, args):
    if args:
        setattr(ed.doc.settings, "message_width" if ed.doc.is_message else "fill_width", int(args))
    if not ed.doc.justify():
        ed.info("Nothing to justify here")
    else:
        ed.info("Justified")


@command("justify-all", "Reflow every paragraph in the buffer", keys=("M-j",), category="Formatting")
def _justify_all(ed, args):
    if not ed.doc.justify(whole=True):
        ed.info("Already justified")


@command("format", "Format the buffer with the language's formatter (ruff/black, clang-format, gofmt, ...)",
         category="Formatting", aliases=("fmt",))
def _format(ed, args):
    ed.format_doc()


@command("trim", "Remove trailing whitespace (selection or whole buffer)", category="Formatting")
def _trim(ed, args):
    doc = ed.doc
    rows = None
    if doc.selection() is not None:
        r1, r2 = doc.selected_rows()
        rows = list(range(r1, r2 + 1))
    n = doc.trim_trailing(rows)
    ed.info(f"Trimmed {n} line{'s' if n != 1 else ''}")


@command("sort", "Sort the selected lines (or the buffer)", category="Formatting", args="[reverse]")
def _sort(ed, args):
    rev = args.strip().lower() in ("r", "reverse", "desc", "-r")
    if not ed.doc.transform_rows(lambda lines: sorted(lines, reverse=rev)):
        ed.info("Already sorted")


@command("uniq", "Remove duplicate adjacent lines (selection or buffer)", category="Formatting")
def _uniq(ed, args):
    def uniq(lines):
        out = []
        for line in lines:
            if not out or out[-1] != line:
                out.append(line)
        return out

    ed.doc.transform_rows(uniq)


@command("upper", "UPPERCASE the selection / line", category="Formatting", aliases=("uppercase",))
def _upper(ed, args):
    ed.doc.transform_selection(str.upper)


@command("lower", "lowercase the selection / line", category="Formatting", aliases=("lowercase",))
def _lower(ed, args):
    ed.doc.transform_selection(str.lower)


@command("tabs-to-spaces", "Convert indentation tabs to spaces", category="Formatting", aliases=("expand",))
def _expand(ed, args):
    size = ed.doc.settings.tab_size
    ed.doc.transform_rows(lambda lines: [l.expandtabs(size) for l in lines])
    ed.doc.settings.expand_tabs = True


@command("spaces-to-tabs", "Convert leading spaces to tabs", category="Formatting", aliases=("unexpand",))
def _unexpand(ed, args):
    size = ed.doc.settings.tab_size

    def conv(lines):
        out = []
        for l in lines:
            ws = len(l) - len(l.lstrip(" "))
            out.append("\t" * (ws // size) + " " * (ws % size) + l[ws:])
        return out

    ed.doc.transform_rows(conv)
    ed.doc.settings.expand_tabs = False


# ======================================================================
# Search
# ======================================================================


@command("search", "Search forward (M-C case, M-R regex, M-B backwards)", keys=("C-w", "F6"),
         aliases=("find", "where-is"), args="TEXT", category="Search")
def _search(ed, args):
    if args:
        ed.last_search = args
        ed.find_next()
    else:
        ed.search_prompt()


@command("replace", "Search and replace, confirming each match", keys=("C-\\", "M-r"), category="Search",
         args="OLD NEW")
def _replace(ed, args):
    parts = args.split(" ", 1) if args else []
    if len(parts) == 2:
        ed.start_replace(parts[0], parts[1])
    else:
        ed.replace_prompt(args)


@command("find-next", "Repeat the last search forward", keys=("M-w", "F16"), category="Search", aliases=("n",))
def _find_next(ed, args):
    ed.find_next()


@command("find-prev", "Repeat the last search backwards", keys=("M-q",), category="Search", aliases=("N",))
def _find_prev(ed, args):
    ed.find_next(backwards=True)


@command("goto", "Go to a line (and column): 'goto 42', 'goto 42:7', 'goto -1'", keys=("C-_", "M-g", "F13"),
         args="LINE[:COL]", prompt="Enter line number, column number", aliases=("line", "g"), category="Search")
def _goto(ed, args):
    ed.goto_text(args)


@command("bracket", "Jump to the matching bracket", keys=("M-]",), category="Search", aliases=("match",))
def _bracket(ed, args):
    if not ed.doc.goto_matching_bracket():
        ed.info("Not a bracket")


# ======================================================================
# Movement (mostly hidden from the palette)
# ======================================================================


def _mv(method, select=False, *margs):
    def fn(ed, args):
        getattr(ed.doc, method)(*margs, select=select)
    return fn


for _name, _method, _keys in [
    ("left", "move_left", ("Left", "C-b")),
    ("right", "move_right", ("Right", "C-f")),
    ("up", "move_up", ("Up", "C-p")),
    ("down", "move_down", ("Down", "C-n")),
    ("home", "move_home", ("Home", "C-a")),
    ("end", "move_end", ("End", "C-e")),
    ("word-left", "move_word_left", ("C-Left", "M-Space")),
    ("word-right", "move_word_right", ("C-Right", "C-Space")),
    ("top", "move_top", ("M-\\", "C-Home", "M-Home")),
    ("bottom", "move_bottom", ("M-/", "C-End", "M-End")),
]:
    command(_name, f"Move {_name.replace('-', ' ')}", keys=_keys, hidden=_name not in ("top", "bottom"),
            category="Movement")(_mv(_method))
    shifted = tuple("S-" + k for k in _keys if not k.startswith(("C-", "M-")) or k.startswith(("C-Left", "C-Right", "C-Home", "C-End")))
    shifted = tuple(k.replace("S-C-", "C-S-") for k in shifted)
    command("select-" + _name, f"Extend the selection {_name.replace('-', ' ')}", keys=shifted, hidden=True,
            category="Movement")(_mv(_method, True))


def _page(direction, select=False):
    def fn(ed, args):
        doc = ed.doc
        n = max(1, ed.body_height - 2)
        doc.move_vertical(direction * n, select=select)
        doc.scroll_row = max(0, doc.scroll_row + direction * n)
    return fn


command("page-up", "Scroll up one page", keys=("PageUp", "C-y", "F7"), hidden=True, category="Movement")(_page(-1))
command("page-down", "Scroll down one page", keys=("PageDown", "C-v", "F8"), hidden=True, category="Movement")(_page(1))
command("select-page-up", "Select a page up", keys=("S-PageUp",), hidden=True, category="Movement")(_page(-1, True))
command("select-page-down", "Select a page down", keys=("S-PageDown",), hidden=True, category="Movement")(_page(1, True))


@command("center", "Center the view on the cursor", keys=("C-l",), category="Movement")
def _center(ed, args):
    ed.doc.center_pending = True


@command("scroll-up", "Scroll the view up one line", keys=("M--", "C-Up"), hidden=True, category="Movement")
def _scroll_up(ed, args):
    doc = ed.doc
    doc.scroll_row = max(0, doc.scroll_row - 1)
    ed.keep_cursor_in_view()


@command("scroll-down", "Scroll the view down one line", keys=("M-=", "M-+", "C-Down"), hidden=True,
         category="Movement")
def _scroll_down(ed, args):
    doc = ed.doc
    doc.scroll_row = min(max(0, doc.display_count() - 1), doc.scroll_row + 1)
    ed.keep_cursor_in_view()


# ======================================================================
# Info & display
# ======================================================================


@command("location", "Show the cursor position", keys=("C-c",), category="Info", aliases=("pos", "where"))
def _location(ed, args):
    doc = ed.doc
    r, c = doc.cursor
    n = len(doc.lines)
    line = doc.lines[r]
    before = sum(len(l) + 1 for l in doc.lines[:r]) + c
    total = max(1, len(doc.text()))
    ed.info(
        f"line {r + 1}/{n} ({100 * (r + 1) // n}%), col {c + 1}/{len(line) + 1} "
        f"({100 * (c + 1) // (len(line) + 1)}%), char {before + 1}/{total + 1} ({100 * (before + 1) // (total + 1)}%)"
    )


@command("wordcount", "Count lines, words and characters", keys=("M-d",), category="Info", aliases=("wc",))
def _wordcount(ed, args):
    doc = ed.doc
    sel = doc.selection()
    text = doc.buffer.get_text(*sel) if sel else doc.text()
    lines, words, chars = count_words(text)
    ed.info(f"{'In Selection: ' if sel else ''}Lines: {lines}  Words: {words}  Chars: {chars}")


def _toggle(attr, label, per_doc=True):
    def fn(ed, args):
        target = ed.doc.settings if (per_doc and ed.doc is not None) else ed.settings
        value = not getattr(target, attr)
        setattr(target, attr, value)
        if per_doc and ed.doc is not None and target is not ed.settings:
            setattr(ed.settings, attr, value)
        ed.info(f"{label} {'enabled' if value else 'disabled'}")
    return fn


command("line-numbers", "Toggle line numbers", keys=("M-n", "M-#"), category="Display", needs_doc=False,
        aliases=("numbers",))(_toggle("line_numbers", "Line numbering"))
command("whitespace", "Toggle showing tabs and trailing spaces", keys=("M-p",), category="Display",
        needs_doc=False)(_toggle("show_whitespace", "Whitespace display"))
command("help-lines", "Toggle the shortcut lines at the bottom", keys=("M-X",), category="Display",
        needs_doc=False, overview=True)(_toggle("help_lines", "Help lines", per_doc=False))
command("auto-indent", "Toggle automatic indentation", keys=("M-i",), category="Display",
        needs_doc=False)(_toggle("auto_indent", "Auto indent"))
command("syntax", "Toggle syntax highlighting", keys=("M-y",), category="Display",
        needs_doc=False)(_toggle("highlight", "Syntax highlighting"))
command("auto-pair", "Toggle automatic bracket/quote pairing", category="Display",
        needs_doc=False)(_toggle("auto_pair", "Auto pairing"))
command("soft-wrap", "Toggle wrapping long lines on screen", keys=("M-s",), category="Display",
        needs_doc=False, aliases=("softwrap", "wrap-lines"))(_toggle("soft_wrap", "Soft wrapping"))
command("hard-wrap", "Toggle breaking long lines while typing", keys=("M-l",), category="Display",
        needs_doc=False, aliases=("breaklonglines",))(_toggle("hard_wrap", "Hard wrapping"))
command("constant-show", "Toggle always showing the cursor position", keys=("M-c",), category="Display",
        needs_doc=False, aliases=("show-position",))(_toggle("show_position", "Constant cursor position display",
                                                             per_doc=False))
command("mouse", "Toggle mouse support (click to place the cursor, wheel to scroll)", keys=("M-m",),
        category="Display", needs_doc=False)(_toggle("mouse", "Mouse support", per_doc=False))
command("cursor-line", "Toggle highlighting the cursor line", category="Display", needs_doc=False,
        aliases=("cursorline",))(_toggle("cursor_line", "Cursor line highlighting"))
command("relative-numbers", "Toggle line numbers relative to the cursor", category="Display", needs_doc=False,
        aliases=("relativenumber", "rnu"))(_toggle("relative_numbers", "Relative line numbers"))


@command("nohl", "Stop highlighting the matches of the last search (until the next search)", category="Search",
         aliases=("nohlsearch", "noh", "clear-highlight"), needs_doc=False)
def _nohl(ed, args):
    ed.search_highlight_off = True
    ed.highlight_match = None


@command("save-settings", "Save the current settings as your defaults (in the settings file)", needs_doc=False,
         overview=True, category="Display", aliases=("write-settings", "save-config"))
def _save_settings(ed, args):
    ed.save_settings()


@command("edit-settings", "Open the settings file", needs_doc=False, category="Display",
         aliases=("rc", "open-settings", "edit-config"))
def _edit_settings(ed, args):
    from .config import config_path

    if ed.commit is not None:
        ed.error("Leave the commit editor first")
        return
    path = config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ed.open_file(path)


@command("settings", "Browse and change all options in a settings panel", needs_doc=False, overview=True,
         aliases=("preferences", "prefs", "options", "config"), category="Display")
def _settings(ed, args):
    ed.show_settings()


@command("set", "Change an option: 'set tabsize 2', 'set spaces off', 'set fill 72' ('set' opens the panel)",
         args="OPTION [VALUE]", category="Display", needs_doc=False, aliases=("option",))
def _set(ed, args):
    if not args:
        ed.show_settings()
        return
    name, _, value = args.partition(" ")
    key = resolve_option(name)
    if key is None:
        raise ValueError(f"Unknown option '{name}'")
    value = value.strip() or None
    if value is None and isinstance(getattr(ed.settings, key), bool):
        value = "off" if getattr((ed.doc or ed).settings, key) else "on"  # toggle, consistently for all targets
    msg = ""
    for t in ed.option_targets(key):
        msg = set_option(t, key, value)
    ed.info(msg)


@command("lang", "Set the syntax/language: 'lang python'", args="LANGUAGE", category="Display",
         aliases=("syntax-set", "language", "ft", "filetype"))
def _lang(ed, args):
    if not args:
        ed.language_picker()
        return
    lang = languages.get(args)
    if lang is None:
        ed.error(f"Unknown language '{args}'. Known: {', '.join(sorted(languages.BY_NAME))}")
        return
    ed.doc.set_language(lang)
    ed.info(f"Language: {lang.name}")


@command("jump-to-definition", "Jump to the definition of the name under the cursor (LLVM IR, Python, C/C++)",
         keys=("M-f",), category="Search", aliases=("definition", "goto-definition", "gd"))
def _jump_to_definition(ed, args):
    ed.jump_to_definition()


@command("show-definition", "Show the definition of the name under the cursor in a panel on the right",
         keys=("M-v",), category="Search", aliases=("peek", "peek-definition"))
def _show_definition(ed, args):
    ed.show_definition()


@command("hide-definition", "Close the definition panel (also Esc)", category="Search", needs_doc=False)
def _hide_definition(ed, args):
    ed.definition = None


@command("definition-scroll-down", "Scroll the definition panel down", keys=("M-PageDown",), category="Search",
         hidden=True)
def _definition_down(ed, args):
    ed.scroll_definition(max(1, ed.body_height - 2))


@command("definition-scroll-up", "Scroll the definition panel up", keys=("M-PageUp",), category="Search",
         hidden=True)
def _definition_up(ed, args):
    ed.scroll_definition(-max(1, ed.body_height - 2))


@command("jump-back", "Go back to where you were before jumping to a definition", keys=("M-b",),
         category="Search", aliases=("back",))
def _jump_back(ed, args):
    ed.jump_back()


# ======================================================================
# Commit editing
# ======================================================================


@command("commit", "Edit a commit's changes: 'commit HEAD~2' (no argument: pick from the log)", args="[REV]",
         needs_doc=False, overview=True, aliases=("edit-commit", "troll", "rev"), category="Commit")
def _commit(ed, args):
    if ed.commit is not None and ed.commit.dirty:
        ed.error("Apply (^S) or discard (^X, N) the current commit edits first")
        return
    if args:
        ed.commit = None
        ed.open_commit(args)
    else:
        ed.commit_picker()


@command("apply", "Rewrite the commit with your edits (replays later commits)", needs_doc=False, overview=True,
         aliases=("rewrite", "amend"), category="Commit", commit_only=True)
def _apply(ed, args):
    ed.apply_commit()


@command("overview", "Back to the commit's file list", needs_doc=False, category="Commit", commit_only=True,
         aliases=("file-list",))
def _overview(ed, args):
    ed.commit.close()


@command("next-change", "Jump to the next change of the commit", keys=("M-Down", "F12"), category="Commit")
def _next_change(ed, args):
    ed.next_change(1)


@command("prev-change", "Jump to the previous change of the commit", keys=("M-Up", "F11"), category="Commit")
def _prev_change(ed, args):
    ed.next_change(-1)


@command("only-changes", "Toggle only-changes mode: on hides the unchanged code between changes, off shows "
         "whole files ('only-changes on/off')", keys=("M-z",), category="Commit", args="[on|off]",
         aliases=("fold", "focus", "unfold", "changes"), needs_doc=False, overview=True)
def _only_changes(ed, args):
    word = args.strip().lower()
    if word in TRUE:
        on = True
    elif word in FALSE:
        on = False
    elif word:
        ed.error(f"Expected on or off, not '{args.strip()}'")
        return
    else:
        on = not (ed.doc.settings if ed.doc is not None else ed.settings).only_changes
    for t in ed.option_targets("only_changes"):
        t.only_changes = on
    if ed.doc is not None:
        ed.doc.center_pending = True
    ed.info("Only-changes mode on: unchanged code is folded away" if on
            else "Only-changes mode off: showing the whole file")


@command("side-by-side", "Toggle showing the commit's diff side by side (old version on the right)",
         category="Commit", aliases=("split", "sbs", "unified"), needs_doc=False, overview=True)
def _side_by_side(ed, args):
    on = not ed.settings.side_by_side
    for t in ed.option_targets("side_by_side"):
        t.side_by_side = on
    ed.info("Side-by-side diff" if on else "Unified diff")


@command("revert", "Undo your edits at the cursor (restore the commit's version)", category="Commit",
         aliases=("restore",), commit_only=True)
def _revert(ed, args):
    ed.revert_edit()


@command("revert-hunk", "Drop the commit's change at the cursor (restore the text from before the commit)",
         category="Commit", aliases=("revert-change", "drop-hunk", "drop-change"), commit_only=True)
def _revert_hunk(ed, args):
    ed.revert_hunk()


@command("comment-hunk", "Comment out the commit's change at the cursor (# in Python, // in C/C++, ...)",
         category="Commit", aliases=("comment-change",), commit_only=True)
def _comment_hunk(ed, args):
    ed.comment_hunk()


@command("revert-file", "Discard all your edits to this file", category="Commit", commit_only=True)
def _revert_file(ed, args):
    doc = ed.doc
    if not doc.changed_from_original:
        ed.info("No edits to discard")
        return
    doc.reload_from("\n".join(doc.original_lines))
    ed.info("Discarded your edits to this file")


# ======================================================================
# Help text
# ======================================================================

INTRO = """\
troll is a nano-compatible editor. ^ means Ctrl, M- means Alt (or Esc then the key).
Press ^T (or M-X) to open the command palette: type to filter, Enter to run.
You can also type full commands there, for example:

    goto 120:4        set tabsize 2       lang rust          commit HEAD~3
    s/foo/bar/g       !sort               /needle            justify 72

EDITING A COMMIT
  Start with `troll --commit REV` (or `git troll REV`, or the `commit` command).
  You get a list of the files the commit touched plus its message. Open one with Enter:
  it shows the file as of that commit, with the commit's diff overlaid - added lines
  are green (+), removed lines are shown in red (-) and can't be edited. Unchanged
  code is folded away: that's only-changes mode, M-Z turns it off to see whole files. The version before the commit is shown in a
  right-hand column (`side-by-side` switches to a unified view and back). Just edit
  the text; lines you changed are marked with a yellow *. `comment-hunk` comments out
  the change under the cursor. M-Down/M-Up jump between changes, ^X goes back to the list.
  ^S rewrites the commit (later commits are replayed on top automatically; nothing
  is changed if that would conflict). Undo a rewrite with `git reset --keep ORIG_HEAD`.

COMMIT MESSAGES
  In the commit message (and in COMMIT_EDITMSG from git) lines are 72 columns: ^J and
  typing wrap the body there, a longer subject is flagged and a guide stripe marks the
  limit. The subject, trailers (Signed-off-by: ...) and '#' lines are never rewrapped.
  Code in ```lang fences is highlighted as that language (```python, ```c++, ```ll, ...;
  a fence without a language is C++) and never rewrapped either.
  Change it with `set msgwidth 72`, `set msgguide 73` (0 = off), `set msgwrap off`.

DEFINITIONS (LLVM IR, Python, C/C++)
  M-F jumps to the definition of the name under the cursor (M-B jumps back), M-V shows
  it in a panel on the right (Esc closes it, M-PgUp/M-PgDn scroll). In LLVM IR this
  covers %values, labels, @globals, #attributes and !metadata (with the nodes it refers
  to); for Python and C++ it's a best guess that also looks in imported/included files.

SMART FORMATTING
  Enter keeps indentation, indents after ':' / '{' and continues comments and lists
  (Enter on an empty comment line ends it). Brackets and quotes are paired (but not
  inside comments). ^J reflows the paragraph or comment block under the cursor,
  keeping the comment markers; select lines first to reflow just those.

SETTINGS
  `settings` opens a panel with every option (S there saves them as your defaults).
  Defaults are read from ~/.config/troll/config at startup; `edit-settings` opens it.
  One option per line, nanorc style: `set softwrap`, `unset linenumbers`, `set tabsize 2`.
"""


def help_lines(ed: "Editor") -> list[str]:
    lines = INTRO.split("\n")
    by_cat: dict[str, list[Command]] = {}
    for cmd in COMMANDS.values():
        if cmd.hidden and not cmd.keys:
            continue
        by_cat.setdefault(cmd.category, []).append(cmd)
    for cat in CATEGORIES:
        cmds = by_cat.get(cat, [])
        if not cmds:
            continue
        lines.append(cat.upper())
        for cmd in cmds:
            if cmd.hidden:
                continue
            keys = ", ".join(key_label(k) for k in cmd.keys[:3])
            lines.append(f"  {keys:<22} {cmd.name:<18} {cmd.help}")
        lines.append("")
    lines.append("MOVEMENT")
    lines.append("  arrows or ^F ^B ^P ^N, ^A/^E line start/end, ^Y/^V page, ^Space/M-Space word,")
    lines.append("  M-\\ / M-/ top/bottom. Hold Shift with any of them to select text.")
    return lines
