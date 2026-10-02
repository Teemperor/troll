"""Command line entry points: `troll` and `git-troll` (so `git troll REV` works)."""

from __future__ import annotations

import argparse
import os
import re
import sys

from . import __version__, config
from .editor import Editor
from .gitcommit import GitError
from .settings import Settings


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="troll",
        description="A nano-style editor with syntax highlighting, smart formatting, "
        "a command palette (^T) and an interactive editor for git commits.",
        epilog="Examples:\n"
        "  troll file.py                 edit a file\n"
        "  troll +42 file.py             open at line 42 (+42,7 for a column)\n"
        "  troll --commit HEAD~2         clean up the changes made by HEAD~2\n"
        "  troll --commit                pick a commit from the log\n"
        "  git troll HEAD~2              same, via the git-troll helper\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("files", nargs="*", metavar="[+LINE[,COL]] FILE")
    p.add_argument("-c", "--commit", nargs="?", const="", metavar="REV",
                   help="edit the diff of a git commit (no REV: choose from the log)")
    p.add_argument("-C", "--directory", default=None, metavar="DIR", help="run as if started in DIR")
    p.add_argument("-T", "--tabsize", type=int, metavar="N", help="tab width / indent size")
    p.add_argument("-E", "--tabstospaces", action="store_true", help="indent with spaces")
    p.add_argument("--tabs", action="store_true", help="indent with tabs")
    p.add_argument("-l", "--linenumbers", action="store_true", default=None, help="show line numbers (default)")
    p.add_argument("-L", "--nolinenumbers", action="store_true", help="hide line numbers")
    p.add_argument("-v", "--view", action="store_true", help="read-only mode")
    p.add_argument("-i", "--noautoindent", action="store_true", help="disable automatic indentation")
    p.add_argument("-P", "--nopairs", action="store_true", help="disable automatic bracket/quote pairing")
    p.add_argument("-r", "--fill", type=int, metavar="N", help="justify width (default 80)")
    p.add_argument("-Y", "--syntax", metavar="LANG", help="force a syntax/language")
    p.add_argument("-x", "--nohelp", action="store_true", help="hide the two help lines")
    p.add_argument("-S", "--softwrap", action="store_true", help="wrap long lines on screen")
    p.add_argument("-b", "--breaklonglines", action="store_true", help="hard-wrap lines while typing")
    p.add_argument("-J", "--guidestripe", type=int, metavar="N", help="draw a guide stripe at column N")
    p.add_argument("-m", "--mouse", action="store_true", help="enable mouse support")
    p.add_argument("-B", "--backup", action="store_true", help="keep the previous version as FILE~ when saving")
    p.add_argument("-I", "--ignorercfiles", action="store_true", help="don't read the settings file")
    p.add_argument("-V", "--version", action="version", version=f"troll {__version__}")
    return p


def split_positions(items: list[str], cwd: str | None = None) -> list[tuple[str, int | None, int | None]]:
    """Pair nano-style "+LINE[,COL]" arguments with the file that follows them.

    "file.py:42", "file.py:42:7" (compiler output) and "file.py:" (a trailing
    colon is dropped) also work, unless a file with that literal name exists.
    """
    out = []
    line = col = None
    for item in items:
        m = re.fullmatch(r"\+(-?\d*)(?:[,:](-?\d+))?", item)
        if m:
            line = int(m.group(1)) if m.group(1) not in ("", "-") else None
            col = int(m.group(2)) if m.group(2) else None
            continue
        m = re.fullmatch(r"(.+?)(?::(\d+)(?::(\d+))?)?:?", item)
        if m and m.group(1) != item and not os.path.exists(os.path.join(cwd or "", os.path.expanduser(item))):
            item = m.group(1)
            if line is None and m.group(2):
                line, col = int(m.group(2)), int(m.group(3)) if m.group(3) else None
        out.append((item, line, col))
        line = col = None
    return out


def make_settings(args, errors: list[str] | None = None) -> Settings:
    """Defaults, then the settings file, then command line options."""
    s = Settings()
    if not getattr(args, "ignorercfiles", False):
        problems = config.load_config(s)
        if errors is not None:
            errors.extend(problems)
    if args.tabsize:
        s.tab_size = args.tabsize
    if args.tabstospaces:
        s.expand_tabs = True
    if args.tabs:
        s.expand_tabs = False
    if args.nolinenumbers:
        s.line_numbers = False
    if args.noautoindent:
        s.auto_indent = False
    if args.nopairs:
        s.auto_pair = False
    if args.fill:
        s.fill_width = args.fill
    if args.nohelp:
        s.help_lines = False
    if args.softwrap:
        s.soft_wrap = True
    if args.breaklonglines:
        s.hard_wrap = True
    if args.guidestripe is not None:
        s.guide_column = max(0, args.guidestripe)
    if args.mouse:
        s.mouse = True
    if args.backup:
        s.backup = True
    return s


def setup_editor(args) -> Editor:
    from . import languages

    problems: list[str] = []
    ed = Editor(make_settings(args, problems), cwd=args.directory)
    if args.commit is not None:
        if args.commit:
            ed.open_commit(args.commit)
        else:
            ed.commit_picker()
            if ed.overlay is None:  # no commits
                raise GitError(ed.message.text if ed.message else "no commits")
    for path, line, col in split_positions(args.files, args.directory):
        doc = ed.open_file(path, line, col)
        if args.view:
            doc.readonly = True
        if args.syntax:
            lang = languages.get(args.syntax)
            if lang:
                doc.set_language(lang)
        # explicit command line options beat detection
        if args.tabsize:
            doc.settings.tab_size = args.tabsize
        if args.tabstospaces or args.tabs:
            doc.settings.expand_tabs = bool(args.tabstospaces)
    if not ed.docs and ed.commit is None and ed.overlay is None:
        ed.new_doc()
    if len(ed.docs) > 1:
        ed.index = 0
    if problems:
        ed.error(problems[0] + (f" (and {len(problems) - 1} more)" if len(problems) > 1 else ""))
    return ed


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        ed = setup_editor(args)
    except (GitError, OSError) as e:
        print(f"troll: {e}", file=sys.stderr)
        return 1
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("troll: needs a terminal", file=sys.stderr)
        return 1
    from .tui.app import run

    try:
        run(ed)
    finally:
        ed.shutdown()
    return 0


def git_main(argv: list[str] | None = None) -> int:
    """`git troll [REV] [options]` - edit the diff of a commit."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and not argv[0].startswith("-"):
        rev = argv.pop(0)
    else:
        rev = ""
    return main(["--commit", rev, *argv] if rev else ["--commit", *argv])


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
