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


def open_editor(repo, rev, **settings) -> Editor:
    ed = Editor(Settings(**settings), cwd=repo.path, raise_errors=True)
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


def test_next_change_from_the_message_jumps_to_the_first_change(repo):
    target = make_history(repo)
    ed = open_editor(repo, target)
    ed.open_entry(0)
    ed.keys("M-Down")
    assert ed.commit.current == 1 and ed.doc.cursor == (0, 0)  # docs.txt: added from line 0
    ed.keys("M-Up")
    assert ed.commit.current == 0  # back to the message
    ed.keys("M-Up")
    assert ed.commit.current == 2 and ed.doc.lines[ed.doc.cursor[0]] == "# Thsi helper is teh best"


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
    assert not ed.doc.settings.only_changes
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


def run_palette(ed, line):
    ed.keys("C-t")
    ed.type(line)
    ed.keys("Enter")


def test_revert_hunk_drops_the_commits_change(repo):
    target = make_history(repo)
    ed = open_editor(repo, target)
    ed.open_entry(2)
    doc = ed.doc
    # an added line: it goes away
    doc.goto(doc.lines.index("# Thsi helper is teh best"), 0)
    run_palette(ed, "revert-hunk")
    assert "# Thsi helper is teh best" not in doc.lines
    assert "Reverted" in ed.message.text
    # a deletion (the ghost precedes "line 6"): the line comes back
    doc.goto(doc.lines.index("line 6"), 0)
    run_palette(ed, "revert-hunk")
    assert doc.lines[5] == "line 5"
    assert doc.lines == doc.diff.base
    # nothing left to revert
    doc.goto(10, 0)
    run_palette(ed, "revert-hunk")
    assert "doesn't change anything" in ed.message.text
    ed.keys("M-u")  # undo brings the deletion back
    assert "line 5" not in doc.lines


def test_revert_hunk_then_apply_rewrites_commit_without_it(repo):
    target = make_history(repo)
    ed = open_editor(repo, target)
    ed.open_entry(2)
    doc = ed.doc
    doc.goto(doc.lines.index("# Thsi helper is teh best"), 0)
    run_palette(ed, "revert-hunk")
    ed.keys("C-s", "y")
    assert "Rewrote" in ed.message.text
    old = repo.show("HEAD~1", "src/app.py")
    assert "# Thsi helper" not in old and "line 5\n" not in old  # the other change stays
    assert "line thirty" in repo.show("HEAD", "src/app.py")


def test_revert_hunk_at_end_of_file(repo):
    repo.commit("base", {"a.txt": "a\nb\nc\n"})
    sha = repo.commit("drop tail", {"a.txt": "a\n"})
    ed = open_editor(repo, sha)
    doc = ed.doc
    doc.goto(len(doc.lines) - 1, 0)
    run_palette(ed, "revert-hunk")
    assert "\n".join(doc.lines) == "a\nb\nc\n"


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


def test_side_by_side_is_the_default(repo):
    target = make_history(repo)
    ed = open_editor(repo, target)
    ed.open_entry(2)
    assert "before this commit" in build_frame(ed, 40, 120).text().split("\n")[1]


def test_side_by_side_view(repo):
    target = make_history(repo)
    ed = open_editor(repo, target, side_by_side=False)
    ed.open_entry(2)
    ed.run_line("side-by-side")
    assert all(e.doc.settings.side_by_side for e in ed.commit.entries if e.doc)  # every file of the commit
    doc = ed.doc
    doc.goto(doc.lines.index("# Thsi helper is teh best"), 2)
    f = build_frame(ed, 40, 120)
    lines = f.text().split("\n")
    assert "your version (editable)" in lines[1] and "before this commit" in lines[1]
    comment_row = next(l for l in lines if "Thsi helper" in l)
    left, right = comment_row.split("│")
    assert "+ # Thsi helper" in left and right.strip() == ""  # added line: nothing on the right
    removed_row = next(l for l in lines if "line 5" in l.split("│")[-1] and "-" in l.split("│")[-1])
    assert removed_row.split("│")[0].strip() == ""  # removed line: nothing on the left
    y, x = f.cursor
    assert lines[y][x] == "T"  # the cursor sits on the character it points at
    # the removed line's row is skipped by the cursor
    doc.goto(doc.lines.index("line 4"), 0)
    ed.keys("Down")
    assert doc.lines[doc.cursor[0]] == "line 6"
    # editing still works and is reflected on the left only
    ed.type("X")
    assert any("Xline 6" in l.split("│")[0] for l in build_frame(ed, 40, 120).text().split("\n"))
    ed.run_line("side-by-side")
    assert not doc.settings.side_by_side


