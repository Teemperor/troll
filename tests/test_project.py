"""IDE mode: the directory overview, file search and find-file, driven like a user."""

import os

from troll import cli, project
from troll.editor import Editor
from troll.prompt import Picker
from troll.search import compile_query
from troll.settings import Settings
from troll.view import build_frame


def make_tree(root):
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("import os\n\ndef main():\n    return helper()\n")
    (root / "src" / "util.py").write_text("def helper():\n    return 42\n")
    (root / "README.md").write_text("# Project\nCall helper() to start.\n")
    (root / "data.bin").write_bytes(b"helper\0\1\2")
    (root / ".git").mkdir()


def ide(root) -> Editor:
    ed = Editor(Settings(), cwd=str(root), raise_errors=True)
    ed.open_project(".")
    return ed


def test_list_dir_puts_directories_first_and_hides_git(tmp_path):
    make_tree(tmp_path)
    names = [(e.name, e.is_dir) for e in project.list_dir(str(tmp_path))]
    assert names == [("src", True), ("data.bin", False), ("README.md", False)]


def test_search_files_skips_binary_files(tmp_path):
    make_tree(tmp_path)
    files = project.list_files(str(tmp_path))
    assert "src/app.py" in files and not any(f.startswith(".git") for f in files)
    hits = project.search_files(str(tmp_path), files, compile_query("helper", False, False))
    assert sorted((h.path, h.row, h.col) for h in hits) == [
        ("README.md", 1, 5), ("src/app.py", 3, 11), ("src/util.py", 0, 4)]


def test_cli_opens_a_directory_in_ide_mode(tmp_path):
    make_tree(tmp_path)
    args = cli.build_parser().parse_args(["-I", "-C", str(tmp_path), "src"])
    ed = cli.setup_editor(args)
    assert ed.in_browser and not ed.docs
    assert ed.cwd == str(tmp_path / "src")
    text = build_frame(ed, 20, 80).text()
    assert "app.py" in text and "util.py" in text


def test_browse_open_file_and_close_returns_to_overview(tmp_path):
    make_tree(tmp_path)
    ed = ide(tmp_path)
    frame = build_frame(ed, 20, 80)
    assert "▸ src/" in frame.text() and frame.cursor[0] == 2  # the filter line
    ed.keys("Enter")  # into src/
    assert ed.browser.rel_dir == "src"
    ed.keys("Down", "Enter")
    assert not ed.in_browser and ed.doc.name == os.path.join("src", "util.py")
    assert "def helper" in build_frame(ed, 20, 80).text()
    ed.keys("C-x")
    assert ed.in_browser and not ed.docs and not ed.quit_requested
    ed.keys("Left")
    assert ed.browser.rel_dir == "" and ed.browser.current().name == "src"
    ed.keys("C-x")
    assert ed.quit_requested


def test_typing_filters_the_listing(tmp_path):
    make_tree(tmp_path)
    ed = ide(tmp_path)
    ed.type("read")
    assert [e.name for e in ed.browser.items()] == ["README.md"]
    assert "› read" in build_frame(ed, 20, 80).text()
    ed.keys("Enter")
    assert ed.doc.name == "README.md"


def test_browse_command_shows_overview_and_esc_returns(tmp_path):
    make_tree(tmp_path)
    ed = ide(tmp_path)
    ed.type("readme")
    ed.keys("Enter")
    ed.keys("M-o")
    assert ed.in_browser and ed.browser.current().name == "README.md"
    assert "README.md  open" in build_frame(ed, 20, 80).text()
    ed.keys("Esc")
    assert not ed.in_browser and ed.doc.name == "README.md"


def test_search_in_files_lists_hits_and_opens_them(tmp_path):
    make_tree(tmp_path)
    ed = ide(tmp_path)
    ed.keys("C-w")
    ed.type("helper")
    ed.keys("Enter")
    assert ed.in_browser and len(ed.browser.hits) == 3
    text = build_frame(ed, 20, 100).text()
    assert 'Search results for "helper"' in text and "src/app.py:4" in text and "return helper()" in text
    ed.type("util")
    ed.keys("Enter")
    assert ed.doc.name == os.path.join("src", "util.py") and ed.doc.cursor == (0, 4)
    assert ed.last_search == "helper"
    ed.keys("M-o")
    assert ed.browser.hits is not None  # the results are still there
    ed.keys("Esc", "Esc")
    assert ed.browser.hits is None


def test_search_from_a_subdirectory_keeps_root_relative_paths(tmp_path):
    make_tree(tmp_path)
    ed = ide(tmp_path)
    ed.keys("Enter")  # src/
    ed.keys("C-w")
    ed.type("helper")
    ed.keys("Enter")
    assert sorted(h.path for h in ed.browser.hits) == ["src/app.py", "src/util.py"]


def test_find_file_picker(tmp_path):
    make_tree(tmp_path)
    ed = ide(tmp_path)
    ed.keys("C-f")
    assert isinstance(ed.overlay, Picker)
    ed.type("utilpy")
    ed.keys("Enter")
    assert ed.doc.name == os.path.join("src", "util.py") and not ed.in_browser


def test_quit_from_overview_asks_about_modified_buffers(tmp_path):
    make_tree(tmp_path)
    ed = ide(tmp_path)
    ed.type("readme")
    ed.keys("Enter")
    ed.type("x")
    ed.keys("M-o", "C-x")
    assert not ed.in_browser and ed.prompt is not None
    ed.keys("n")
    assert ed.in_browser and not ed.docs


def test_context_ranges_share_lines_between_close_hits():
    lines = [f"l{i}" for i in range(20)]
    hits = [project.Hit("a", r, 0, 1, lines[r], lines) for r in (2, 4, 15)]
    assert project.context_ranges(hits, 0) == [(2, 3), (4, 5), (15, 16)]
    assert project.context_ranges(hits, 2) == [(0, 4), (4, 7), (13, 18)]
    other = project.Hit("b", 3, 0, 1, "x", ["", "", "", "x"])
    assert project.context_ranges([hits[0], other], 5) == [(0, 8), (0, 4)]


def test_search_results_context_keys(tmp_path):
    make_tree(tmp_path)
    (tmp_path / "long.py").write_text("".join(f"line {i}\n" for i in range(30)) + "    needle here\n"
                                      + "".join(f"after {i}\n" for i in range(5)))
    ed = ide(tmp_path)
    ed.keys("C-w")
    ed.type("needle")
    ed.keys("Enter")
    text = build_frame(ed, 20, 80).text()
    assert "long.py:31  needle here" in text and "line 29" not in text
    ed.keys("M-+", "M-=")
    assert ed.settings.search_context == 2 and "2 lines of context" in ed.message.text
    text = build_frame(ed, 20, 80).text()
    lines = [l.rstrip() for l in text.split("\n")]
    i = next(k for k, l in enumerate(lines) if "needle here" in l)
    assert lines[i - 2].endswith("29  line 28") and lines[i + 2].endswith("33  after 1")
    assert "    needle" in lines[i]  # relative indentation is kept
    ed.keys("M--", "M--", "M--")
    assert ed.settings.search_context == 0


def test_context_keeps_the_selected_hit_visible(tmp_path):
    (tmp_path / "f.txt").write_text("".join(f"hit {i}\n" + "x\n" * 9 for i in range(10)))
    ed = ide(tmp_path)
    ed.settings.search_context = 4
    ed.keys("C-w")
    ed.type("hit")
    ed.keys("Enter")
    for _ in range(7):
        ed.keys("Down")
    text = build_frame(ed, 20, 80).text()
    assert "▸ f.txt:71  hit 7" in text
