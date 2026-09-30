"""The display, editing and saving options borrowed from nano/vim/VS Code."""

from diffedit import cli, config
from diffedit.editor import Editor
from diffedit.settings import Settings
from diffedit.textutil import row_of, wrap_starts
from diffedit.view import build_frame

from conftest import editor_with


def run(ed, line):
    """Run a command line through the palette, like a user would."""
    ed.keys("C-t")
    ed.type(line)
    ed.keys("Enter")


def bg_of(frame, row, text):
    for seg_text, _fg, bg in frame.rows[row]:
        if text in seg_text:
            return bg
    raise AssertionError(f"{text!r} not on row {row}: {frame.rows[row]}")


def body(frame):
    return frame.text().split("\n")[1:]


# ---------------------------------------------------------------- soft wrap


def test_wrap_starts_breaks_after_blanks():
    assert wrap_starts("hello world foo bar", 8, 4) == [0, 6, 12]
    assert wrap_starts("abcdefghijkl", 5, 4) == [0, 5, 10]  # no blank: break mid-word
    assert wrap_starts("", 5, 4) == [0]
    assert wrap_starts("fits", 4, 4) == [0]
    assert row_of([0, 6, 12], 7) == 1 and row_of([0, 6, 12], 0) == 0 and row_of([0, 6, 12], 19) == 2


def test_soft_wrap_shows_long_lines_on_several_rows(editor):
    long = "word " * 10 + "end"
    editor_with(editor, f"{long}\nnext\n", cursor=(0, len(long)), soft_wrap=True, line_numbers=True)
    f = build_frame(editor, 10, 24)
    rows = body(f)
    assert rows[0].startswith("  1 word word word word")
    assert rows[1].startswith("    word")  # continuation: blank gutter
    assert rows[2].startswith("    word word end")
    assert rows[3].startswith("  2 next")
    assert f.cursor == (3, 4 + len("word word end"))


def test_soft_wrap_scrolls_until_the_cursor_row_fits(editor):
    text = "\n".join(["x " * 30] * 6) + "\n"  # every line needs 3 rows at width 24
    doc = editor_with(editor, text, soft_wrap=True, line_numbers=True)
    doc.set_cursor((4, 55))  # last row of line 5
    f = build_frame(editor, 10, 24)  # 6 body rows
    assert f.cursor is not None and 1 <= f.cursor[0] <= 6
    assert body(f)[f.cursor[0] - 1].startswith("    ")  # a continuation row
    assert doc.scroll_col == 0


# ------------------------------------------------------------ gutter & lines


def test_relative_numbers(editor):
    editor_with(editor, "a\nb\nc\nd\n", cursor=(1, 0), relative_numbers=True)
    rows = body(build_frame(editor, 10, 30))
    assert [r[:3] for r in rows[:4]] == ["  1", "  2", "  1", "  2"]


def test_cursor_line_background(editor):
    editor_with(editor, "one\ntwo\n", cursor=(1, 0), cursor_line=True)
    f = build_frame(editor, 10, 30)
    assert bg_of(f, 2, "two") == "cursorline"
    assert bg_of(f, 1, "one") is None
    editor.keys("M-a", "Right")  # a selection shows instead
    assert bg_of(build_frame(editor, 10, 30), 2, "wo") is None


def test_guide_column_on_short_and_long_lines(editor):
    editor_with(editor, "ab\nabcdefgh\n", guide_column=5, line_numbers=False)
    f = build_frame(editor, 10, 30)
    assert [s for s in f.rows[1] if s[2] == "guide"] == [(" ", "text", "guide")]
    assert "".join(s[0] for s in f.rows[1]).startswith("ab   ")
    guide = [s for s in f.rows[2] if s[2] == "guide"]
    assert guide == [("e", "text", "guide")]  # the 5th column


def test_indent_guides(editor):
    editor_with(editor, "if x:\n        y\n", indent_guides=True, tab_size=4, line_numbers=False)
    f = build_frame(editor, 10, 30)
    assert body(f)[1].startswith("│   │   y")