def test_side_by_side_from_settings_panel(repo):
    target = make_history(repo)
    ed = open_editor(repo, target, side_by_side=False)
    ed.open_entry(1)
    ed.run_line("settings")
    panel = ed.overlay
    panel.selected = [o.key for o in panel.options].index("side_by_side")
    ed.keys("Enter", "Esc")
    assert all(e.doc.settings.side_by_side for e in ed.commit.entries if e.doc)
    assert ed.settings.side_by_side


def test_comment_hunk_uses_the_languages_comment_marker(repo):
    target = make_history(repo)
    ed = open_editor(repo, target)
    ed.open_entry(2)  # src/app.py: Python
    doc = ed.doc
    row = doc.lines.index("# Thsi helper is teh best")
    doc.goto(row, 0)
    run_palette(ed, "comment-hunk")
    assert "already commented out" in ed.message.text
    doc.goto(doc.lines.index("line 6"), 0)  # the removed "line 5" is before it
    run_palette(ed, "comment-hunk")
    assert "only removes lines" in ed.message.text
    doc.goto(0, 0)
    run_palette(ed, "comment-hunk")
    assert "doesn't change anything" in ed.message.text


def test_comment_hunk_cpp_and_python(repo):
    repo.commit("base", {"a.cpp": "int a;\nint b;\n", "b.py": "x = 1\n"})
    target = repo.commit("add", {"a.cpp": "int a;\n  foo();\n\n  bar();\nint b;\n", "b.py": "x = 1\nif x:\n    y = 2\n"})
    ed = open_editor(repo, target)
    ed.open_entry([e.label for e in ed.commit.entries].index("a.cpp"))
    doc = ed.doc
    doc.goto(2, 0)
    run_palette(ed, "comment-hunk")
    assert doc.lines[:5] == ["int a;", "  // foo();", "", "  // bar();", "int b;"]
    assert "Commented out 3 lines" in ed.message.text
    ed.keys("M-u")
    assert doc.lines[1] == "  foo();"
    ed.open_entry([e.label for e in ed.commit.entries].index("b.py"))
    doc = ed.doc
    doc.goto(2, 0)
    run_palette(ed, "comment-hunk")
    assert doc.lines[:3] == ["x = 1", "# if x:", "#     y = 2"]


def test_only_changes_mode(repo):
    target = make_history(repo)
    ed = open_editor(repo, target)
    ed.open_entry(2)
    doc = ed.doc
    assert doc.settings.only_changes  # on by default: unchanged code is folded
    assert "unchanged lines" in build_frame(ed, 40, 100).text()
    run_palette(ed, "only-changes off")
    assert "Only-changes mode off" in ed.message.text
    assert all(e.doc.settings.only_changes is False for e in ed.commit.entries if e.doc)  # every file
    assert not ed.settings.only_changes
    frame = build_frame(ed, 30, 100).text()
    assert "unchanged lines" not in frame and "line 39" not in frame
    doc.goto(39, 0)
    assert "line 39" in build_frame(ed, 30, 100).text()  # the whole file is reachable
    run_palette(ed, "only-changes off")  # explicit values don't toggle
    assert not doc.settings.only_changes
    run_palette(ed, "only-changes")  # no argument: toggle
    assert doc.settings.only_changes
    assert "unchanged lines" in build_frame(ed, 40, 100).text()
    run_palette(ed, "only-changes maybe")
    assert "Expected on or off" in ed.message.text and doc.settings.only_changes


def test_only_changes_from_settings_and_config(repo):
    target = make_history(repo)
    ed = open_editor(repo, target, only_changes=False)
    ed.open_entry(2)
    assert "unchanged lines" not in build_frame(ed, 60, 100).text()
    run_palette(ed, "set onlychanges on")
    assert all(e.doc.settings.only_changes for e in ed.commit.entries if e.doc)
    assert "unchanged lines" in build_frame(ed, 40, 100).text()
    ed.commit.close()
    run_palette(ed, "only-changes")  # works from the file list too
    assert not ed.settings.only_changes
