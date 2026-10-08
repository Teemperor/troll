"""The editor: open documents, modal state and key dispatch.

`Editor.handle_key(name)` is the single entry point for input. Keys are
plain strings ("a", "Enter", "C-k", "M-u", "S-Up", ...) produced by the
terminal layer, so the whole editor can be driven from tests.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass

from . import commands as cmds
from . import config, gitcommit, languages, project
from . import definitions as defs
from . import explain
from .commit_session import CommitSession
from .diffmodel import diff_opcodes
from .document import Document, ReadOnlyError, decode
from .prompt import Choice, HelpScreen, Picker, PickerItem, Prompt, PromptOption, is_printable
from .search import compile_query, find, replacement_text
from .project import Browser
from .textutil import index_at_display_col
from .view import last_visible_index
from .settings import Settings
from .settings_screen import SettingsScreen

Pos = tuple[int, int]


@dataclass
class Message:
    text: str
    kind: str = "info"  # "info" | "error"


@dataclass
class Task:
    """Work running in a background thread (e.g. git rewriting a commit). Input is
    ignored until it's done; the status bar shows `label` with a spinner."""
    label: str
    thread: threading.Thread
    done: Callable[[object], None]  # called with the result, on the main thread
    result: object = None
    error: BaseException | None = None


@dataclass
class Job:
    """A what-is-this request running in a background thread. Unlike a `Task`
    the editor keeps working meanwhile; `poll_jobs` picks up the answer."""
    key: tuple
    tooltip: explain.Tooltip
    thread: threading.Thread
    result: str | None = None
    error: BaseException | None = None