def test_scroll_margin_keeps_lines_below_the_cursor(editor):
    doc = editor_with(editor, "".join(f"l{i}\n" for i in range(50)), scroll_margin=3)
    build_frame(editor, 14, 40)  # 10 body rows
    for _ in range(9):
        editor.keys("Down")
        build_frame(editor, 14, 40)
    assert doc.cursor[0] == 9
    assert doc.scroll_row == 9 - 10 + 1 + 3  # three lines stay visible below
    editor.keys("C-End")
    build_frame(editor, 14, 40)
    assert doc.scroll_row == 51 - 10  # at the end of the file the margin doesn't apply


# --------------------------------------------------------------- highlights


def test_highlight_word_under_cursor(editor):
    editor_with(editor, "foo = foo + food\n", cursor=(0, 1), highlight_word=True, line_numbers=False)
    f = build_frame(editor, 10, 30)
    marked = [s[0] for s in f.rows[1] if s[2] == "occurrence"]
    assert marked == ["foo", "foo"]  # not "food"


def test_highlight_all_search_matches_until_nohl(editor):
    editor.settings.highlight_search = True
    editor_with(editor, "cat dog cat\ncat\n", line_numbers=False)
    editor.keys("C-w")
    editor.type("cat")
    editor.keys("Enter")
    editor.keys("Down")  # the highlight stays after moving on
    f = build_frame(editor, 10, 30)
    assert [s[0] for s in f.rows[1] if s[2] == "match.other"] == ["cat", "cat"]
    run(editor, "nohl")
    f = build_frame(editor, 10, 30)
    assert not [s for s in f.rows[1] if s[2] == "match.other"]
    editor.keys("M-w")  # searching again brings it back
    assert editor.search_highlight_pattern() is not None


def test_show_position_in_status_bar(editor):
    editor_with(editor, "abc\ndef\n", cursor=(1, 2))
    editor.keys("M-c")
    assert editor.settings.show_position
    status = build_frame(editor, 10, 80).text().split("\n")[-3]
    assert status.rstrip().endswith("line 2/3, col 3")


# ----------------------------------------------------------------- hard wrap


def test_hard_wrap_breaks_lines_while_typing(editor):
    doc = editor_with(editor, "", path="notes.txt", hard_wrap=True, fill_width=20)
    editor.type("the quick brown fox j")
    assert doc.lines == ["the quick brown fox", "j"]
    editor.type("umps over")
    assert doc.lines == ["the quick brown fox", "jumps over"]
    assert doc.cursor == (1, len("jumps over"))
    editor.keys("M-u", "M-u", "M-u")  # "over", " ", then "jumps" together with the break
    assert doc.lines == ["the quick brown fox "]


def test_hard_wrap_continues_comments(editor):
    doc = editor_with(editor, "", path="t.py", hard_wrap=True, fill_width=20, auto_pair=False)
    editor.type("    # aaaa bbbb cccc dddd")
    assert doc.lines == ["    # aaaa bbbb cccc", "    # dddd"]


def test_hard_wrap_leaves_long_words_alone(editor):
    doc = editor_with(editor, "", path="notes.txt", hard_wrap=True, fill_width=10)
    editor.type("abcdefghijklmnop")
    assert doc.lines == ["abcdefghijklmnop"]


# -------------------------------------------------------------------- mouse


def test_click_places_the_cursor(editor):
    doc = editor_with(editor, "first line\nsecond line\n", line_numbers=True)
    build_frame(editor, 10, 40)
    editor.handle_key("Click:2:9")  # screen row 2 = second line, x 9 = gutter 4 + 5
    assert doc.cursor == (1, 5)
    editor.handle_key("Click:9:0")  # the help bar: ignored
    assert doc.cursor == (1, 5)


def test_click_on_a_soft_wrapped_row(editor):
    doc = editor_with(editor, "aaaa bbbb cccc\n", soft_wrap=True, line_numbers=False)
    build_frame(editor, 10, 8)  # rows: "aaaa ", "bbbb ", "cccc"
    editor.handle_key("Click:2:1")
    assert doc.cursor == (0, 6)


def test_wheel_up_past_wrapped_lines(editor):
    text = "\n".join(["x " * 30] * 10) + "\n"  # 3 rows per line at width 24
    doc = editor_with(editor, text, soft_wrap=True)
    doc.set_cursor((9, 0))
    build_frame(editor, 10, 24)
    top = doc.scroll_row
    editor.handle_key("WheelUp:3:3")
    build_frame(editor, 10, 24)
    assert doc.scroll_row < top  # not pulled back down by the cursor


