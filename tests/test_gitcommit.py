import os

import pytest

from troll import gitcommit
from troll.gitcommit import GitError, load_commit, rewrite_commit


def test_load_commit_lists_files_with_base_and_content(repo):
    repo.commit("base", {"a.py": "x = 1\n", "old.txt": "bye\n", "img.bin": "\0\1"})
    repo.git("mv", "old.txt", "renamed.txt")
    sha = repo.commit("change", {"a.py": "x = 2\n", "new.py": "# new\n", "img.bin": "\0\2"})
    commit, files = load_commit(repo.path, "HEAD")
    assert commit.sha == sha and commit.subject == "change"
    assert commit.author.name == "Ada Author" and commit.author.date.startswith("1700000000")
    by_path = {f.change.path: f for f in files}
    assert by_path["a.py"].base == b"x = 1\n" and by_path["a.py"].content == b"x = 2\n"
    assert by_path["a.py"].editable
    assert by_path["new.py"].change.status == "A" and by_path["new.py"].base == b""
    assert by_path["renamed.txt"].change.status == "R"
    assert by_path["renamed.txt"].change.old_path == "old.txt"
    assert not by_path["img.bin"].editable and by_path["img.bin"].reason == "binary"


def test_rewrite_head_commit_updates_worktree_and_keeps_metadata(repo):
    repo.commit("base", {"a.py": "x = 1\n"})
    old = repo.commit("add comment", {"a.py": "x = 1\n# teh comment\n"}, date="1700000500 +0200")
    result = rewrite_commit(repo.path, old, {"a.py": b"x = 1\n# the comment\n"})
    assert result.old_sha == old and result.new_sha != old
    assert result.new_head == repo.git("rev-parse", "HEAD")
    assert repo.show("HEAD", "a.py") == "x = 1\n# the comment"
    assert repo.read("a.py") == "x = 1\n# the comment\n"
    assert repo.git("log", "-1", "--format=%an|%ae|%ad|%s", "--date=raw") == "Ada Author|ada@example.com|1700000500 +0200|add comment"
    assert repo.git("status", "--porcelain") == ""
    assert repo.git("rev-parse", "ORIG_HEAD") == old
    assert "troll: edit" in repo.git("reflog", "-1")


def test_rewrite_older_commit_replays_descendants(repo):
    repo.commit("base", {"a.py": "def f():\n    # frist\n    return 1\n", "b.py": "b\n"})
    target = repo.commit("comment", {"a.py": "def f():\n    # frist comment\n    return 1\n"})
    repo.commit("later touches a", {"a.py": "def f():\n    # frist comment\n    return 2\n"})
    repo.commit("later touches b", {"b.py": "bb\n"})
    result = rewrite_commit(repo.path, target, {"a.py": b"def f():\n    # first comment\n    return 1\n"})
    assert result.replayed == 2
    assert repo.log() == ["later touches b", "later touches a", "comment", "base"]
    assert repo.show("HEAD~2", "a.py") == "def f():\n    # first comment\n    return 1"
    assert repo.show("HEAD", "a.py") == "def f():\n    # first comment\n    return 2"
    assert repo.show("HEAD", "b.py") == "bb"
    assert repo.read("a.py") == "def f():\n    # first comment\n    return 2\n"
    assert repo.git("status", "--porcelain") == ""


def test_conflicting_descendant_aborts_without_changes(repo):
    repo.commit("base", {"a.txt": "one\n"})
    target = repo.commit("edit", {"a.txt": "two\n"})
    later = repo.commit("later", {"a.txt": "three\n"})
    with pytest.raises(GitError, match="overlaps with a change made by later commit"):
        rewrite_commit(repo.path, target, {"a.txt": b"TWO\n"})
    assert repo.git("rev-parse", "HEAD") == later
    assert repo.read("a.txt") == "three\n"
    assert repo.git("status", "--porcelain") == ""


def test_edit_message_only(repo):
    repo.commit("base", {"a": "a\n"})
    sha = repo.commit("typo in mesage", {"a": "b\n"})
    rewrite_commit(repo.path, sha, {}, "typo in message\n\nWith a body.\n")
    assert repo.git("log", "-1", "--format=%B") == "typo in message\n\nWith a body."
    assert repo.show("HEAD", "a") == "b"


def test_no_change_is_a_noop(repo):
    sha = repo.commit("base", {"a": "a\n"})
    result = rewrite_commit(repo.path, sha, {"a": b"a\n"})
    assert result.new_sha == sha and repo.git("rev-parse", "HEAD") == sha


def test_root_commit_can_be_edited(repo):
    root = repo.commit("root", {"a": "helo\n"})
    repo.commit("second", {"b": "b\n"})
    rewrite_commit(repo.path, root, {"a": b"hello\n"})
    assert repo.show("HEAD~1", "a") == "hello"
    assert repo.git("rev-list", "--max-parents=0", "HEAD") != root


