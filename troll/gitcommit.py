"""Git plumbing for editing the contents of an existing commit.

Rewriting works entirely on objects (no checkout, no rebase state):

1. build a new tree = the commit's tree with the edited blobs swapped in,
2. create a replacement commit with the original author/message,
3. replay every descendant up to HEAD: each later commit keeps its tree,
   except that the edited files get a line-based three-way merge
   (merge.py) of your edit and that commit's own change,
4. update the work tree with a safe two-tree `read-tree -m -u`, then move
   HEAD's branch with `update-ref`.

If any step fails (e.g. a later commit conflicts with the edit) nothing in
the repository is changed apart from unreferenced objects.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass, field

from .merge import merge3_bytes


class GitError(Exception):
    pass


def run_git(
    repo: str,
    *args: str,
    input: bytes | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess:
    full_env = None
    if env:
        full_env = dict(os.environ)
        full_env.update(env)
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=repo,
            input=input,
            capture_output=True,
            env=full_env,
        )
    except FileNotFoundError:
        raise GitError("git is not installed") from None
    if check and proc.returncode != 0:
        msg = proc.stderr.decode(errors="replace").strip() or proc.stdout.decode(errors="replace").strip()
        raise GitError(f"git {args[0]} failed: {msg}")
    return proc


def git_out(repo: str, *args: str, **kw) -> str:
    return run_git(repo, *args, **kw).stdout.decode(errors="surrogateescape").strip()


def repo_root(path: str = ".") -> str:
    try:
        return git_out(path, "rev-parse", "--show-toplevel")
    except GitError:
        raise GitError(f"not inside a git repository: {os.path.abspath(path)}") from None


def resolve(repo: str, rev: str) -> str:
    try:
        return git_out(repo, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
    except GitError:
        raise GitError(f"unknown revision '{rev}'") from None


@dataclass
class Person:
    name: str
    email: str
    date: str  # git internal format "<unix> <tz>"

    @classmethod
    def parse(cls, value: str) -> "Person":
        lt = value.rfind("<")
        gt = value.rfind(">")
        name = value[:lt].strip()
        email = value[lt + 1 : gt]
        date = value[gt + 1 :].strip()
        return cls(name, email, date)


@dataclass
class CommitInfo:
    sha: str
    tree: str
    parents: list[str]
    author: Person
    committer: Person
    message: str
    raw_headers: list[str] = field(default_factory=list)

    @property
    def subject(self) -> str:
        return self.message.split("\n", 1)[0]

    @property
    def short(self) -> str:
        return self.sha[:10]


def read_commit(repo: str, sha: str) -> CommitInfo:
    raw = run_git(repo, "cat-file", "commit", sha).stdout.decode("utf-8", errors="surrogateescape")
    header, _, message = raw.partition("\n\n")
    tree = ""
    parents: list[str] = []
    author = committer = None
    headers = []
    for line in header.split("\n"):
        if line.startswith(" "):
            continue  # continuation (e.g. gpgsig)
        key, _, value = line.partition(" ")
        headers.append(key)
        if key == "tree":
            tree = value
        elif key == "parent":
            parents.append(value)
        elif key == "author":
            author = Person.parse(value)
        elif key == "committer":
            committer = Person.parse(value)
    if author is None or committer is None:
        raise GitError(f"could not parse commit {sha}")
    return CommitInfo(sha, tree, parents, author, committer, message, headers)


@dataclass
class FileChange:
    status: str  # A, M, D, R, C, T
    path: str
    old_path: str | None
    old_mode: str
    new_mode: str
    old_blob: str
    new_blob: str

    @property
    def is_regular(self) -> bool:
        return self.new_mode in ("100644", "100755")


NULL_SHA = "0" * 40


def changed_files(repo: str, commit: CommitInfo) -> list[FileChange]:
    if commit.parents:
        args = ["diff-tree", "-r", "-z", "-M", "--no-commit-id", "--raw", commit.parents[0], commit.sha]
    else:
        args = ["diff-tree", "-r", "-z", "--root", "--no-commit-id", "--raw", commit.sha]
    out = run_git(repo, *args).stdout.decode("utf-8", errors="surrogateescape")
    parts = out.split("\0")
    changes: list[FileChange] = []
    i = 0
    while i < len(parts):
        meta = parts[i]
        if not meta.startswith(":"):
            i += 1
            continue
        old_mode, new_mode, old_blob, new_blob, status = meta[1:].split(" ")
        kind = status[0]
        if kind in ("R", "C"):
            old_path, path = parts[i + 1], parts[i + 2]
            i += 3
        else:
            path = parts[i + 1]
            old_path = path if kind != "A" else None
            i += 2
        changes.append(FileChange(kind, path, old_path, old_mode, new_mode, old_blob, new_blob))
    return changes


def read_blob(repo: str, blob: str) -> bytes:
    if blob == NULL_SHA:
        return b""
    return run_git(repo, "cat-file", "blob", blob).stdout


def is_binary(data: bytes) -> bool:
    return b"\0" in data[:8000]


def recent_commits(repo: str, limit: int = 300) -> list[tuple[str, str]]:
    """(sha, one-line description) for commits reachable from HEAD."""
    fmt = "%H%x00%h %s (%an, %ar)"
    try:
        out = git_out(repo, "log", f"-n{limit}", f"--format={fmt}")
    except GitError:
        return []
    result = []
    for line in out.splitlines():
        sha, _, desc = line.partition("\0")
        result.append((sha, desc))
    return result


# ------------------------------------------------------------------ rewrite


@dataclass
class RewriteResult:
    old_sha: str
    new_sha: str
    old_head: str
    new_head: str
    replayed: int  # number of descendant commits that were re-created


def _identity_env(person: Person, prefix: str) -> dict[str, str]:
    return {
        f"GIT_{prefix}_NAME": person.name,
        f"GIT_{prefix}_EMAIL": person.email,
        f"GIT_{prefix}_DATE": f"@{person.date}",
    }


def _commit_tree(repo: str, tree: str, parents: list[str], commit: CommitInfo, message: str | None = None) -> str:
    args = ["commit-tree", tree]
    for p in parents:
        args += ["-p", p]
    env = _identity_env(commit.author, "AUTHOR")
    msg = commit.message if message is None else message
    return git_out(repo, *args, input=msg.encode("utf-8", errors="surrogateescape"), env=env)


def build_tree(repo: str, commit: CommitInfo, files: dict[str, bytes]) -> str:
    """The commit's tree with `files` (path -> new content) replaced."""
    if not files:
        return commit.tree
    with tempfile.TemporaryDirectory(prefix="troll-") as tmp:
        env = {"GIT_INDEX_FILE": os.path.join(tmp, "index")}
        run_git(repo, "read-tree", commit.tree, env=env)
        for path, data in files.items():
            listing = git_out(repo, "--literal-pathspecs", "ls-files", "-s", "--", path, env=env)
            if not listing:
                raise GitError(f"{path} is not part of commit {commit.short}")
            mode = listing.split()[0]
            blob = git_out(repo, "hash-object", "-w", "--stdin", "--no-filters", input=data)
            run_git(repo, "update-index", "--cacheinfo", f"{mode},{blob},{path}", env=env)
        return git_out(repo, "write-tree", env=env)


