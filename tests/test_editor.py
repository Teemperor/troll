import os

from diffedit.prompt import Choice, HelpScreen, Picker, Prompt

from conftest import editor_with


def run(ed, line):
    """Run a command line through the palette, like a user would."""
    ed.keys("C-t")
    ed.type(line)
    ed.keys("Enter")


def test_type_and_save_with_ctrl_s(editor, tmp_path):
    doc = editor_with(editor, "", path="new.txt")
    editor.type("hello\nworld")
    editor.keys("C-s")
    assert (tmp_path / "new.txt").read_text() == "hello\nworld\n"
    assert "Wrote 2 lines" in editor.message.text
    assert not doc.modified


def test_write_out_prompts_for_name(editor, tmp_path):
    editor.new_doc()
    editor.type("x")
    editor.keys("C-o")
    assert isinstance(editor.prompt, Prompt)
    editor.type("out.txt")
    editor.keys("Enter")
    assert (tmp_path / "out.txt").read_text() == "x\n"
    assert editor.doc.path.endswith("out.txt")


def test_write_out_asks_before_overwriting(editor, tmp_path):
    (tmp_path / "exists.txt").write_text("keep")
    editor.new_doc()
    editor.type("new")
    editor.keys("C-o")
    editor.type("exists.txt")
    editor.keys("Enter")
    assert isinstance(editor.prompt, Choice)
    editor.keys("n")
    assert (tmp_path / "exists.txt").read_text() == "keep"
    editor.keys("C-o")
    editor.type("exists.txt")
    editor.keys("Enter", "y")
    assert (tmp_path / "exists.txt").read_text() == "new\n"


def test_exit_unmodified_quits(editor):
    editor_with(editor, "x")
    editor.keys("C-x")
    assert editor.quit_requested


def test_exit_modified_asks_to_save(editor, tmp_path):
    editor_with(editor, "x", path="f.txt")
    editor.type("y")
    editor.keys("C-x")
    assert isinstance(editor.prompt, Choice)
    editor.keys("C-c")
    assert not editor.quit_requested and editor.message.text == "Cancelled"
    editor.keys("C-x", "y")
    assert editor.quit_requested
    assert (tmp_path / "f.txt").read_text() == "yx"


def test_exit_modified_discard(editor, tmp_path):
    editor_with(editor, "x", path="f.txt")
    editor.type("y")
    editor.keys("C-x", "n")
    assert editor.quit_requested
    assert not (tmp_path / "f.txt").exists()


def test_exit_closes_one_of_several_buffers(editor, tmp_path):
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    editor.open_file("a.txt")
    editor.open_file("b.txt")
    assert editor.doc.text() == "b"
    editor.keys("M->")
    assert editor.doc.text() == "a"
    editor.keys("C-x")
    assert not editor.quit_requested and editor.doc.text() == "b"


def test_cut_accumulates_consecutive_lines_and_pastes(editor):
    doc = editor_with(editor, "1\n2\n3\n4")
    editor.keys("C-k", "C-k")
    assert editor.cutbuffer == "1\n2\n"
    editor.keys("Down", "C-u")
    assert doc.text() == "3\n1\n2\n4"
    editor.keys("C-k")
    assert editor.cutbuffer == "4"  # a new cut after moving starts over


def test_cut_marked_region(editor):
    doc = editor_with(editor, "hello world")
    editor.keys("M-a", "Right", "Right", "Right", "Right", "Right", "C-k")
    assert editor.cutbuffer == "hello"
    assert doc.text() == " world"
    editor.keys("End", "C-u")
    assert doc.text() == " worldhello"


def test_copy_line_and_shift_selection_copy(editor):
    doc = editor_with(editor, "ab\ncd")
    editor.keys("M-6")
    assert editor.cutbuffer == "ab\n" and doc.cursor == (1, 0)
    editor.keys("S-Right", "S-Right", "M-6")
    assert editor.cutbuffer == "cd"


def test_undo_redo_keys(editor):
    doc = editor_with(editor, "", path="x.txt")
    editor.type("abc")
    editor.keys("M-u")
    assert doc.text() == ""
    editor.keys("M-e")
    assert doc.text() == "abc"
    editor.keys("M-U")  # Alt+Shift works too
    assert doc.text() == ""


