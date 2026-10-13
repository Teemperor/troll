"""Blame mode: commit titles next to each line, Enter shows the commit, driven like a user."""

import os

from troll import blame
from troll.blame import CommitView
from troll.editor import Editor
from troll.settings import Settings
from troll.view import build_frame


def run_palette(ed, line):
    ed.keys("C-t")
    ed.type(line)
    ed.keys("Enter")


def make_history(repo):
    repo.commit("Add the app", {"app.py": "def main():\n    return 1\n"})
    repo.commit("Return two instead", {"app.py": "def main():\n    return 2\n\n# done\n"})


def open_file(repo, name="app.py", **settings) -> Editor:
    settings.setdefault("line_numbers", False)
    ed = Editor(Settings(**settings), cwd=repo.path, raise_errors=True)
    ed.open_file(os.path.join(repo.path, name))
    return ed


def test_blame_shows_commit_titles_next_to_lines(repo):
    make_history(repo)
    ed = open_file(repo)
    run_palette(ed, "blame")
    lines = build_frame(ed, 12, 80).text().split("\n")
    assert "[Blame]" in lines[0]
    assert lines[1].startswith("Add the app        def main():")
    assert lines[2].startswith("Return two instead     return 2")
    assert lines[4].startswith("Return two instead # done")
    assert lines[5].startswith("                   ")  # the "" after the final newline has no commit


def test_blame_mode_only_moves_between_lines(repo):
    make_history(repo)
    ed = open_file(repo)
    run_palette(ed, "blame")
    ed.type("x")
    ed.keys("Backspace", "C-k")
    assert ed.doc.text() == "def main():\n    return 2\n\n# done\n" and not ed.doc.modified
    assert "Blame mode" in ed.message.text
    ed.keys("Down", "Down", "Up")
    assert ed.doc.cursor[0] == 1
    ed.keys("Esc")
    assert ed.doc.blame is None and "[Blame]" not in build_frame(ed, 12, 80).text()
    ed.type("x")
    assert ed.doc.modified


def test_enter_shows_the_commit_with_a_highlighted_diff(repo):
    make_history(repo)
    ed = open_file(repo)
    run_palette(ed, "blame")
    ed.keys("Down", "Enter")
    assert isinstance(ed.overlay, CommitView)
    frame = build_frame(ed, 30, 80)
    text = frame.text()
    assert "Return two instead" in text and "Author: Ada Author" in text
    assert "-    return 1" in text and "+    return 2" in text
    styles = {(seg.strip(), fg, bg) for row in frame.rows for seg, fg, bg in row}
    assert ("return", "keyword", "add") in styles  # code highlighted inside the changed lines
    assert ("return", "keyword", "del") in styles
    assert ("Return two instead", "heading", None) in styles
    ed.keys("PageDown", "Down", "Up")
    ed.keys("Esc")
    assert ed.overlay is None and ed.doc.blame is not None  # back in blame mode
    ed.keys("Up", "Enter")
    assert "Add the app" in build_frame(ed, 30, 80).text()


def test_unsaved_edits_are_not_committed_yet(repo):
    make_history(repo)
    ed = open_file(repo)
    ed.keys("Down", "End")
    ed.type(" + 1")
    run_palette(ed, "blame")
    lines = build_frame(ed, 12, 80).text().split("\n")
    assert lines[2].startswith("Not committed yet      return 2 + 1")
    assert lines[1].startswith("Add the app ")
    ed.keys("Enter")
    assert ed.overlay is None and "not committed yet" in ed.message.text


def test_blame_with_line_numbers_and_long_titles(repo):
    repo.commit("A very long commit title that does not fit in the column", {"f.txt": "one\n"})
    ed = open_file(repo, "f.txt", line_numbers=True)
    run_palette(ed, "blame")
    line = build_frame(ed, 12, 80).text().split("\n")[1]
    assert line.startswith("A very long commit title that…   1 one")


def test_blame_errors(repo, tmp_path):
    make_history(repo)
    repo.write("new.txt", "untracked\n")
    ed = open_file(repo, "new.txt")
    run_palette(ed, "blame")
    assert ed.doc.blame is None and "new.txt is not tracked by git" in ed.message.text
    outside = tmp_path / "outside.txt"
    outside.write_text("x\n")
    ed = Editor(Settings(), cwd=str(tmp_path), raise_errors=True)
    ed.open_file(str(outside))
    run_palette(ed, "blame")
    assert ed.doc.blame is None and "not inside a git repository" in ed.message.text


def test_parse_porcelain():
    sha = "a" * 40
    out = f"{sha} 1 1 2\nauthor X\nsummary First\nfilename f\n\tone\n{sha} 2 2\nfilename f\n\ttwo\n"
    assert blame.parse_porcelain(out, 3) == ([sha, sha, None], {sha: "First"})
