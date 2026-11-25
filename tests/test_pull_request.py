import json

import pytest

from troll import github, markdown, prview
from troll.editor import Editor
from troll.prompt import Picker
from troll.prview import PullRequestView
from troll.settings import Settings
from troll.view import build_frame

URL = "https://github.com/llvm/llvm-project/pull/7"

DIFF = """diff --git a/lib/a.cpp b/lib/a.cpp
index 111..222 100644
--- a/lib/a.cpp
+++ b/lib/a.cpp
@@ -10,3 +10,3 @@ int f() {
   int x = 1;
-  return x;
+  return x + 1;
 }
"""

API = {
    "pulls/7": {
        "title": "[lldb] Fix the frobnicator", "body": "## Summary\nThis fixes **frobs**.\n\n```cpp\nint x;\n```",
        "user": {"login": "alice"}, "state": "closed", "merged_at": "2026-11-20T10:00:00Z",
        "merged_by": {"login": "bob"}, "merge_commit_sha": "abcdef1234567", "created_at": "2026-11-18T10:00:00Z",
        "base": {"label": "llvm:main"}, "head": {"label": "alice:frob"}, "author_association": "CONTRIBUTOR",
        "additions": 1, "deletions": 1, "labels": [{"name": "lldb"}], "requested_reviewers": [{"login": "carol"}],
        "assignees": [],
    },
    "issues/7/comments": [
        {"id": 1, "user": {"login": "bot"}, "body": "Thanks for the patch", "created_at": "2026-11-18T11:00:00Z"},
    ],
    "pulls/7/reviews": [
        {"id": 50, "user": {"login": "bob"}, "state": "COMMENTED", "body": "", "submitted_at": "2026-11-19T09:00:00Z"},
        {"id": 51, "user": {"login": "alice"}, "state": "COMMENTED", "body": "", "submitted_at": "2026-11-19T10:00:00Z"},
        {"id": 52, "user": {"login": "bob"}, "state": "APPROVED", "body": "LGTM", "submitted_at": "2026-11-19T12:00:00Z"},
    ],
    "pulls/7/comments": [
        {"id": 100, "user": {"login": "bob"}, "body": "Why `+ 1`?", "path": "lib/a.cpp", "line": 11,
         "diff_hunk": "@@ -10,3 +10,3 @@ int f() {\n   int x = 1;\n-  return x;\n+  return x + 1;",
         "created_at": "2026-11-19T09:00:00Z", "pull_request_review_id": 50},
        {"id": 101, "user": {"login": "alice"}, "body": "Off by one.", "path": "lib/a.cpp", "line": 11,
         "in_reply_to_id": 100, "created_at": "2026-11-19T10:00:00Z", "pull_request_review_id": 51},
    ],
    "pulls/7/commits": [
        {"sha": "e375f87aaaa", "author": {"login": "alice"},
         "commit": {"message": "Fix the frobnicator\n\nbody", "committer": {"date": "2026-11-18T09:00:00Z"}}},
    ],
    "pulls/7/files": [{"filename": "lib/a.cpp", "additions": 1, "deletions": 1}],
}


def fake_gh(calls=None):
    def run(args):
        if calls is not None:
            calls.append(args)
        path = args[-1].split("?")[0].removeprefix("repos/llvm/llvm-project/")
        if any("vnd.github.diff" in a for a in args):
            return DIFF.encode()
        if path not in API:
            raise github.GhError(f"gh: Not Found ({path})")
        return json.dumps(API[path]).encode()
    return run


@pytest.fixture
def gh(monkeypatch):
    calls = []
    monkeypatch.setattr(github, "run_gh", fake_gh(calls))
    return calls


def text(ed, h=120, w=120):
    return build_frame(ed, h, w).text()


def test_fetch_builds_the_conversation(gh):
    pr = github.fetch(URL)
    assert pr.state == "merged" and pr.merged_by == "bob" and pr.base == "llvm:main"
    assert [c.author for c in pr.comments] == ["bot"]
    thread = next(r for r in pr.reviews if r.id == 50).threads[0]
    assert thread.body == "Why `+ 1`?" and [c.body for c in thread.replies] == ["Off by one."]
    assert next(r for r in pr.reviews if r.id == 51).threads == []  # the reply lives in bob's thread
    assert pr.reviewers() == [("bob", "APPROVED"), ("carol", "PENDING")]
    kinds = [e.kind for e in github.timeline(pr)]
    assert kinds == ["commits", "comment", "review", "review", "merged"]  # alice's reply-only review is hidden
    assert pr.diff.startswith("diff --git") and pr.files[0].path == "lib/a.cpp"
    assert ["api", "--paginate", "repos/llvm/llvm-project/pulls/7/reviews?per_page=100"] in gh


def test_paginated_output_is_concatenated():
    assert github._decode_pages(b'[1, 2]\n[3]\n') == [1, 2, 3]


def test_pr_urls():
    ref = github.parse_pr_url("https://git.example.com/org/repo/pull/12/files")
    assert (ref.host, ref.owner, ref.repo, ref.number) == ("git.example.com", "org", "repo", 12)
    assert github.parse_pr_url("https://example.com/x") is None


