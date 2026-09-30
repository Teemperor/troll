"""End-to-end tests of the commit editor, driven by key presses like a user."""

from diffedit.editor import Editor
from diffedit.prompt import Choice, Picker
from diffedit.settings import Settings
from diffedit.view import build_frame

BASE = "".join(f"line {i}\n" for i in range(40))


def make_history(repo):
    repo.commit("base", {"src/app.py": BASE, "README.md": "# Readme\n"})
    changed = BASE.replace("line 20\n", "# Thsi helper is teh best\nline 20\n").replace("line 5\n", "")
    target = repo.commit("Add helper comment", {"src/app.py": changed, "docs.txt": "some docs\n"})
    repo.commit("Later change", {"src/app.py": changed.replace("line 30", "line thirty")})
    return target


def open_editor(repo, rev) -> Editor:
    ed = Editor(Settings(), cwd=repo.path, raise_errors=True)
    ed.open_commit(rev)
    return ed


def test_overview_lists_message_and_files(repo):
    target = make_history(repo)
    ed = open_editor(repo, target)
    assert ed.in_overview
    labels = [e.label for e in ed.commit.entries]
    assert labels == ["Commit message", "docs.txt", "src/app.py"]
    text = build_frame(ed, 30, 100).text()
    assert "Add helper comment" in text
    assert "src/app.py" in text and "+1" in text and "-1" in text
    assert "1 later commit will be replayed" in text


def test_edit_comment_and_rewrite_through_keys(repo):
    target = make_history(repo)
    ed = open_editor(repo, target)
    assert ed.commit.selected == 1  # the first file is preselected
    ed.keys("Down")
    assert ed.commit.selected == 2
    ed.keys("Enter")
    doc = ed.doc
    assert doc is not None and doc.diff is not None
    # the cursor starts on the first change (the removed "line 5" ghost is at row 5)
    assert doc.cursor[0] == 5
    ed.keys("M-Down")
    assert doc.lines[doc.cursor[0]] == "# Thsi helper is teh best"
    # fix the typos with the replace command
    ed.run_line("s/Thsi/This/")
    ed.run_line("s/teh/the/")
    assert doc.lines[doc.cursor[0]] == "# This helper is the best"
    frame = build_frame(ed, 30, 100).text()
    assert "*+" in frame  # edited marker + added marker in the gutter
    assert "src/app.py" in frame and "edited" in frame
    ed.keys("C-s")
    assert isinstance(ed.prompt, Choice) and "replay 1 later commit" in ed.prompt.label
    ed.keys("y")
    assert "Rewrote" in ed.message.text and "git reset --keep" in ed.message.text
    assert "# This helper is the best" in repo.show("HEAD~1", "src/app.py")
    assert "line thirty" in repo.show("HEAD", "src/app.py")
    assert "# This helper is the best" in repo.read("src/app.py")
    assert repo.log() == ["Later change", "Add helper comment", "base"]
    assert not ed.commit.dirty
    # leaving without further changes doesn't ask anything
    ed.keys("C-x", "C-x")
    assert ed.quit_requested


def test_ghost_lines_and_folding_in_frame(repo):
    target = make_history(repo)
    ed = open_editor(repo, target)
    ed.open_entry(2)
    frame = build_frame(ed, 40, 100).text()
    assert "- line 5" in frame
    assert "unchanged lines" in frame
    rows = ed.doc.layout()
    assert any(r.kind == "ghost" and r.text == "line 5" for r in rows)
    ed.keys("M-z")
    assert not ed.doc.diff.fold
    assert "unchanged lines" not in build_frame(ed, 60, 100).text()


def test_cursor_skips_folded_and_ghost_rows(repo):
    target = make_history(repo)
    ed = open_editor(repo, target)
    ed.open_entry(2)
    doc = ed.doc
    doc.set_cursor((7, 0))  # last visible line of the first hunk's context
    ed.keys("Down")
    assert doc.cursor[0] == 16  # jumped over the fold to the next hunk's context


def test_editing_message(repo):
    target = make_history(repo)
    ed = open_editor(repo, target)
    ed.keys("m")
    assert ed.doc.lang.name == "gitcommit"
    ed.keys("End")
    ed.type("s")
    ed.keys("C-x")  # back to the list
    assert ed.in_overview
    frame = build_frame(ed, 30, 100).text()
    assert "edited" in frame
    ed.keys("C-x")
    assert isinstance(ed.prompt, Choice) and "before leaving" in ed.prompt.label
    ed.keys("y", "y")
    assert repo.git("log", "-1", "--skip=1", "--format=%s") == "Add helper comments"
    assert ed.quit_requested