class Editor:
    def __init__(self, settings: Settings | None = None, cwd: str | None = None, raise_errors: bool = False):
        self.settings = settings or Settings()
        self.cwd = cwd or os.getcwd()
        self.raise_errors = raise_errors
        self.docs: list[Document] = []
        self.index = 0
        self.commit: CommitSession | None = None
        self.browser: Browser | None = None  # IDE mode (a directory was opened)
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
        self.search_highlight_off = False  # 'nohl' until the next search
        self.quit_requested = False
        self.suspend_requested = False
        self.body_height = 20
        self.body_width = 80
        self.click_map: dict[int, tuple[int, int, int]] = {}  # screen row -> (buffer row, left col, text x)
        self.definition: defs.DefinitionPanel | None = None  # the show-definition panel on the right
        self.panel_x: int | None = None  # screen column where that panel starts (set by build_frame)
        self.jump_stack: list[tuple[Document, tuple[int, int]]] = []  # for jump-back
        self.task: Task | None = None
        self.tooltip: explain.Tooltip | None = None  # what-is-this answer drawn at its token
        self.explanations: dict[tuple, str] = {}  # (file, row, token, line text) -> answer
        self.jobs: list[Job] = []
        self.llm: explain.Chat | None = None  # tests put a fake client here
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

    @property
    def in_browser(self) -> bool:
        """The directory overview of IDE mode is on screen."""
        return self.browser is not None and self.commit is None and (self.browser.visible or not self.docs)

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
        elif self.settings.remember_position and os.path.exists(full):
            pos = config.recall_position(full)
            if pos is not None:
                doc.goto(*pos)
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
        if self.in_browser and self.docs:
            self.browser.visible = False
            self.info(f"Switched to {self.doc.name}")
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
        self.remember_positions([self.doc])
        del self.docs[self.index]
        if self.browser is not None:
            self.index = max(0, min(self.index, len(self.docs) - 1))
            self.show_browser()
            return
        if not self.docs:
            self.quit_requested = True
            return
        self.index = min(self.index, len(self.docs) - 1)
        self.info(f"Switched to {self.doc.name}")

    def remember_positions(self, docs: list[Document] | None = None) -> None:
        """Log where the cursor is in each file, for `remember_position`."""
        if not self.settings.remember_position:
            return
        docs = self.docs if docs is None else docs
        config.remember_positions({d.path: d.cursor for d in docs if d.path and d.diff is None})

    def shutdown(self) -> None:
        """Called once when the editor exits."""
        self.remember_positions()

    def save_settings(self) -> None:
        path, n = config.save_config(self.settings)
        self.info(f"Saved {n} setting{'s' if n != 1 else ''} to {path}")

    # -------------------------------------------------------------- messages
    def info(self, text: str) -> None:
        self.message = Message(text, "info")

    def error(self, text: str) -> None:
        self.message = Message(text, "error")

    # ------------------------------------------------------------ key input
    def handle_key(self, key: str) -> None:
        if key == "Resize" or self.task is not None:
            return  # nothing happens while a task runs
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
        if key.startswith(("Click:", "WheelUp:", "WheelDown:")):
            self.mouse(key)
            return
        self.message = None if self.prompt is None else self.message
        self.highlight_match = None if self.prompt is None else self.highlight_match
        self._guarded(self._dispatch, key)
        t = self.tooltip
        if t is not None and t.doc.buffer.version != t.version:
            self.tooltip = None  # the text it explains changed

    def _guarded(self, fn, *args) -> None:
        """Run `fn`, turning errors into status messages."""
        try:
            fn(*args)
        except ReadOnlyError as e:
            self.error(str(e))
        except (OSError, ValueError, gitcommit.GitError) as e:
            self.error(str(e))
        except Exception as e:  # never lose the user's work to a bug
            if self.raise_errors:
                raise
            self.error(f"Internal error: {type(e).__name__}: {e}")

    def keys(self, *keys: str) -> None:
        """Feed key names (handy for tests and macros). Waits for tasks and jobs they start."""
        for k in keys:
            self.handle_key(k)
            self.poll_task(wait=True)
            self.poll_jobs(wait=True)

    # ----------------------------------------------------------------- tasks
    def run_task(self, label: str, work: Callable[[], object], done: Callable[[object], None]) -> None:
        """Run `work` in a background thread, then `done(result)` from `poll_task`.
        An exception in `work` is reported like one from a key press."""
        def target():
            try:
                task.result = work()
            except BaseException as e:  # re-raised on the main thread
                task.error = e

        task = Task(label, threading.Thread(target=target, name=label), done)
        self.task = task
        task.thread.start()

    def poll_task(self, wait: bool = False) -> bool:
        """Finish the running task if it's done (or wait for it). True if it finished."""
        task = self.task
        if task is None:
            return False
        task.thread.join(None if wait else 0)
        if task.thread.is_alive():
            return False
        self.task = None

        def finish():
            if task.error is not None:
                raise task.error
            task.done(task.result)

        self._guarded(finish)
        return True

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
        if self.in_browser:
            self._browser_key(key)
            return
        if key == "Esc" and self.tooltip is not None:
            self.tooltip = None
            return
        if key == "Esc" and self.definition is not None:
            self.definition = None
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

    # ---------------------------------------------------------------- mouse
    def mouse(self, key: str) -> None:
        """Mouse events arrive as "Click:Y:X", "WheelUp:Y:X" or "WheelDown:Y:X" (screen cells)."""
        kind, y, x = key.split(":")
        y, x = int(y), int(x)
        doc = self.doc
        if self.prompt is not None or self.overlay is not None or doc is None or self.in_overview or self.in_browser:
            return
        if self.definition is not None and self.panel_x is not None and x >= self.panel_x:
            if kind != "Click":
                self.scroll_definition(3 if kind == "WheelDown" else -3)
            return
        if kind == "Click":
            hit = self.click_map.get(y - 1)  # the title bar is screen row 0
            if hit is None:
                return
            row, left, text_x = hit
            col = index_at_display_col(doc.lines[row], left + max(0, x - text_x), doc.settings.tab_size)
            doc._before_move(False)
            doc.set_cursor((row, col))
            return
        step = 3 if kind == "WheelDown" else -3
        doc.scroll_row = max(0, min(max(0, doc.display_count() - 1), doc.scroll_row + step))
        self.keep_cursor_in_view()

    # ---------------------------------------------------------------- paste
    def paste_text(self, text: str) -> None:
        if self.prompt is not None and isinstance(self.prompt, Prompt):
            self.prompt.field.handle("Paste:" + text)
            return
        if self.overlay is not None and isinstance(self.overlay, Picker):
            self.overlay.field.handle("Paste:" + text)
            return
        if self.in_browser:
            self._browser_key("Paste:" + text.replace("\n", " "))
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

    def option_choices(self, key: str) -> list[str]:
        """Values to pick from for a `listed` option (may ask a server: run it as a task)."""
        if key == "ai_model":
            s = self.settings
            return explain.Client(s.ai_url, "", os.environ.get("OPENAI_API_KEY"), timeout=30).list_models()
        raise ValueError(f"{key} has no list of values")

    def option_dropdown(self, sc: SettingsScreen) -> None:
        key = sc.current.key

        def work():
            try:
                return self.option_choices(key)
            except explain.ExplainError as e:
                return e

        def done(result):
            if isinstance(result, explain.ExplainError):
                sc.choices_failed(str(result))
            elif not result:
                sc.choices_failed("The server lists no models")
            else:
                sc.open_dropdown(result)

        label = f"Fetching models from {self.settings.ai_url}" if key == "ai_model" else f"Listing {key}"
        self.run_task(label, work, done)

    def show_settings(self) -> None:
        self.overlay = SettingsScreen(self)

    def show_help(self) -> None:
        self.overlay = HelpScreen("troll help", cmds.help_lines(self))

    def palette(self, initial: str = "") -> None:
        items = []
        for c in cmds.COMMANDS.values():
            if c.hidden or (c.commit_only and self.commit is None):
                continue
            items.append(PickerItem(c.name, c.help, c.name, cmds.key_label(c.keys[0]) if c.keys else "", c.name + " " + " ".join(c.aliases)))
        # most recently used first (the first one is preselected); the rest keep their order
        rank = {name: i for i, name in enumerate(config.recent_commands())}
        items.sort(key=lambda it: rank.get(it.value, len(rank)))

        def chosen(item, text):
            text = text.strip()
            if text and (" " in text or item is None or cmds.is_special_line(text)):
                self.command_history.append(text)
                cmd = None if cmds.is_special_line(text) else cmds.find_command(text.split(" ")[0])
                if cmd is not None:
                    config.remember_command(cmd.name)
                self.run_line(text)
            elif item is not None:
                self.command_history.append(item.label)
                config.remember_command(item.value)
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
        if self.in_browser:
            self.quit_all()
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
        self.search_highlight_off = False
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

    def search_highlight_pattern(self):
        """Pattern whose matches are all highlighted (`highlight_search`), or None."""
        if not self.settings.highlight_search or not self.last_search or self.search_highlight_off:
            return None
        try:
            return compile_query(self.last_search, self.settings.regex_search, self.settings.case_sensitive)
        except re.error:
            return None

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
        if self.browser is not None:
            self.browser.visible = False
        self.exit()

    def keep_cursor_in_view(self) -> None:
        """After scrolling the view, pull the cursor back inside it."""
        doc = self.doc
        rows = doc.layout()
        top = doc.scroll_row
        bottom = last_visible_index(doc, self.body_height, self.body_width)
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

    # ------------------------------------------------------------- IDE mode
    def open_project(self, path: str) -> None:
        """IDE mode: show the directory overview of `path` (which becomes the working directory)."""
        full = os.path.abspath(os.path.join(self.cwd, os.path.expanduser(path)))
        if not os.path.isdir(full):
            raise NotADirectoryError(f'"{path}" is not a directory')
        self.browser = Browser(full)
        self.cwd = full
        self.browser.visible = not self.docs
        self.info(f"Browsing {full}")

    def show_browser(self) -> None:
        if self.commit is not None:
            self.error("Leave the commit editor first (^X)")
            return
        if self.browser is None:
            self.browser = Browser(self.cwd)
        b = self.browser
        b.visible = True
        if b.hits is None:
            b.refresh()
        doc = self.doc
        if doc is not None and doc.path and b.hits is None:
            # point at the file we come from when it's in this directory
            d, name = os.path.split(os.path.abspath(doc.path))
            if d == b.dir:
                b.filter.set("")
                b.selected = next((i for i, e in enumerate(b.entries) if e.name == name), b.selected)

    def _browser_open(self, path: str, line: int | None = None, col: int | None = None) -> Document:
        rel = os.path.relpath(path, self.cwd)
        doc = self.open_file(path if rel.startswith("..") else rel, line, col)
        if self.browser is not None:
            self.browser.visible = False
        return doc

    def _browser_key(self, key: str) -> None:
        b = self.browser
        page = max(1, self.body_height - 4)
        if key in ("Up", "C-p"):
            b.wrap(-1)
        elif key in ("Down", "C-n"):
            b.wrap(1)
        elif key in ("PageUp", "C-y"):
            b.move(-page)
        elif key in ("PageDown", "C-v"):
            b.move(page)
        elif key in ("Home", "M-\\"):
            b.selected = 0
        elif key in ("End", "M-/"):
            b.selected = max(0, len(b.items()) - 1)
        elif key in ("Enter", "Right"):
            self._browser_choose()
        elif key in ("Left", "Backspace", "C-h") and not b.filter.text:
            if b.hits is not None:
                b.close_hits()
            elif not b.parent():
                self.info("This is the top of the project")
        elif key == "Esc" or key == "C-c":
            if b.filter.text:
                b.filter.set("")
                b.selected = 0
            elif b.hits is not None:
                b.close_hits()
            elif self.docs:
                b.visible = False
            else:
                self.info("^X quits, ^W searches the files, ^F finds a file by name")
        elif key in ("C-w", "F6"):
            self.search_files_prompt()
        elif key == "C-f":
            self.find_file()
        elif key in ("M-=", "M-+", "M--"):
            self.search_context(1 if key != "M--" else -1)
        elif key == "C-r" or key == "F5":
            b.refresh()
            self.info("Reloaded the directory")
        elif key in ("C-x", "F2"):
            self.quit_all()
        elif key.startswith("Paste:"):
            b.filter.handle(key)
            b.selected = 0
        elif is_printable(key) and key != "\t" or key in ("Backspace", "C-h", "M-Backspace", "C-u"):
            before = b.filter.text
            b.filter.handle(key)
            if b.filter.text != before:
                b.selected = 0
                b.scroll = 0
        else:
            name = self.lookup(key)
            if name is not None and cmds.COMMANDS[name].overview and name not in ("save", "write-out", "apply"):
                self.run(name)
                self.last_command = name
            else:
                self.info("Enter opens, type to filter, ^W searches the files, ^F finds a file, ^X quits")

    def _browser_choose(self) -> None:
        b = self.browser
        item = b.current()
        if item is None:
            self.info("Nothing selected")
        elif isinstance(item, project.Hit):
            doc = self._browser_open(os.path.join(b.root, item.path), item.row + 1, item.col + 1)
            self.last_search = b.query
            self.search_highlight_off = False
            self.highlight_match = ((item.row, item.col), (item.row, item.end))
            doc.center_pending = True
        elif item.is_dir:
            b.enter(item.path)
        else:
            self._browser_open(item.path)

    def search_context(self, delta: int) -> None:
        """More/fewer lines around each match in the file search results."""
        s = self.settings
        s.search_context = max(0, min(20, s.search_context + delta))
        self.info(f"{s.search_context} line{'s' if s.search_context != 1 else ''} of context around matches")

    def search_files_prompt(self) -> None:
        if self.commit is not None:
            self.error("Leave the commit editor first (^X)")
            return
        if self.browser is None:
            self.browser = Browser(self.cwd)
        b = self.browser
        where = b.rel_dir if b.visible and b.rel_dir else "project"
        opts = self._search_options(lambda: self._search_label(f"Search in {where}"))
        self.ask(self._search_label(f"Search in {where}"), self.search_files, initial="",
                 history=self.search_history, options=opts)

    def search_files(self, query: str) -> None:
        """Search every file of the shown directory (or the whole project) for `query`."""
        if self.commit is not None:
            self.error("Leave the commit editor first (^X)")
            return
        query = query or self.last_search
        if not query:
            self.info("Cancelled")
            return
        pattern = self._pattern(query)
        if pattern is None:
            return
        if self.browser is None:
            self.browser = Browser(self.cwd)
        b = self.browser
        base = b.dir if b.visible else b.root
        self.last_search = query

        def work():
            return project.search_files(base, project.list_files(base), pattern)

        def done(hits):
            prefix = os.path.relpath(base, b.root)
            for h in hits:
                h.path = os.path.normpath(os.path.join(prefix, h.path))
            b.show_hits(query, hits)
            b.visible = True
            files = len({h.path for h in hits})
            more = "+" if len(hits) >= project.MAX_HITS else ""
            if hits:
                self.info(f"{len(hits)}{more} matching line{'s' if len(hits) != 1 else ''} in {files} file{'s' if files != 1 else ''}")
            else:
                self.info(f'"{query}" not found')

        self.run_task(f'Searching for "{query}"', work, done)

    def find_file(self) -> None:
        """Pick any file below the project root by (fuzzy) name."""
        if self.commit is not None:
            self.error("Leave the commit editor first (^X)")
            return
        root = self.browser.root if self.browser is not None else self.cwd
        files = project.list_files(root)
        if not files:
            self.info("No files found")
            return
        items = [PickerItem(f, "", f, "", os.path.basename(f)) for f in files]

        def chosen(item, _text):
            if item is not None:
                self._browser_open(os.path.join(root, item.value))

        self.overlay = Picker("Find file", items, chosen, placeholder="type part of a file name")

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
            files, message = s.collect()
            self.run_task(
                f"Rewriting commit {s.commit.short}",
                lambda: gitcommit.rewrite_commit(s.repo, s.commit.sha, files, message),
                done,
            )

        def done(result):
            s.finish(result)
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
        if doc.diff is None and self.commit is None:
            self.error("Change navigation is available when editing a commit")
            return
        starts = []  # the message has no changes of its own: go straight to the next file
        if doc.diff is not None:
            d = doc.diff.get(doc.buffer)
            starts = [min(s, len(doc.lines) - 1) for s, _e in d.hunks]
        r = doc.cursor[0]
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
            self.info("No more changes" if starts or doc.diff is None else "This file has no changes")
            return
        doc.goto(target, 0)
        self.info(f"Change {starts.index(target) + 1}/{len(starts)}")

    def move_line(self, n: int) -> None:
        """Up/Down; past the first/last line of a commit file, go on to the previous/next file's change."""
        doc = self.doc
        if self.commit is not None and len(self.commit.editable_indices()) > 1:
            rows = doc.visible_line_rows() or [0, len(doc.lines) - 1]
            if doc.cursor[0] == (rows[0] if n < 0 else rows[-1]):
                self.next_change(n)
                return
        doc.move_vertical(n)

    def _jump_to_last_change(self) -> None:
        doc = self.doc
        if doc is None or doc.diff is None:
            return
        d = doc.diff.get(doc.buffer)
        if d.hunks:
            doc.goto(min(d.hunks[-1][0], len(doc.lines) - 1), 0)

    def revert_edit(self) -> None:
        """Restore the commit's version of the edit under the cursor."""
        doc = self.doc
        if doc.diff is None:
            self.error("Revert is available when editing a commit")
            return
        if _restore_block(doc, doc.diff.original):
            self.info("Restored the commit's version here")
        else:
            self.info("You haven't changed anything here")

    def comment_hunk(self) -> None:
        """Comment out the lines of the commit's change under the cursor."""
        doc = self.doc
        if doc.diff is None:
            self.error("Commenting out a change is available when editing a commit")
            return
        r = doc.cursor[0]
        d = doc.diff.get(doc.buffer)
        hunk = next(((s, e) for s, e in d.hunks if s <= r < e), None)
        if hunk is None:
            if d.hunk_at(r) is not None:
                self.info("This change only removes lines: nothing to comment out")
            else:
                self.info("The commit doesn't change anything here")
            return
        s, e = hunk
        marker = doc.lang.line_comment or "//"
        if doc.comment_out_rows(s, e - 1, marker):
            self.info(f"Commented out {e - s} line{'s' if e - s != 1 else ''}")
        else:
            self.info("This change is already commented out")

    # --------------------------------------------------------- definitions
    def _label(self, path: str | None, fallback: str = "New Buffer") -> str:
        if not path:
            return fallback
        rel = os.path.relpath(path, self.cwd)
        return path if rel.startswith("..") else rel

    def _doc_source(self, doc: Document) -> defs.Source:
        return defs.Source(doc.title or self._label(doc.path, doc.name), doc.lines, doc.lang, doc=doc, path=doc.path)

    def _file_source(self, path: str) -> defs.Source | None:
        path = os.path.abspath(path)
        for d in self.all_docs():
            if d.path and os.path.abspath(d.path) == path:
                return self._doc_source(d)
        try:
            if os.path.getsize(path) > 4 << 20:
                return None
            with open(path, "rb") as f:
                text, _eol = decode(f.read())
        except OSError:
            return None
        lines = text.split("\n")
        return defs.Source(self._label(path), lines, languages.detect(path, lines[0]), path=path)

    def locate_definitions(self, everywhere: bool = False):
        """(symbol, [(source, definition), ...] best first) for the cursor, or None.

        Other files are searched when this one has nothing better than a
        declaration or an import (or always, with `everywhere`)."""
        doc = self.doc
        if doc.lang.name not in defs.SUPPORTED:
            self.error(f"Definitions work in LLVM IR, Python and C/C++ files (this is {doc.lang.name})")
            return None
        r, c = doc.cursor
        sym = defs.symbol_at(doc.lines, doc.lang, r, c)
        if sym is None:
            self.error("No symbol under the cursor")
            return None
        here = self._doc_source(doc)
        found = [(here, d) for d in here.find(sym, r)]
        if doc.lang.name == "python" and sym.access == "." and sym.qualifier not in (None, "self", "cls"):
            # module.attr: look in the module the qualifier was imported from
            q = defs.Symbol(sym.qualifier, r, 0, 0)
            imp = next((d for d in here.find(q, r)[:1] if d.kind == "import"), None)
            if imp is not None:
                found = self._py_import_definitions(imp, sym.name, here.path) + found
        if everywhere or not found or found[0][1].tier >= defs.WEAK:
            ext = self._external_definitions(sym, here, found)
            found = [sd for sd in ext if sd[1].tier < defs.WEAK] + found + [sd for sd in ext if sd[1].tier >= defs.WEAK]
        return sym, found

    def _py_import_definitions(self, imp: defs.Definition, attr: str | None, from_path: str | None, depth: int = 0):
        """Definitions for a name bound by the import `imp` (or `attr` inside the imported module)."""
        module = imp.module
        name = imp.target  # from M import name
        if attr is not None:
            if name is not None:
                module = module + name if module.endswith(".") else f"{module}.{name}"
            name = attr
        if depth > 3 or module is None:
            return []
        path = defs.python_module_path(module, from_path, self.cwd)
        src = self._file_source(path) if path else None
        if name is None:  # `import M`: the module itself
            return [] if src is None else [(src, defs.Definition(0, 0, "module", 0, min(len(src.lines) - 1, 40)))]
        out = []
        if src is not None:
            for d in src.find(defs.Symbol(name, -1, 0, 0), None):
                if d.kind == "import" and not out:
                    out += self._py_import_definitions(d, None, src.path, depth + 1)  # a re-export
                elif d.tier < defs.WEAK:
                    out.append((src, d))
        if not out:  # from package import submodule
            sub = module + name if module.endswith(".") else f"{module}.{name}"
            path = defs.python_module_path(sub, from_path, self.cwd)
            src = self._file_source(path) if path else None
            if src is not None:
                out.append((src, defs.Definition(0, 0, "module", 0, min(len(src.lines) - 1, 40))))
        return out

    def _external_definitions(self, sym: defs.Symbol, here: defs.Source, local):
        family = ("c", "cpp") if here.lang.name in ("c", "cpp") else (here.lang.name,)
        out = []
        if here.lang.name == "python":
            for _src, d in local:
                if d.kind == "import":
                    out += self._py_import_definitions(d, None, here.path)
                    break
        sources = [self._doc_source(d) for d in self.all_docs() if d is not here.doc and d.lang.name in family]
        if here.lang.name in ("c", "cpp"):
            open_paths = {os.path.abspath(s.path) for s in sources if s.path}
            for path in defs.cpp_related_files(here.lines, here.path, self.cwd):
                if path not in open_paths:
                    src = self._file_source(path)
                    if src is not None:
                        sources.append(src)
        bare = defs.Symbol(sym.name, -1, 0, 0, sym.qualifier, sym.access)
        for src in sources:
            out += [(src, d) for d in src.find(bare, None)]
        out.sort(key=lambda sd: sd[1].tier)
        return out

    def _switch_to(self, doc: Document) -> bool:
        if doc is self.doc:
            return True
        if self.commit is not None:
            for i, e in enumerate(self.commit.entries):
                if e.doc is doc:
                    return self.commit.open(i)
            return False
        for i, d in enumerate(self.docs):
            if d is doc:
                self.index = i
                return True
        return False

    def jump_to_definition(self) -> None:
        res = self.locate_definitions()
        if res is None:
            return
        sym, found = res
        doc = self.doc
        if not found:
            self.info(f"No definition of {sym.name} found")
            return
        def elsewhere(found):
            return next(((s, d) for s, d in found if not (s.doc is doc and (d.row, d.col) == (sym.row, sym.col))), None)

        pick = elsewhere(found)
        if pick is None:  # on the definition: try the declaration in another file (header <-> source)
            pick = elsewhere(self.locate_definitions(everywhere=True)[1])
        if pick is None:
            self.info(f"This is the definition of {sym.name}")
            return
        src, d = pick
        if src.doc is not None:
            target = src.doc if self._switch_to(src.doc) else None
        else:
            target = self.open_file(src.path) if self.commit is None else None
            if target is not None:
                target.title = src.label if not os.path.isabs(src.label) else None
        if target is None:
            self._show(sym, src, d)
            self.info(f"{sym.name} is defined in {src.label} (not part of this commit): shown on the right")
            return
        self.jump_stack.append((doc, doc.cursor))
        del self.jump_stack[:-100]
        target.goto(d.row, d.col)
        where = "" if target is doc else f"{src.label}:"
        self.info(f"{sym.name}: {d.kind} at {where}{d.row + 1} (M-B jumps back)")

    def _show(self, sym: defs.Symbol, src: defs.Source, d: defs.Definition) -> None:
        title = f"{sym.name} · {d.kind} · {src.label}:{d.row + 1}"
        self.definition = defs.build_panel(title, src.lines, src.spans, d)

    def show_definition(self) -> None:
        res = self.locate_definitions()
        if res is None:
            return
        sym, found = res
        if not found:
            self.info(f"No definition of {sym.name} found")
            return
        src, d = found[0]
        self._show(sym, src, d)
        self.info("Esc closes the definition panel, M-PgUp/M-PgDn scroll it")

    def scroll_definition(self, delta: int) -> None:
        if self.tooltip is not None:  # the tooltip is on top, so it scrolls first
            self.tooltip.scroll = max(0, self.tooltip.scroll + delta)  # view.py clamps the bottom
            return
        panel = self.definition
        if panel is None:
            self.info("No definition is shown")
            return
        panel.scroll = max(0, min(len(panel.rows) - 1, panel.scroll + delta))

    def jump_back(self) -> None:
        live = self.all_docs()
        while self.jump_stack:
            doc, pos = self.jump_stack.pop()
            if any(d is doc for d in live) and self._switch_to(doc):
                doc.goto(*doc.buffer.clamp(pos))
                self.info("Jumped back")
                return
        self.info("No earlier position to jump back to")

    # ---------------------------------------------------------- what-is-this
    def _project_root(self, doc: Document) -> str:
        if self.commit is not None:
            return self.commit.repo
        if self.browser is not None:
            return self.browser.root
        start = os.path.dirname(doc.path) if doc.path else self.cwd
        try:
            return gitcommit.repo_root(start)
        except (gitcommit.GitError, OSError):
            return start

    def what_is_this(self) -> None:
        """Ask the model what the token under the cursor is; the answer shows in a tooltip."""
        doc = self.doc
        tok = explain.token_at(doc.lines, doc.lang, *doc.cursor, doc.selection())
        if tok is None:
            self.error("No token under the cursor")
            return
        here = self._doc_source(doc)
        key = (doc.path or here.label, tok.row, tok.text, doc.lines[tok.row])
        tip = explain.Tooltip(doc, tok, doc.buffer.version)
        self.tooltip = tip
        if key in self.explanations:
            tip.text = self.explanations[key]
            self.info("Cached answer (Esc closes it)")
            return
        s = self.settings
        q = explain.Question(tok, here.label, doc.lang.name, list(doc.lines), s.ai_context)
        # the worker only sees copies: the buffers may change while it runs
        sources = [defs.Source(here.label, q.lines, doc.lang, path=doc.path)]
        sources += [defs.Source(self._label(d.path, d.name), list(d.lines), d.lang, path=d.path)
                    for d in self.all_docs() if d is not doc]
        tools = explain.ProjectTools(self._project_root(doc), sources)
        client = self.llm or explain.Client(s.ai_url, s.ai_model, os.environ.get("OPENAI_API_KEY"))
        tip.model = s.ai_model or "the model"

        def target():
            try:
                job.result = explain.explain(q, client, tools)
            except BaseException as e:  # reported on the main thread
                job.error = e

        job = Job(key, tip, threading.Thread(target=target, name="what-is-this", daemon=True))
        self.jobs.append(job)
        job.thread.start()
        self.info(f"Asking what '{tok.text}' is (Esc closes the tooltip)")

    @property
    def busy(self) -> bool:
        """Background jobs are running (the screen should keep refreshing)."""
        return bool(self.jobs)

    def poll_jobs(self, wait: bool = False) -> bool:
        """Pick up finished what-is-this answers. True if one arrived."""
        arrived = False
        for job in list(self.jobs):
            job.thread.join(None if wait else 0)
            if job.thread.is_alive():
                continue
            self.jobs.remove(job)
            arrived = True
            tip = job.tooltip
            if job.error is None:
                self.explanations[job.key] = job.result
                tip.text = job.result
            elif isinstance(job.error, (explain.ExplainError, OSError)):
                tip.error = str(job.error)
            elif self.raise_errors:
                raise job.error
            else:
                tip.error = f"Internal error: {type(job.error).__name__}: {job.error}"
        return arrived

    def revert_hunk(self) -> None:
        """Drop the commit's change under the cursor (restore the pre-commit text)."""
        doc = self.doc
        if doc.diff is None:
            self.error("Reverting a change is available when editing a commit")
            return
        if _restore_block(doc, doc.diff.base):
            self.info("Reverted the commit's change here")
        else:
            self.info("The commit doesn't change anything here")