def test_search_wraps_and_repeats(editor):
    doc = editor_with(editor, "foo\nbar foo\nfoo")
    editor.keys("Down")
    editor.keys("C-w")
    editor.type("foo")
    editor.keys("Enter")
    assert doc.cursor == (1, 4)
    assert editor.highlight_match == ((1, 4), (1, 7))
    editor.keys("M-w")
    assert doc.cursor == (2, 0)
    editor.keys("M-w")
    assert doc.cursor == (0, 0) and editor.message.text == "Search Wrapped"
    editor.keys("M-q")
    assert doc.cursor == (2, 0)


def test_search_empty_reuses_last_and_not_found(editor):
    doc = editor_with(editor, "abc abc")
    editor.keys("C-w")
    editor.type("abc")
    editor.keys("Enter")
    assert doc.cursor == (0, 4)
    editor.keys("C-w", "Enter")  # empty input repeats the last search
    assert doc.cursor == (0, 0) and editor.message.text == "Search Wrapped"
    editor.keys("C-w")
    editor.type("zzz")
    editor.keys("Enter")
    assert editor.message.text == '"zzz" not found'


def test_search_prompt_toggles_case_and_regex(editor):
    doc = editor_with(editor, "Foo foo f00")
    editor.keys("C-w", "M-c")
    assert "Case Sensitive" in editor.prompt.label
    editor.type("foo")
    editor.keys("Enter")
    assert doc.cursor == (0, 4)
    editor.settings.case_sensitive = False
    editor.keys("C-w", "M-r")
    editor.type(r"f\d+")
    editor.keys("Enter")
    assert doc.cursor == (0, 8)
    editor.keys("C-w")
    editor.type("(")
    editor.keys("Enter")
    assert editor.message.kind == "error" and "Bad regex" in editor.message.text


def test_interactive_replace(editor):
    doc = editor_with(editor, "a a a")
    editor.keys("C-\\")
    editor.type("a")
    editor.keys("Enter")
    editor.type("b")
    editor.keys("Enter")
    assert isinstance(editor.prompt, Choice)
    editor.keys("y", "n", "y")
    assert doc.text() == "b a b"
    assert editor.message.text == "Replaced 2 occurrences"


def test_replace_all_and_wraparound(editor):
    doc = editor_with(editor, "x1 x2\nx3", cursor=(0, 3))
    editor.keys("C-\\")
    editor.type("x")
    editor.keys("Enter")
    editor.type("y")
    editor.keys("Enter", "a")
    assert doc.text() == "y1 y2\ny3"
    assert editor.message.text == "Replaced 3 occurrences"


def test_replace_within_selection(editor):
    doc = editor_with(editor, "a a a a")
    editor.keys("S-Right", "S-Right", "S-Right", "S-Right")
    editor.keys("C-\\")
    assert "in selection" in editor.prompt.label
    editor.type("a")
    editor.keys("Enter")
    editor.type("b")
    editor.keys("Enter", "a")
    assert doc.text() == "b b a a"


def test_replace_with_growing_text_terminates(editor):
    doc = editor_with(editor, "a a")
    editor.start_replace("a", "aa", confirm=False)
    assert doc.text() == "aa aa"


def test_goto_line_and_column(editor):
    doc = editor_with(editor, "one\ntwo\nthree\n")
    editor.keys("C-_")
    editor.type("3,2")
    editor.keys("Enter")
    assert doc.cursor == (2, 1)
    editor.keys("M-g")
    editor.type("-1")
    editor.keys("Enter")
    assert doc.cursor == (2, 0)
    editor.keys("M-g")
    editor.type("x")
    editor.keys("Enter")
    assert editor.message.kind == "error"


def test_help_screen_opens_and_closes(editor):
    editor_with(editor, "x")
    editor.keys("C-g")
    assert isinstance(editor.overlay, HelpScreen)
    assert any("EDITING A COMMIT" in line for line in editor.overlay.lines)
    editor.keys("C-v", "C-x")
    assert editor.overlay is None and not editor.quit_requested


def test_command_palette_runs_selected_command(editor):
    doc = editor_with(editor, "x = 1")
    editor.keys("C-t")
    assert isinstance(editor.overlay, Picker)
    editor.type("commen")
    assert editor.overlay.current().label == "comment"
    editor.keys("Enter")
    assert doc.text() == "# x = 1"


def test_command_palette_asks_for_arguments(editor):
    doc = editor_with(editor, "a\nb\nc")
    editor.keys("C-t")
    editor.type("goto")
    editor.keys("Enter")
    assert isinstance(editor.prompt, Prompt)
    editor.type("3")
    editor.keys("Enter")
    assert doc.cursor == (2, 0)