def _blob_at(repo: str, commit: str, path: str) -> bytes | None:
    proc = run_git(repo, "cat-file", "blob", f"{commit}:{path}", check=False)
    return proc.stdout if proc.returncode == 0 else None


def _renames(repo: str, old: str, new: str) -> dict[str, str]:
    out = run_git(repo, "diff-tree", "-r", "-z", "-M", "--name-status", old, new).stdout
    parts = out.decode("utf-8", errors="surrogateescape").split("\0")
    renames = {}
    i = 0
    while i < len(parts):
        status = parts[i]
        if status.startswith("R") and i + 2 < len(parts):
            renames[parts[i + 1]] = parts[i + 2]
            i += 3
        elif status:
            i += 2
        else:
            i += 1
    return renames


def _in_progress_operation(repo: str) -> str | None:
    for name, what in (
        ("MERGE_HEAD", "a merge"),
        ("CHERRY_PICK_HEAD", "a cherry-pick"),
        ("REVERT_HEAD", "a revert"),
        ("rebase-merge", "a rebase"),
        ("rebase-apply", "a rebase/am"),
    ):
        path = git_out(repo, "rev-parse", "--git-path", name)
        if not os.path.isabs(path):
            path = os.path.join(repo, path)
        if os.path.exists(path):
            return what
    return None


