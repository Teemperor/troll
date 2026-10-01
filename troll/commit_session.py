"""The "edit a commit" session: one document per changed file plus the message."""

from __future__ import annotations

from dataclasses import dataclass

from . import gitcommit, languages
from .diffmodel import DiffState, compute
from .document import Document, decode
from .settings import Settings


@dataclass
class Entry:
    kind: str  # "message" or "file"
    label: str
    status: str  # git status letter, "" for the message
    doc: Document | None
    reason: str = ""  # why a file can't be edited
    old_path: str | None = None

    @property
    def editable(self) -> bool:
        return self.doc is not None

    @property
    def changed(self) -> bool:
        return self.doc is not None and self.doc.changed_from_original


STATUS_NAMES = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed", "C": "copied", "T": "type changed"}


def _file_doc(cf: gitcommit.CommitFile, settings: Settings) -> Document:
    path = cf.change.path
    doc = Document.from_bytes(cf.content, path=None, settings=settings, lang=languages.detect(path, ""), title=path)
    if doc.lang is languages.TEXT:
        doc.set_language(languages.detect(path, doc.lines[0]))
    base = [] if cf.change.status == "A" else decode(cf.base)[0].split("\n")
    doc.diff = DiffState(base, list(doc.lines))
    return doc


class CommitSession:
    def __init__(self, repo: str, commit: gitcommit.CommitInfo, files: list[gitcommit.CommitFile], settings: Settings):
        self.repo = repo
        self.commit = commit
        self.settings = settings
        self.entries: list[Entry] = []
        msg_doc = Document(commit.message, settings=settings, lang=languages.BY_NAME["gitcommit"], title="Commit message")
        self.entries.append(Entry("message", "Commit message", "", msg_doc))
        for cf in files:
            if cf.editable:
                doc = _file_doc(cf, settings)
                self.entries.append(Entry("file", cf.change.path, cf.change.status, doc, old_path=cf.change.old_path))
            else:
                self.entries.append(Entry("file", cf.change.path, cf.change.status, None, cf.reason, cf.change.old_path))
        self.selected = 1 if len(self.entries) > 1 else 0
        self.current: int | None = None  # entry being edited, None = overview
        self.descendants: list[str] = []
        self.problem: str | None = None
        self.refresh_rewritable()

    @classmethod
    def load(cls, repo: str, rev: str, settings: Settings) -> "CommitSession":
        commit, files = gitcommit.load_commit(repo, rev)
        return cls(repo, commit, files, settings)

    def refresh_rewritable(self) -> None:
        try:
            self.descendants = gitcommit.check_rewritable(self.repo, self.commit.sha)
            self.problem = None
        except gitcommit.GitError as e:
            self.descendants = []
            self.problem = str(e)

    # ----------------------------------------------------------- navigation
    @property
    def entry(self) -> Entry | None:
        return None if self.current is None else self.entries[self.current]

    @property
    def doc(self) -> Document | None:
        e = self.entry
        return e.doc if e else None

    def editable_indices(self) -> list[int]:
        return [i for i, e in enumerate(self.entries) if e.editable]

    def open(self, index: int) -> bool:
        if not self.entries[index].editable:
            return False
        self.current = index
        self.selected = index
        return True

    def step(self, delta: int) -> bool:
        """Open the next/previous editable entry."""
        idx = self.editable_indices()
        if not idx:
            return False
        if self.current is None and self.selected in idx:
            return self.open(self.selected)  # from the overview: open the highlighted file
        cur = self.current if self.current is not None else self.selected
        if cur in idx:
            pos = (idx.index(cur) + delta) % len(idx)
        else:
            pos = 0 if delta > 0 else len(idx) - 1
        return self.open(idx[pos])

    def close(self) -> None:
        if self.current is not None:
            self.selected = self.current
        self.current = None

    # --------------------------------------------------------------- state
    @property
    def dirty(self) -> bool:
        return any(e.changed for e in self.entries)

    def stats(self, entry: Entry) -> tuple[int, int]:
        if entry.doc is None or entry.doc.diff is None:
            return (0, 0)
        d = entry.doc.diff.get(entry.doc.buffer)
        return d.added_count, d.removed_count

    def original_stats(self, entry: Entry) -> tuple[int, int]:
        if entry.doc is None or entry.doc.diff is None:
            return (0, 0)
        d = compute(entry.doc.diff.base, None, entry.doc.diff.original)
        return d.added_count, d.removed_count

    def collect(self) -> tuple[dict[str, bytes], str | None]:
        files: dict[str, bytes] = {}
        message = None
        for e in self.entries:
            if not e.changed:
                continue
            e.doc.prepare_for_save()
            if not e.changed:
                continue
            if e.kind == "message":
                message = e.doc.text()
                if not message.endswith("\n"):
                    message += "\n"
            else:
                files[e.label] = e.doc.to_bytes()
        return files, message

    def apply(self) -> gitcommit.RewriteResult:
        files, message = self.collect()
        return self.finish(gitcommit.rewrite_commit(self.repo, self.commit.sha, files, message))

    def finish(self, result: gitcommit.RewriteResult) -> gitcommit.RewriteResult:
        """Adopt a successful rewrite: the edited texts become the new originals."""
        self.commit = gitcommit.read_commit(self.repo, result.new_sha)
        for e in self.entries:
            if e.doc is not None:
                e.doc.original_lines = list(e.doc.lines)
                e.doc.buffer.mark_saved()
                if e.doc.diff is not None:
                    e.doc.diff.reset_original(e.doc.lines)
        self.refresh_rewritable()
        return result