def test_executable_bit_is_preserved(repo):
    repo.commit("base", {"run.sh": "echo hi\n"})
    os.chmod(os.path.join(repo.path, "run.sh"), 0o755)
    sha = repo.commit("exec", {"run.sh": "echo hi there\n"})
    rewrite_commit(repo.path, sha, {"run.sh": b"echo hello there\n"})
    assert repo.git("ls-tree", "HEAD", "run.sh").split()[0] == "100755"


def test_unrelated_local_changes_survive(repo):
    repo.commit("base", {"a": "a\n", "b": "b\n"})
    sha = repo.commit("edit a", {"a": "aa\n"})
    repo.write("b", "local edit\n")
    repo.write("untracked", "u\n")
    rewrite_commit(repo.path, sha, {"a": b"AA\n"})
    assert repo.read("b") == "local edit\n"
    assert repo.read("untracked") == "u\n"
    assert repo.read("a") == "AA\n"


def test_local_changes_to_edited_file_block_rewrite(repo):
    repo.commit("base", {"a": "a\n"})
    sha = repo.commit("edit a", {"a": "aa\n"})
    repo.write("a", "dirty\n")
    with pytest.raises(GitError, match="work tree"):
        rewrite_commit(repo.path, sha, {"a": b"AA\n"})
    assert repo.git("rev-parse", "HEAD") == sha
    assert repo.read("a") == "dirty\n"


def test_refuses_merges_and_non_ancestors(repo):
    base = repo.commit("base", {"a": "a\n"})
    repo.git("checkout", "-q", "-b", "side")
    side = repo.commit("side", {"s": "s\n"})
    repo.git("checkout", "-q", "main")
    repo.commit("main", {"m": "m\n"})
    with pytest.raises(GitError, match="not an ancestor"):
        rewrite_commit(repo.path, side, {"s": b"x\n"})
    repo.git("merge", "-q", "--no-edit", "side")
    with pytest.raises(GitError, match="merge commit"):
        rewrite_commit(repo.path, "HEAD", {})
    with pytest.raises(GitError, match="merge commits between"):
        rewrite_commit(repo.path, base, {"a": b"b\n"})


def test_refuses_during_rebase_like_operation(repo):
    repo.commit("base", {"a": "a\n"})
    sha = repo.commit("x", {"a": "b\n"})
    git_dir = repo.git("rev-parse", "--git-dir")
    os.makedirs(os.path.join(repo.path, git_dir, "rebase-merge"))
    with pytest.raises(GitError, match="rebase"):
        rewrite_commit(repo.path, sha, {"a": b"c\n"})


def test_detached_head(repo):
    repo.commit("base", {"a": "a\n"})
    sha = repo.commit("x", {"a": "b\n"})
    repo.git("checkout", "-q", "--detach")
    rewrite_commit(repo.path, sha, {"a": b"B\n"})
    assert repo.show("HEAD", "a") == "B"
    assert repo.show("main", "a") == "b"  # the branch itself was not touched


def test_paths_with_glob_characters(repo):
    repo.commit("base", {"we[ir]d*.txt": "a\n", "weid.txt": "z\n"})
    sha = repo.commit("x", {"we[ir]d*.txt": "b\n"})
    rewrite_commit(repo.path, sha, {"we[ir]d*.txt": b"c\n"})
    assert repo.show("HEAD", "we[ir]d*.txt") == "c"
    assert repo.show("HEAD", "weid.txt") == "z"


def test_unknown_revision_and_not_a_repo(repo, tmp_path):
    repo.commit("base", {"a": "a\n"})
    with pytest.raises(GitError, match="unknown revision"):
        load_commit(repo.path, "nope")
    lonely = tmp_path / "lonely"
    lonely.mkdir()
    with pytest.raises(GitError, match="not inside a git repository"):
        gitcommit.repo_root(str(lonely))


def test_recent_commits(repo):
    repo.commit("first", {"a": "a\n"})
    repo.commit("second", {"a": "b\n"})
    items = gitcommit.recent_commits(repo.path)
    assert [d.split(" ", 1)[1].startswith(s) for (_sha, d), s in zip(items, ["second", "first"])] == [True, True]


def test_replay_follows_renames_and_deletions(repo):
    repo.commit("base", {"a.py": "# helo\nx = 1\n", "gone.py": "# helo\n"})
    target = repo.commit("comments", {"a.py": "# helo world\nx = 1\n", "gone.py": "# helo world\n"})
    repo.git("mv", "a.py", "b.py")
    repo.commit("rename", {"b.py": "# helo world\nx = 2\n"})
    repo.git("rm", "-q", "gone.py")
    repo.commit("delete")
    rewrite_commit(repo.path, target, {"a.py": b"# hello world\nx = 1\n", "gone.py": b"# hello world\n"})
    assert repo.show("HEAD", "b.py") == "# hello world\nx = 2"
    assert repo.git("ls-tree", "--name-only", "HEAD").split() == ["b.py"]
    assert repo.read("b.py") == "# hello world\nx = 2\n"