def check_rewritable(repo: str, sha: str) -> list[str]:
    """Descendants of `sha` up to HEAD (oldest first). Raises if not rewritable."""
    op = _in_progress_operation(repo)
    if op:
        raise GitError(f"cannot rewrite history while {op} is in progress")
    head = resolve(repo, "HEAD")
    commit = read_commit(repo, sha)
    if len(commit.parents) > 1:
        raise GitError(f"{commit.short} is a merge commit; editing merges is not supported")
    if sha == head:
        return []
    if run_git(repo, "merge-base", "--is-ancestor", sha, head, check=False).returncode != 0:
        raise GitError(f"{commit.short} is not an ancestor of HEAD")
    merges = git_out(repo, "rev-list", "--min-parents=2", f"{sha}..{head}")
    if merges:
        raise GitError("there are merge commits between the commit and HEAD; cannot replay them")
    out = git_out(repo, "rev-list", "--reverse", "--topo-order", f"{sha}..{head}")
    return out.split() if out else []


def rewrite_commit(repo: str, sha: str, files: dict[str, bytes], message: str | None = None) -> RewriteResult:
    """Replace the content of `files` in commit `sha` and rebuild history up to HEAD."""
    descendants = check_rewritable(repo, sha)
    old_head = resolve(repo, "HEAD")
    commit = read_commit(repo, sha)
    tree = build_tree(repo, commit, files)
    if tree == commit.tree and (message is None or message == commit.message):
        return RewriteResult(sha, sha, old_head, old_head, 0)
    new_sha = _commit_tree(repo, tree, commit.parents, commit, message)

    parent = new_sha
    tracked = {path: path for path in files}  # edited path -> its name in the commit being replayed
    for child_sha in descendants:
        child = read_commit(repo, child_sha)
        old_parent = child.parents[0]
        renames = _renames(repo, old_parent, child_sha) if tracked else {}
        updates: dict[str, bytes] = {}
        for key, path in list(tracked.items()):
            new_path = renames.get(path, path)
            theirs = _blob_at(repo, child_sha, new_path)
            if theirs is None:  # the later commit deleted the file
                del tracked[key]
                continue
            base = _blob_at(repo, old_parent, path) or b""
            ours = _blob_at(repo, parent, path) or b""
            merged = merge3_bytes(base, ours, theirs)
            if merged is None:
                raise GitError(
                    f"your edit to {path} overlaps with a change made by later commit "
                    f"{child.short} \"{child.subject}\". Nothing was changed."
                )
            if merged != theirs:
                updates[new_path] = merged
            tracked[key] = new_path
        tree = build_tree(repo, child, updates) if updates else child.tree
        parent = _commit_tree(repo, tree, [parent], child)
    new_head = parent

    # Update index + work tree first (fails safely on conflicting local changes).
    run_git(repo, "update-index", "-q", "--refresh", check=False)
    proc = run_git(repo, "read-tree", "-m", "-u", old_head, new_head, check=False)
    if proc.returncode != 0:
        raise GitError(
            "could not update the work tree (uncommitted changes to the edited files?): "
            + proc.stderr.decode(errors="replace").strip()
        )
    run_git(repo, "update-ref", "-m", f"troll: edit {commit.short}", "HEAD", new_head, old_head)
    run_git(repo, "update-ref", "ORIG_HEAD", old_head, check=False)
    return RewriteResult(sha, new_sha, old_head, new_head, len(descendants))


# ------------------------------------------------------------ load session


@dataclass
class CommitFile:
    change: FileChange
    base: bytes  # content before the commit
    content: bytes  # content in the commit
    editable: bool
    reason: str = ""  # why it isn't editable


def load_commit(repo: str, rev: str) -> tuple[CommitInfo, list[CommitFile]]:
    sha = resolve(repo, rev)
    commit = read_commit(repo, sha)
    files = []
    for change in changed_files(repo, commit):
        base = read_blob(repo, change.old_blob) if change.status not in ("A",) else b""
        if change.status == "D":
            files.append(CommitFile(change, base, b"", False, "deleted"))
            continue
        content = read_blob(repo, change.new_blob)
        if not change.is_regular:
            reason = "symlink" if change.new_mode == "120000" else "submodule" if change.new_mode == "160000" else "special file"
            files.append(CommitFile(change, base, content, False, reason))
        elif is_binary(content) or is_binary(base):
            files.append(CommitFile(change, base, content, False, "binary"))
        else:
            files.append(CommitFile(change, base, content, True))
    return commit, files