def test_leaving_without_applying_discards(repo):
    target = make_history(repo)
    head = repo.git("rev-parse", "HEAD")
    ed = open_editor(repo, target)
    ed.open_entry(1)
    ed.type("x")
    ed.keys("C-x", "C-x", "n")
    assert ed.quit_requested
    assert repo.git("rev-parse", "HEAD") == head


def test_conflict_is_reported_and_nothing_changes(repo):
    target = make_history(repo)
    head = repo.git("rev-parse", "HEAD")
    ed = open_editor(repo, target)
    ed.open_entry(2)
    doc = ed.doc
    doc.goto(doc.lines.index("line 30"), 0)  # the later commit changes this line too
    doc.move_end()
    ed.type("!")
    ed.keys("C-s", "y")
    assert ed.message.kind == "error" and "overlaps" in ed.message.text
    assert repo.git("rev-parse", "HEAD") == head
    assert ed.commit.dirty  # the edit is still there to fix up


def test_revert_hunk_restores_commit_version(repo):
    target = make_history(repo)
    ed = open_editor(repo, target)
    ed.open_entry(2)
    doc = ed.doc
    row = doc.lines.index("# Thsi helper is teh best")
    doc.goto(row, 0)
    ed.keys("C-k")
    assert "# Thsi helper is teh best" not in doc.lines
    doc.goto(row, 0)
    ed.run_line("revert")
    assert doc.lines[row] == "# Thsi helper is teh best"
    assert not doc.changed_from_original


def test_switching_files_with_alt_arrows(repo):
    target = make_history(repo)
    ed = open_editor(repo, target)
    ed.keys("M->")
    assert ed.commit.entry.label == "docs.txt"
    ed.keys("M->")
    assert ed.commit.entry.label == "src/app.py"
    ed.keys("M-<")
    assert ed.commit.entry.label == "docs.txt"


def test_single_file_commit_opens_directly(repo):
    repo.commit("base", {"a.py": "x = 1\n"})
    sha = repo.commit("one file", {"a.py": "x = 2\n"})
    ed = open_editor(repo, sha)
    assert not ed.in_overview and ed.commit.entry.label == "a.py"


def test_commit_picker(repo):
    make_history(repo)
    ed = Editor(Settings(), cwd=repo.path, raise_errors=True)
    ed.commit_picker()
    assert isinstance(ed.overlay, Picker)
    ed.type("helper")
    ed.keys("Enter")
    assert ed.commit is not None and ed.commit.commit.subject == "Add helper comment"


def test_commit_command_from_normal_editing(repo, tmp_path):
    make_history(repo)
    ed = Editor(Settings(), cwd=repo.path, raise_errors=True)
    ed.open_file("README.md")
    ed.run_line("commit HEAD~1")
    assert ed.commit is not None
    ed.keys("C-x")  # leave the commit editor: back to README
    assert ed.commit is None and not ed.quit_requested
    assert ed.doc.path.endswith("README.md")


def test_save_trims_only_edited_lines_in_commit(repo):
    repo.commit("base", {"a.py": "keep  \nx = 1\n"})
    sha = repo.commit("c", {"a.py": "keep  \nx = 2\n"})
    ed = open_editor(repo, sha)
    doc = ed.doc
    doc.goto(1, 5)
    ed.type("   ")
    ed.keys("C-s", "y")
    # the trailing spaces typed on the edited line are trimmed, so nothing is left to rewrite,
    # and the untouched line keeps its trailing whitespace
    assert ed.message.text == "Nothing changed"
    assert repo.git("rev-parse", "HEAD") == sha
    assert repo.show("HEAD", "a.py") == "keep  \nx = 2"


def test_non_editable_entries(repo):
    repo.commit("base", {"img.bin": "\0\1", "a.txt": "a\n"})
    sha = repo.commit("bin", {"img.bin": "\0\2", "a.txt": "b\n"})
    ed = open_editor(repo, sha)
    assert ed.commit.entry.label == "a.txt"  # the only editable file is opened directly
    ed.keys("C-x")
    idx = [e.label for e in ed.commit.entries].index("img.bin")
    ed.commit.selected = idx
    ed.keys("Enter")
    assert ed.in_overview and "can't be edited (binary)" in ed.message.text