def test_markdown_rendering():
    lines = markdown.render("# Title\nSome **bold** and `code`, see #12 by @bob.\n\n- [x] done\n"
                            "> quoted\n\n```python\ndef f(): pass\n```\n<!-- hidden -->\n| a | b |\n|---|---|\n| 1 | 2 |",
                            40)
    plain = ["".join(t for t, _, _ in runs) for runs, _ in lines]
    assert plain[0] == "Title" and plain[1] == "─" * 40
    assert plain[2] == "Some bold and code, see #12 by @bob."
    styles = {t: fg for runs, _ in lines for t, fg, _ in runs}
    assert styles["bold"] == "md.bold" and styles["code"] == "md.codetext" and styles["@bob"] == "md.mention"
    assert "☑ done" in plain and "▎ quoted" in plain and "hidden" not in "".join(plain)
    assert any(fill == "md.block" and "def f(): pass" in p for p, (_, fill) in zip(plain, lines))
    assert "│ 1 │ 2 │" in plain


def test_markdown_wraps_words():
    lines = markdown.render("aaa bbb ccc ddd", 8)
    assert ["".join(t for t, _, _ in runs) for runs, _ in lines] == ["aaa bbb", "ccc ddd"]


def test_open_pr_command_shows_the_pr_like_github(gh, tmp_path):
    ed = Editor(Settings(), cwd=str(tmp_path), raise_errors=True)
    ed.keys("C-t")
    ed.type("open-pr 7")
    ed.keys("Enter")
    assert isinstance(ed.overlay, PullRequestView)
    screen = text(ed)
    assert "llvm/llvm-project · Pull request #7" in screen
    assert "[lldb] Fix the frobnicator #7" in screen and "⑂ Merged" in screen
    assert "bob merged 1 commit into  llvm:main  from  alice:frob" in screen.replace("\u00a0", " ")
    assert "bob reviewed" in screen and "bob commented on" not in screen
    assert "Conversation" in screen and "+1 −1" in screen
    assert "alice commented" in screen and "This fixes frobs." in screen and "int x;" in screen
    assert "Reviewers" in screen and "carol" in screen and "Labels" in screen  # the sidebar
    assert "bob approved these changes" in screen and "LGTM" in screen
    assert "lib/a.cpp:11" in screen and "Why + 1?" in screen and "Off by one." in screen
    assert "-  return x;" in screen and "bob merged commit abcdef1 into" in screen

    ed.keys("Tab")
    screen = text(ed)
    assert "Commits on Nov 18, 2026" in screen and "Fix the frobnicator" in screen and "e375f87" in screen
    ed.keys("3")
    screen = text(ed)
    assert "Showing 1 changed file with 1 additions and 1 deletions" in screen
    assert "   11 +  return x + 1;" in screen and "   11      -  return x;" in screen

    ed.keys("1")
    ed.overlay.height = 10
    ed.keys("n")
    assert ed.overlay.scroll > 0
    opened = []
    ed.open_url = lambda url: opened.append(url) or "open"
    ed.keys("o")
    assert opened == [URL]
    ed.keys("Esc")
    assert ed.overlay is None

    calls = len(gh)
    ed.run_line("pr #7")  # cached
    ed.poll_task(wait=True)
    assert isinstance(ed.overlay, PullRequestView) and len(gh) == calls


def test_open_pr_narrow_terminal_has_no_sidebar(gh, tmp_path):
    ed = Editor(Settings(), cwd=str(tmp_path), raise_errors=True)
    ed.run_line("open-pr " + URL)
    ed.poll_task(wait=True)
    screen = text(ed, 60, 80)
    assert "Reviewers" not in screen and "bob approved these changes" in screen
    assert all(len(line) <= 80 for line in screen.split("\n"))


def test_open_pr_errors(tmp_path):
    ed = Editor(Settings(), cwd=str(tmp_path))
    ed.run_line("open-pr 7")  # the conftest gh refuses
    ed.poll_task(wait=True)
    assert ed.overlay is None and "gh is disabled" in ed.message.text
    ed.settings.pr_url = "https://example.com/{}"
    ed.run_line("open-pr 7")
    ed.poll_task(wait=True)
    assert "Not a GitHub pull request URL" in ed.message.text
    ed.run_line("open-pr")  # nothing to infer: asks
    ed.poll_task(wait=True)
    assert ed.prompt is not None and "Pull request" in ed.prompt.label


def test_commit_list_shows_the_pr_and_returns(gh, repo):
    repo.commit("base", {"a.txt": "1\n"})
    repo.commit("Fix the frobnicator (#7)", {"a.txt": "2\n"})
    ed = Editor(Settings(), cwd=repo.path, raise_errors=True)
    ed.commit_picker()
    ed.keys("C-r")
    assert isinstance(ed.overlay, PullRequestView) and ed.overlay.pr.ref.number == 7
    assert "PR in Browser" not in text(ed) and "Next Tab" in text(ed)
    ed.keys("Esc")
    assert isinstance(ed.overlay, Picker) and ed.overlay.kind == "commits"


def test_open_pr_in_a_commit_uses_its_subject(gh, repo):
    repo.commit("base", {"a.txt": "1\n"})
    repo.commit("Fix the frobnicator (#7)", {"a.txt": "2\n"})
    ed = Editor(Settings(), cwd=repo.path, raise_errors=True)
    ed.open_commit("HEAD")
    ed.run_line("open-pr")
    ed.poll_task(wait=True)
    assert isinstance(ed.overlay, PullRequestView) and ed.overlay.pr.ref.number == 7
    ed.keys("Esc")
    assert ed.overlay is None and ed.commit is not None