def _restore_block(doc: Document, ref: list[str]) -> bool:
    """Replace the changed block under the cursor with its text in `ref`.

    A block of deleted lines counts as being under the cursor when the cursor
    is on the line they were deleted before (or the last line, for lines
    deleted at the end). Returns False if the cursor isn't in a changed block.
    """
    lines = doc.lines
    r = doc.cursor[0]
    for tag, i1, i2, j1, j2 in diff_opcodes(ref, lines):
        if tag == "equal":
            continue
        if not (j1 <= r < j2 or (j1 == j2 and min(j1, len(lines) - 1) == r)):
            continue
        old = ref[i1:i2]
        with doc.edit():
            if j2 > j1 and old:
                doc.buffer.replace_lines(j1, j2 - 1, old)
            elif j2 > j1:  # inserted lines
                if j2 < len(lines):
                    doc.buffer.delete((j1, 0), (j2, 0))
                else:
                    doc.buffer.delete((j1 - 1, len(lines[j1 - 1])), (j2 - 1, len(lines[j2 - 1])))
            elif j1 < len(lines):  # deleted lines
                doc.buffer.insert((j1, 0), "\n".join(old) + "\n")
            else:
                doc.buffer.insert(doc.buffer.end(), "\n" + "\n".join(old))
            doc.cursor = doc.buffer.clamp((j1, 0))
        return True
    return False


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