def test_command_lines(editor):
    doc = editor_with(editor, "b\na\nc\n")
    run(editor, "2")
    assert doc.cursor == (1, 0)
    run(editor, "set tabsize 2")
    assert doc.settings.tab_size == 2 and editor.settings.tab_size == 2
    run(editor, "sort")
    assert doc.text() == "a\nb\nc\n"
    run(editor, "s/([ab])/<\\1>/g")
    assert doc.text() == "<a>\n<b>\nc\n"
    run(editor, "s/<(.)>/$1/")
    assert doc.text() == "a\nb\nc\n"
    run(editor, "/c")
    assert doc.cursor == (2, 0)
    run(editor, "lang rust")
    assert doc.lang.name == "rust"
    run(editor, "frobnicate now")
    assert editor.message.kind == "error" and "Unknown command" in editor.message.text
    run(editor, "set tabsize banana")
    assert editor.message.kind == "error" and "not a number" in editor.message.text


def test_shell_command_inserts_and_filters(editor):
    doc = editor_with(editor, "b\na\n")
    editor.run_line("!echo hi")
    assert doc.text().startswith("hi\n")
    doc.set_cursor((1, 0))  # select the two original lines and sort them
    editor.keys("S-Down", "S-Down")
    editor.run_line("!sort")
    assert doc.text() == "hi\na\nb\n"


def test_toggles(editor):
    doc = editor_with(editor, "x")
    editor.keys("M-n")
    assert not doc.settings.line_numbers
    editor.keys("M-#")
    assert doc.settings.line_numbers
    editor.keys("M-X")
    assert not editor.settings.help_lines


def test_location_and_wordcount(editor):
    editor_with(editor, "one two\nthree", cursor=(1, 2))
    editor.keys("C-c")
    assert editor.message.text.startswith("line 2/2")
    editor.keys("M-d")
    assert "Words: 3" in editor.message.text


def test_bracketed_paste_is_inserted_verbatim(editor):
    doc = editor_with(editor, "", path="x.py")
    editor.keys("PasteStart")
    editor.type("if x:\n    y = (1\n")
    editor.keys("PasteEnd")
    assert doc.text() == "if x:\n    y = (1\n"
    editor.keys("M-u")
    assert doc.text() == ""


def test_paste_into_prompt(editor):
    editor_with(editor, "abc")
    editor.keys("C-w", "PasteStart")
    editor.type("b\nc")
    editor.keys("PasteEnd")
    assert editor.prompt.text == "b c"


def test_insert_file(editor, tmp_path):
    (tmp_path / "other.txt").write_text("inserted\n")
    doc = editor_with(editor, "x")
    editor.keys("C-r")
    editor.type("oth")
    editor.keys("Tab")
    assert editor.prompt.text == "other.txt"
    editor.keys("Enter")
    assert doc.text() == "inserted\nx"


def test_open_file_with_line_number(editor, tmp_path):
    (tmp_path / "f.txt").write_text("a\nb\nc\n")
    doc = editor.open_file("f.txt", 2, 1)
    assert doc.cursor == (1, 0)
    assert editor.message.text == "Read 3 lines"


def test_errors_are_reported_not_raised(tmp_path):
    from diffedit.editor import Editor

    ed = Editor(cwd=str(tmp_path))
    editor_with(ed, "x")
    run(ed, f"open {tmp_path}")
    assert ed.message.kind == "error" and "directory" in ed.message.text
    ed.doc.readonly = True
    ed.keys("a")
    assert ed.message.text == "File is read-only"


def test_quit_force_and_quit_asks(editor):
    editor_with(editor, "x")
    editor.type("y")
    editor.run_line("quit")
    assert isinstance(editor.prompt, Choice)
    editor.keys("C-c")
    editor.run_line("q!")
    assert editor.quit_requested


def test_unbound_key_message(editor):
    editor_with(editor, "x")
    editor.keys("F20")
    assert "Unbound key" in editor.message.text


def test_page_down_moves_cursor(editor):
    doc = editor_with(editor, "\n".join(str(i) for i in range(100)))
    editor.body_height = 10
    editor.keys("PageDown")
    assert doc.cursor[0] == 8
    editor.keys("C-y")
    assert doc.cursor[0] == 0


def test_tab_and_shift_tab_indent_selection(editor):
    doc = editor_with(editor, "a\nb", path="x.py")
    editor.keys("S-Down", "S-Right", "Tab")
    assert doc.text() == "    a\n    b"
    editor.keys("S-Tab")
    assert doc.text() == "a\nb"
    assert os.path.basename(doc.path) == "x.py"
