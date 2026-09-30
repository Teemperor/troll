"""The editor: open documents, modal state and key dispatch.

`Editor.handle_key(name)` is the single entry point for input. Keys are
plain strings ("a", "Enter", "C-k", "M-u", "S-Up", ...) produced by the
terminal layer, so the whole editor can be driven from tests.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass

from . import commands as cmds
from . import gitcommit, languages
from .commit_session import CommitSession
from .diffmodel import diff_opcodes
from .document import Document, ReadOnlyError, decode
from .prompt import Choice, HelpScreen, Picker, PickerItem, Prompt, PromptOption, is_printable
from .search import compile_query, find, replacement_text
from .settings import Settings
from .settings_screen import SettingsScreen

Pos = tuple[int, int]


@dataclass
class Message:
    text: str
    kind: str = "info"  # "info" | "error"


class Editor:
    def __init__(self, settings: Settings | None = None, cwd: str | None = None, raise_errors: bool = False):
        self.settings = settings or Settings()
        self.cwd = cwd or os.getcwd()
        self.raise_errors = raise_errors
        self.docs: list[Document] = []
        self.index = 0
        self.commit: CommitSession | None = None
        self.cutbuffer: str | None = None
        self.last_command: str | None = None
        self.prompt: Prompt | Choice | None = None
        self.overlay: Picker | HelpScreen | SettingsScreen | None = None
        self.message: Message | None = None
        self.highlight_match: tuple[Pos, Pos] | None = None
        self.search_history: list[str] = []
        self.replace_history: list[str] = []
        self.command_history: list[str] = []
        self.last_search: str = ""
        self.search_backwards = False
        self.quit_requested = False
        self.suspend_requested = False
        self.body_height = 20
        self.body_width = 80
        self._pasting = False
        self._paste: list[str] = []
        self.keymap = cmds.build_keymap()

    # ------------------------------------------------------------ documents
    @property
    def doc(self) -> Document | None:
        if self.commit is not None:
            return self.commit.doc
        if not self.docs:
            return None
        return self.docs[self.index]

    @property
    def in_overview(self) -> bool:
        return self.commit is not None and self.commit.current is None

    def add_doc(self, doc: Document) -> Document:
        self.docs.append(doc)
        self.index = len(self.docs) - 1
        return doc

    def open_file(self, path: str, line: int | None = None, col: int | None = None) -> Document:
        full = os.path.join(self.cwd, os.path.expanduser(path))
        for i, d in enumerate(self.docs):
            if d.path and os.path.abspath(d.path) == os.path.abspath(full):
                self.index = i
                return d
        if os.path.isdir(full):
            raise IsADirectoryError(f'"{path}" is a directory')
        doc = Document.from_file(full, self.settings)
        doc.title = path if not os.path.isabs(path) else None
        self.add_doc(doc)
        if line is not None:
            row = line - 1 if line > 0 else len(doc.lines) + line
            doc.goto(max(0, row), max(0, (col or 1) - 1))
        if not os.path.exists(full):
            self.info("New File")
        elif doc.readonly:
            self.info(f"{path} is read-only")
        else:
            n = len(doc.lines) - (1 if doc.lines[-1] == "" else 0)
            self.info(f"Read {n} line{'s' if n != 1 else ''}" + (" (DOS format)" if doc.eol == "\r\n" else ""))
        return doc

    def new_doc(self) -> Document:
        return self.add_doc(Document("", settings=self.settings))

    def switch_doc(self, delta: int) -> None:
        if self.commit is not None:
            if self.commit.step(delta):
                self.info(f"Switched to {self.commit.entry.label}")
            return
        if len(self.docs) < 2:
            self.info("No more open file buffers")
            return
        self.index = (self.index + delta) % len(self.docs)
        self.info(f"Switched to {self.doc.name}")

    def close_doc(self) -> None:
        if self.commit is not None:
            self.commit.close()
            return
        if not self.docs:
            self.quit_requested = True
            return
        del self.docs[self.index]
        if not self.docs:
            self.quit_requested = True
            return
        self.index = min(self.index, len(self.docs) - 1)
        self.info(f"Switched to {self.doc.name}")

    # -------------------------------------------------------------- messages
    def info(self, text: str) -> None:
        self.message = Message(text, "info")

    def error(self, text: str) -> None:
        self.message = Message(text, "error")

    # ------------------------------------------------------------ key input
    def handle_key(self, key: str) -> None:
        if key == "Resize":
            return
        if key == "PasteStart":
            self._pasting = True
            self._paste = []
            return
        if key == "PasteEnd":
            self._pasting = False
            self.paste_text("".join(self._paste))
            return
        if self._pasting:
            self._paste.append({"Enter": "\n", "Tab": "\t", "C-j": "\n"}.get(key, key if len(key) == 1 else ""))
            return
        self.message = None if self.prompt is None else self.message
        self.highlight_match = None if self.prompt is None else self.highlight_match
        try:
            self._dispatch(key)
        except ReadOnlyError as e:
            self.error(str(e))
        except (OSError, ValueError, gitcommit.GitError) as e:
            self.error(str(e))
        except Exception as e:  # never lose the user's work to a bug
            if self.raise_errors:
                raise
            self.error(f"Internal error: {type(e).__name__}: {e}")

    def keys(self, *keys: str) -> None:
        """Feed key names (handy for tests and macros)."""
        for k in keys:
            self.handle_key(k)

    def type(self, text: str) -> None:
        """Type text character by character (newlines press Enter)."""
        for ch in text:
            self.handle_key("Enter" if ch == "\n" else "Tab" if ch == "\t" else ch)

    def _dispatch(self, key: str) -> None:
        if self.prompt is not None:
            p = self.prompt
            try:
                p.handle(key)
            finally:
                if p.done and self.prompt is p:
                    self.prompt = None
            return
        if self.overlay is not None:
            o = self.overlay
            try:
                o.handle(key)
            finally:
                if o.done and self.overlay is o:
                    self.overlay = None
            return
        if self.in_overview:
            self._overview_key(key)
            return
        name = self.lookup(key)
        if name is not None:
            self.run(name)
            self.last_command = name
            return
        self.last_command = None
        doc = self.doc
        if doc is None:
            return
        if is_printable(key) and key != "\t":
            doc.type_char(key)
        elif key.startswith("Paste:"):
            self.paste_text(key[6:])
        else:
            self.info(f"Unbound key: {cmds.key_label(key)}")

    def lookup(self, key: str) -> str | None:
        name = self.keymap.get(key)
        if name is None and key.startswith("M-") and len(key) == 3 and key[2].isalpha():
            name = self.keymap.get("M-" + key[2].swapcase())
        return name

    def run(self, name: str, args: str = "") -> None:
        cmd = cmds.COMMANDS[name]
        if cmd.needs_doc and self.doc is None:
            self.error("No file open")
            return
        if not args and cmd.prompt:
            cmds.invoke_interactive(self, cmd)
            return
        cmd.func(self, args)

    def run_line(self, line: str) -> None:
        cmds.run_command_line(self, line)

    # ---------------------------------------------------------------- paste
    def paste_text(self, text: str) -> None:
        if self.prompt is not None and isinstance(self.prompt, Prompt):
            self.prompt.field.handle("Paste:" + text)
            return
        if self.overlay is not None and isinstance(self.overlay, Picker):
            self.overlay.field.handle("Paste:" + text)
            return
        doc = self.doc
        if doc is None or not text:
            return
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        doc.insert_text(text)
        doc.buffer.seal()

    # ------------------------------------------------------------- prompts
    def ask(self, label: str, on_submit, initial: str = "", **kw) -> Prompt:
        self.prompt = Prompt(label, on_submit, initial, **kw)
        return self.prompt

    def choose(self, question: str, options, on_cancel=None) -> Choice:
        self.prompt = Choice(question, options, on_cancel)
        return self.prompt

    def all_docs(self) -> list[Document]:
        docs = list(self.docs)
        if self.commit is not None:
            docs += [e.doc for e in self.commit.entries if e.doc is not None]
        return docs

    def option_targets(self, key: str) -> list[Settings]:
        """Settings objects a change to option `key` applies to: the editor
        defaults plus the current file, or every open file for options that
        are marked `all_files`, or just the editor for editor-wide ones."""
        from .settings import OPTION_INFO

        info = OPTION_INFO.get(key)
        targets = [self.settings]
        if info is not None and info.editor_wide:
            return targets
        if info is not None and info.all_files:
            return targets + [d.settings for d in self.all_docs()]
        if self.doc is not None:
            targets.append(self.doc.settings)
        return targets

    def show_settings(self) -> None:
        self.overlay = SettingsScreen(self)

    def show_help(self) -> None:
        self.overlay = HelpScreen("diffedit help", cmds.help_lines(self))

    def palette(self, initial: str = "") -> None:
        items = []
        for c in cmds.COMMANDS.values():
            if c.hidden or (c.commit_only and self.commit is None):
                continue
            items.append(PickerItem(c.name, c.help, c.name, cmds.key_label(c.keys[0]) if c.keys else "", c.name + " " + " ".join(c.aliases)))

        def chosen(item, text):
            text = text.strip()
            if text and (" " in text or item is None or cmds.is_special_line(text)):
                self.command_history.append(text)
                self.run_line(text)
            elif item is not None:
                self.command_history.append(item.label)
                cmds.invoke_interactive(self, cmds.COMMANDS[item.value])

        p = Picker(
            "Command",
            items,
            chosen,
            allow_free_text=True,
            filter_key=lambda t: t.split(" ")[0] if t.strip() else "",
            placeholder="type a command, e.g. 'goto 42', 'set tabsize 2', '!sort', 's/old/new/g'",
        )
        p.field.set(initial)
        self.overlay = p

    # ----------------------------------------------------------- save/exit
    def save(self, prompt_name: bool = False) -> None:
        if self.commit is not None:
            self.apply_commit()
            return
        doc = self.doc
        if doc.path and not prompt_name:
            self._write(doc, doc.path)
            return
        self.ask("File Name to Write", lambda name: self._save_as(doc, name), initial=doc.path or "", completer=self.complete_path)

    def _save_as(self, doc: Document, name: str, then=None) -> None:
        name = name.strip()
        if not name:
            self.info("Cancelled")
            return
        path = os.path.join(self.cwd, os.path.expanduser(name))
        if os.path.exists(path) and (not doc.path or os.path.abspath(path) != os.path.abspath(doc.path)):
            def yes():
                if self._write(doc, path) and then:
                    then()

            self.choose("File exists -- OVERWRITE ?", [("yY", "Yes", yes), ("nN", "No", lambda: self.info("Cancelled"))])
            return
        if self._write(doc, path) and then:
            then()

    def _write(self, doc: Document, path: str) -> bool:
        try:
            n = doc.save(path)
        except OSError as e:
            self.error(f"Error writing {path}: {e.strerror or e}")
            return False
        self.info(f"Wrote {n} line{'s' if n != 1 else ''}")
        return True

    def exit(self) -> None:
        if self.commit is not None:
            if self.commit.current is not None:
                self.commit.close()
                return
            self.exit_commit()
            return
        doc = self.doc
        if doc is None:
            self.quit_requested = True
            return
        if not doc.modified:
            self.close_doc()
            return

        def yes():
            if doc.path:
                if self._write(doc, doc.path):
                    self.close_doc()
            else:
                self.ask("File Name to Write", lambda name: self._save_as(doc, name, then=self.close_doc), completer=self.complete_path)

        self.choose(
            "Save modified buffer?",
            [("yY", "Yes", yes), ("nN", "No", self.close_doc)],
            on_cancel=lambda: self.info("Cancelled"),
        )

    # -------------------------------------------------------------- search
    def _search_options(self, after):
        def toggle(attr, label):
            def fn(p):
                setattr(self.settings, attr, not getattr(self.settings, attr))
                p.label = after()
            return fn

        def toggle_back(p):
            self.search_backwards = not self.search_backwards
            p.label = after()

        return [
            PromptOption("M-c", "Case Sens", toggle("case_sensitive", "case")),
            PromptOption("M-C", "Case Sens", toggle("case_sensitive", "case")),
            PromptOption("M-r", "Reg.exp.", toggle("regex_search", "regex")),
            PromptOption("M-R", "Reg.exp.", toggle("regex_search", "regex")),
            PromptOption("M-b", "Backwards", toggle_back),
            PromptOption("M-B", "Backwards", toggle_back),
        ]

    def _search_label(self, base: str) -> str:
        flags = []
        if self.settings.case_sensitive:
            flags.append("Case Sensitive")
        if self.settings.regex_search:
            flags.append("Regexp")
        if self.search_backwards:
            flags.append("Backwards")
        label = base
        if flags:
            label += " [" + ", ".join(flags) + "]"
        if self.last_search:
            label += f" [{self.last_search[:20]}]"
        return label

    def search_prompt(self) -> None:
        opts = self._search_options(lambda: self._search_label("Search"))

        def to_replace(p):
            p.done = True
            self.prompt = None
            self.replace_prompt(p.text)

        opts.append(PromptOption("C-\\", "Replace", to_replace))
        opts.append(PromptOption("M-r", "Reg.exp.", opts[2].action))
        self.ask(self._search_label("Search"), self._do_search, history=self.search_history, options=opts)

    def _pattern(self, query: str):
        try:
            return compile_query(query, self.settings.regex_search, self.settings.case_sensitive)
        except re.error as e:
            self.error(f"Bad regex \"{query}\": {e}")
            return None

    def _do_search(self, query: str) -> None:
        if not query:
            query = self.last_search
        if not query:
            self.info("Cancelled")
            return
        self.last_search = query
        self.find_next(backwards=self.search_backwards)

    def find_next(self, backwards: bool = False) -> bool:
        doc = self.doc
        if not self.last_search:
            self.error("No current search pattern")
            return False
        pattern = self._pattern(self.last_search)
        if pattern is None:
            return False
        res = find(doc.lines, doc.cursor, pattern, forward=not backwards)
        if res is None:
            self.info(f'"{self.last_search}" not found')
            return False
        match, wrapped = res
        doc.goto(*match.start)
        doc.center_pending = False
        self.highlight_match = (match.start, match.end)
        if wrapped:
            self.info("Search Wrapped")
        return True

    def replace_prompt(self, initial: str = "") -> None:
        doc = self.doc
        if doc.readonly:
            raise ReadOnlyError("File is read-only")
        in_sel = doc.selection() is not None
        base = "Search (to replace)" + (" in selection" if in_sel else "")
        opts = self._search_options(lambda: self._search_label(base))

        def got_query(query: str) -> None:
            query = query or self.last_search
            if not query:
                self.info("Cancelled")
                return
            self.last_search = query
            self.ask(f"Replace with", lambda repl: self.start_replace(query, repl), history=self.replace_history)

        self.ask(self._search_label(base), got_query, initial=initial, history=self.search_history, options=opts)

    def start_replace(self, query: str, replacement: str, confirm: bool = True) -> "ReplaceSession | None":
        pattern = self._pattern(query)
        if pattern is None:
            return None
        session = ReplaceSession(self, self.doc, pattern, replacement, self.settings.regex_search)
        if confirm:
            session.next()
        else:
            session.replace_all()
        return session

    # ---------------------------------------------------------------- goto
    def goto_prompt(self) -> None:
        self.ask("Enter line number, column number", self.goto_text)

    def goto_text(self, text: str) -> None:
        pos = cmds.parse_goto(text, self.doc)
        if pos is None:
            if text.strip():
                self.error("Invalid line or column number")
            else:
                self.info("Cancelled")
            return
        self.doc.goto(*pos)

    # ------------------------------------------------------------ cut/paste
    def cut(self) -> None:
        doc = self.doc
        if doc.mark is not None:
            text = doc.cut_selection()
            if text is not None:
                self.cutbuffer = text
            return
        text = doc.cut_line()
        if self.last_command == "cut" and self.cutbuffer is not None:
            self.cutbuffer += text
        else:
            self.cutbuffer = text

    def copy(self) -> None:
        doc = self.doc
        if doc.selection() is not None:
            self.cutbuffer = doc.copy_selection()
            self.info("Copied selection")
            return
        r = doc.cursor[0]
        line = doc.lines[r] + ("\n" if r + 1 < len(doc.lines) else "")
        if self.last_command == "copy" and self.cutbuffer is not None:
            self.cutbuffer += line
        else:
            self.cutbuffer = line
        doc.buffer.seal()
        if r + 1 < len(doc.lines):
            doc.set_cursor((r + 1, 0))
        self.info("Copied line")

    def paste(self) -> None:
        if not self.cutbuffer:
            self.info("Cutbuffer is empty")
            return
        doc = self.doc
        with doc.edit():
            doc.cursor = doc.buffer.insert(doc.cursor, self.cutbuffer)
        doc.buffer.seal()

    # ----------------------------------------------------------- external
    def complete_path(self, text: str) -> list[str]:
        base = os.path.join(self.cwd, os.path.expanduser(text))
        directory, prefix = os.path.split(base)
        try:
            names = os.listdir(directory or ".")
        except OSError:
            return []
        head = text[: len(text) - len(prefix)]
        out = []
        for n in sorted(names):
            if n.startswith(prefix):
                full = os.path.join(directory, n)
                out.append(head + n + ("/" if os.path.isdir(full) else ""))
        return out

    def insert_file(self, name: str) -> None:
        name = name.strip()
        if not name:
            self.info("Cancelled")
            return
        path = os.path.join(self.cwd, os.path.expanduser(name))
        with open(path, "rb") as f:
            text, _ = decode(f.read())
        self.doc.insert_text(text)
        n = text.count("\n")
        self.info(f"Read {n} line{'s' if n != 1 else ''}")

    def insert_file_prompt(self) -> None:
        self.ask("File to insert [from ./]", self.insert_file, completer=self.complete_path)

    def run_shell(self, command: str) -> None:
        """nano's "Execute": insert command output, or pipe the selection through it."""
        doc = self.doc
        sel = doc.selection()
        stdin = doc.buffer.get_text(*sel) if sel else None
        proc = subprocess.run(command, shell=True, cwd=self.cwd, input=stdin, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0 and not proc.stdout:
            self.error(f"'{command}' failed: {proc.stderr.strip()[:200] or proc.returncode}")
            return
        out = proc.stdout
        if sel:
            if stdin.endswith("\n") is False and out.endswith("\n"):
                out = out[:-1]
            doc.transform_selection(lambda _t: out)
            self.info(f"Filtered selection through '{command}'")
        else:
            doc.insert_text(out)
            self.info(f"Inserted output of '{command}'")

    def format_doc(self) -> None:
        doc = self.doc
        for tool in doc.lang.formatters:
            argv = [a.replace("{path}", doc.path or doc.title or "stdin") for a in tool]
            try:
                proc = subprocess.run(argv, input=doc.text(), capture_output=True, text=True, cwd=self.cwd, timeout=60)
            except FileNotFoundError:
                continue
            if proc.returncode != 0:
                self.error(f"{argv[0]}: {proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else 'failed'}")
                return
            if proc.stdout == doc.text():
                self.info(f"Already formatted ({argv[0]})")
            else:
                doc.reload_from(proc.stdout)
                self.info(f"Formatted with {argv[0]}")
            return
        if doc.lang.formatters:
            names = ", ".join(t[0] for t in doc.lang.formatters)
            self.error(f"No formatter installed for {doc.lang.name} (tried {names})")
        else:
            self.error(f"No formatter known for {doc.lang.name}; try 'justify-all' or '!cmd' on a selection")

    def sed_replace(self, old: str, new: str, ignore_case: bool = False) -> None:
        """`s/old/new/[gi]` from the command line: regex replace-all (selection or buffer)."""
        try:
            pattern = re.compile(old, re.IGNORECASE if ignore_case else 0)
        except re.error as e:
            self.error(f"Bad regex \"{old}\": {e}")
            return
        new = re.sub(r"\$(\d)", r"\\\1", new)  # allow $1 as well as \1
        doc = self.doc
        with doc.edit():
            ReplaceSession(self, doc, pattern, new, regex=True).replace_all()

    def quit_all(self, force: bool = False) -> None:
        if force:
            self.quit_requested = True
            return
        if self.commit is not None:
            self.exit_commit()
            if self.commit is not None or self.quit_requested:
                return
        dirty = [d for d in self.docs if d.modified]
        if not dirty:
            self.quit_requested = True
            return
        self.index = self.docs.index(dirty[0])
        self.exit()

    def keep_cursor_in_view(self) -> None:
        """After scrolling the view, pull the cursor back inside it."""
        doc = self.doc
        rows = doc.layout()
        top = doc.scroll_row
        bottom = top + max(1, self.body_height) - 1
        idx = doc.display_index(doc.cursor[0])
        if top <= idx <= bottom:
            return
        target = top if idx < top else bottom
        if rows is None:
            doc.set_cursor((min(target, len(doc.lines) - 1), doc.cursor[1]), keep_goal=True)
        else:
            candidates = [r.row for r in rows[top : bottom + 1] if r.kind == "line"]
            if candidates:
                doc.set_cursor((candidates[0] if idx < top else candidates[-1], doc.cursor[1]), keep_goal=True)

    def buffer_picker(self) -> None:
        if self.commit is not None:
            items = []
            for i, e in enumerate(self.commit.entries):
                detail = e.reason if not e.editable else ("edited" if e.changed else "")
                items.append(PickerItem(e.label, detail, i))

            def chosen(item, _text):
                if item is not None:
                    self.open_entry(item.value)

            self.overlay = Picker("Commit files", items, chosen)
            return
        items = [PickerItem(d.name, "modified" if d.modified else "", i) for i, d in enumerate(self.docs)]

        def pick(item, _text):
            if item is not None:
                self.index = item.value

        self.overlay = Picker("Open buffers", items, pick)

    def language_picker(self) -> None:
        items = [PickerItem(l.name, " ".join(l.extensions[:5]), l.name) for l in languages.LANGUAGES]

        def pick(item, _text):
            if item is not None:
                self.run("lang", item.value)

        self.overlay = Picker("Language", items, pick)

    # --------------------------------------------------------------- commit
    def open_commit(self, rev: str) -> None:
        repo = gitcommit.repo_root(self.cwd)
        session = CommitSession.load(repo, rev, self.settings)
        self.commit = session
        editable = [e for e in session.entries if e.kind == "file" and e.editable]
        if len(editable) == 1:
            session.open(session.entries.index(editable[0]))
            self._jump_to_first_change()
        self.info(f"Editing commit {session.commit.short}: {session.commit.subject[:60]}")

    def commit_picker(self) -> None:
        repo = gitcommit.repo_root(self.cwd)
        items = [PickerItem(desc, "", sha, "", desc) for sha, desc in gitcommit.recent_commits(repo)]
        if not items:
            self.error("No commits found")
            return

        def chosen(item, text):
            if item is not None:
                self.open_commit(item.value)
            elif text.strip():
                self.open_commit(text.strip())

        self.overlay = Picker("Edit which commit?", items, chosen, allow_free_text=True, placeholder="filter commits or type a revision")

    def _jump_to_first_change(self) -> None:
        doc = self.doc
        if doc is None or doc.diff is None:
            return
        d = doc.diff.get(doc.buffer)
        if d.hunks:
            doc.set_cursor((min(d.hunks[0][0], len(doc.lines) - 1), 0))
            doc.center_pending = True

    def open_entry(self, index: int) -> None:
        session = self.commit
        entry = session.entries[index]
        if not session.open(index):
            self.error(f"{entry.label} can't be edited ({entry.reason})")
            return
        doc = entry.doc
        if doc.cursor == (0, 0) and not doc.modified:
            self._jump_to_first_change()

    def _overview_key(self, key: str) -> None:
        s = self.commit
        n = len(s.entries)
        if key in ("Up", "C-p", "k"):
            s.selected = (s.selected - 1) % n
        elif key in ("Down", "C-n", "j"):
            s.selected = (s.selected + 1) % n
        elif key in ("Home", "M-\\"):
            s.selected = 0
        elif key in ("End", "M-/"):
            s.selected = n - 1
        elif key in ("Enter", "Right", "e", " "):
            self.open_entry(s.selected)
        elif key in ("m",):
            self.open_entry(0)
        else:
            name = self.lookup(key)
            if name is not None and cmds.COMMANDS[name].overview:
                self.run(name)
                self.last_command = name
            elif key in ("q",):
                self.exit()
            else:
                self.info("Enter opens the selected file, ^S rewrites the commit, ^X exits")

    def apply_commit(self, then=None) -> None:
        s = self.commit
        if not s.dirty:
            self.info("No changes to apply")
            if then:
                then()
            return
        if s.problem:
            self.error(f"Cannot rewrite: {s.problem}")
            return
        later = len(s.descendants)
        extra = f" and replay {later} later commit{'s' if later != 1 else ''}" if later else ""

        def yes():
            result = s.apply()
            if result.new_sha == result.old_sha:
                self.info("Nothing changed")
            else:
                self.info(
                    f"Rewrote {result.old_sha[:10]} -> {result.new_sha[:10]}"
                    + (f", replayed {result.replayed} commit{'s' if result.replayed != 1 else ''}" if result.replayed else "")
                    + f". Undo with: git reset --keep {result.old_head[:10]}"
                )
            if then:
                then()

        self.choose(f"Rewrite commit {s.commit.short}{extra}?", [("yY", "Yes", yes), ("nN", "No", lambda: self.info("Cancelled"))])

    def exit_commit(self) -> None:
        s = self.commit

        def leave():
            self.commit = None
            if not self.docs:
                self.quit_requested = True

        if not s.dirty:
            leave()
            return

        def yes():
            self.apply_commit(then=leave)

        self.choose(
            "Rewrite the commit with your changes before leaving?",
            [("yY", "Yes", yes), ("nN", "No (discard)", leave)],
            on_cancel=lambda: self.info("Cancelled"),
        )

    def next_change(self, delta: int) -> None:
        doc = self.doc
        if doc.diff is None:
            self.error("Change navigation is available when editing a commit")
            return
        d = doc.diff.get(doc.buffer)
        r = doc.cursor[0]
        starts = [min(s, len(doc.lines) - 1) for s, _e in d.hunks]
        if delta > 0:
            later = [s for s in starts if s > r]
            target = later[0] if later else None
        else:
            earlier = [s for s in starts if s < r]
            target = earlier[-1] if earlier else None
        if target is None:
            if self.commit is not None and len(self.commit.editable_indices()) > 1 and self.commit.step(delta):
                if delta > 0:
                    self._jump_to_first_change()
                else:
                    self._jump_to_last_change()
                self.info(f"Now in {self.commit.entry.label}")
                return
            self.info("No more changes" if starts else "This file has no changes")
            return
        doc.goto(target, 0)
        self.info(f"Change {starts.index(target) + 1}/{len(starts)}")

    def _jump_to_last_change(self) -> None:
        doc = self.doc
        if doc is None or doc.diff is None:
            return
        d = doc.diff.get(doc.buffer)
        if d.hunks:
            doc.goto(min(d.hunks[-1][0], len(doc.lines) - 1), 0)

    def revert_hunk(self) -> None:
        """Restore the commit's version of the edit under the cursor."""
        doc = self.doc
        if doc.diff is None:
            self.error("Revert is available when editing a commit")
            return
        lines, original = doc.lines, doc.diff.original
        r = doc.cursor[0]
        for tag, i1, i2, j1, j2 in diff_opcodes(original, lines):
            if tag == "equal" or not (j1 <= r < j2 or (j1 == j2 == r)):
                continue
            old = original[i1:i2]
            with doc.edit():
                if j2 > j1 and old:
                    doc.buffer.replace_lines(j1, j2 - 1, old)
                elif j2 > j1:  # you inserted these lines
                    if j2 < len(lines):
                        doc.buffer.delete((j1, 0), (j2, 0))
                    else:
                        doc.buffer.delete((j1 - 1, len(lines[j1 - 1])), (j2 - 1, len(lines[j2 - 1])))
                elif j1 < len(lines):  # you deleted these lines
                    doc.buffer.insert((j1, 0), "\n".join(old) + "\n")
                else:
                    doc.buffer.insert(doc.buffer.end(), "\n" + "\n".join(old))
                doc.cursor = doc.buffer.clamp((j1, 0))
            self.info("Restored the commit's version here")
            return
        self.info("You haven't changed anything here")


class ReplaceSession:
    """Interactive search & replace (nano's ^\\)."""

    def __init__(self, ed: Editor, doc: Document, pattern, replacement: str, regex: bool):
        self.ed = ed
        self.doc = doc
        self.pattern = pattern
        self.replacement = replacement
        self.regex = regex
        self.count = 0
        sel = doc.selection()
        self.bounds = sel
        self.pos = sel[0] if sel else doc.cursor
        self.origin = self.pos
        self.wrapped = False
        self.current = None
        doc.clear_mark()

    def _find(self):
        res = find(self.doc.lines, self.pos, self.pattern, wrap=not self.bounds, inclusive=True)
        if res is None:
            return None
        match, wrapped = res
        if self.bounds:
            if match.start < self.bounds[0] or match.end > self.bounds[1]:
                return None
            return match
        if wrapped:
            self.wrapped = True
        if self.wrapped and match.start >= self.origin:
            return None
        return match

    def next(self) -> None:
        match = self._find()
        if match is None:
            self.finish()
            return
        self.current = match
        self.doc.set_cursor(match.start)
        self.doc.center_pending = True
        self.ed.highlight_match = (match.start, match.end)
        self.ed.choose(
            "Replace this instance?",
            [("yY", "Yes", self.yes), ("nN", "No", self.no), ("aA", "All", self.all)],
            on_cancel=self.finish,
        )

    def _replace_current(self) -> None:
        m = self.current
        text = replacement_text(m, self.replacement, self.regex)
        with self.doc.edit():
            end = self.doc.buffer.replace(m.start, m.end, text)
            self.doc.cursor = end
        self.doc.buffer.seal()
        delta = len(text) - (m.end[1] - m.start[1])
        if self.bounds and self.bounds[1][0] == m.end[0]:
            self.bounds = (self.bounds[0], (self.bounds[1][0], self.bounds[1][1] + delta))
        if self.wrapped and self.origin[0] == m.start[0] and m.start < self.origin:
            self.origin = (self.origin[0], self.origin[1] + delta)
        self.count += 1
        self.pos = end

    def yes(self) -> None:
        self._replace_current()
        self.next()

    def no(self) -> None:
        self.pos = self.current.end
        self.next()

    def all(self) -> None:
        self._replace_current()
        self.replace_all()

    def replace_all(self) -> None:
        guard = 0
        while guard < 1_000_000:
            guard += 1
            match = self._find()
            if match is None:
                break
            self.current = match
            self._replace_current()
        self.finish()

    def finish(self) -> None:
        self.ed.highlight_match = None
        self.ed.info(f"Replaced {self.count} occurrence{'s' if self.count != 1 else ''}")