def test_wheel_scrolls(editor):
    doc = editor_with(editor, "".join(f"{i}\n" for i in range(100)))
    build_frame(editor, 14, 40)
    editor.handle_key("WheelDown:3:3")
    assert doc.scroll_row == 3 and doc.cursor[0] == 3


# ------------------------------------------------------------------- saving


def test_backup_keeps_the_previous_version(editor, tmp_path):
    (tmp_path / "a.txt").write_text("old\n")
    editor.settings.backup = True
    doc = editor.open_file("a.txt")
    editor.type("new ")
    editor.keys("C-s")
    assert (tmp_path / "a.txt").read_text() == "new old\n"
    assert (tmp_path / "a.txt~").read_text() == "old\n"


def test_remember_position_across_sessions(tmp_path):
    (tmp_path / "a.txt").write_text("".join(f"{i}\n" for i in range(20)))
    ed = Editor(Settings(remember_position=True), cwd=str(tmp_path), raise_errors=True)
    ed.open_file("a.txt").set_cursor((12, 1))
    ed.shutdown()
    ed2 = Editor(Settings(remember_position=True), cwd=str(tmp_path), raise_errors=True)
    assert ed2.open_file("a.txt").cursor == (12, 1)
    ed3 = Editor(Settings(remember_position=True), cwd=str(tmp_path), raise_errors=True)
    assert ed3.open_file("a.txt", line=3).cursor == (2, 0)  # an explicit line wins
    ed4 = Editor(Settings(), cwd=str(tmp_path), raise_errors=True)
    assert ed4.open_file("a.txt").cursor == (0, 0)  # off: no restoring


# ------------------------------------------------------------ settings file


def test_config_file_forms_and_errors(user_files):
    path = user_files["config"]
    path.parent.mkdir(parents=True)
    path.write_text("# comment\nset tabsize 2\nunset linenumbers\nsoft_wrap = on\nset mouse\n"
                    "guide_column 81  # trailing comment\nset bogus 1\nset tab_size many\n")
    s = Settings()
    errors = config.load_config(s)
    assert s.tab_size == 2 and not s.line_numbers and s.soft_wrap and s.mouse and s.guide_column == 81
    assert len(errors) == 2 and "line 7" in errors[0] and "bogus" in errors[0] and "line 8" in errors[1]


def test_save_settings_keeps_comments(editor, user_files):
    path = user_files["config"]
    path.parent.mkdir(parents=True)
    path.write_text("# mine\nset tabsize 8\n")
    editor_with(editor, "x")
    run(editor, "set tabsize 2")
    run(editor, "set softwrap on")
    run(editor, "save-settings")
    assert "Saved 2 settings" in editor.message.text
    assert path.read_text() == "# mine\nset tab_size 2\nset soft_wrap on\n"
    s = Settings()
    config.load_config(s)
    assert s.tab_size == 2 and s.soft_wrap


def test_cli_reads_the_config_file_before_flags(tmp_path, user_files):
    user_files["config"].parent.mkdir(parents=True)
    user_files["config"].write_text("set tabsize 3\nset softwrap on\nset nonsense 1\n")
    (tmp_path / "a.txt").write_text("x\n")
    ed = cli.setup_editor(cli.build_parser().parse_args(["-C", str(tmp_path), "-T", "5", "a.txt"]))
    assert ed.settings.soft_wrap and ed.settings.tab_size == 5
    assert ed.message.kind == "error" and "nonsense" in ed.message.text
    ed = cli.setup_editor(cli.build_parser().parse_args(["-C", str(tmp_path), "-I", "-J", "80", "a.txt"]))
    assert not ed.settings.soft_wrap and ed.settings.guide_column == 80


def test_settings_panel_saves_with_s(editor, user_files):
    editor_with(editor, "x")
    run(editor, "set cursorline on")
    run(editor, "settings")
    editor.keys("s")
    assert "set cursor_line on" in user_files["config"].read_text()
    assert editor.overlay is not None  # still open
