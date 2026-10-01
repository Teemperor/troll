import os

from troll import languages
from troll.document import Document, ReadOnlyError

import pytest

from conftest import make_doc


def type_text(doc, text):
    for ch in text:
        if ch == "\n":
            doc.newline()
        else:
            doc.type_char(ch)


# -------------------------------------------------------------------- typing


def test_typing_is_one_undo_step_per_word_run():
    doc = make_doc("", path="x.txt")
    type_text(doc, "hello world")
    assert doc.text() == "hello world"
    doc.undo()
    assert doc.text() == "hello "
    doc.undo()
    assert doc.text() == "hello"
    doc.undo()
    assert doc.text() == ""


def test_cursor_move_breaks_undo_group():
    doc = make_doc("", path="x.txt")
    type_text(doc, "ab")
    doc.move_left()
    type_text(doc, "X")
    assert doc.text() == "aXb"
    doc.undo()
    assert doc.text() == "ab"


def test_auto_pair_and_skip_closer():
    doc = make_doc("", path="x.py")
    type_text(doc, "f(x")
    assert doc.text() == "f(x)"
    type_text(doc, ")")
    assert doc.text() == "f(x)" and doc.cursor == (0, 4)


def test_no_auto_pair_in_comments():
    doc = make_doc("# ", path="x.py", cursor=(0, 2))
    type_text(doc, "don't (x")
    assert doc.text() == "# don't (x"


def test_backspace_removes_empty_pair():
    doc = make_doc("", path="x.py")
    type_text(doc, "[")
    assert doc.text() == "[]"
    doc.backspace()
    assert doc.text() == ""


def test_smart_backspace_in_indentation():
    doc = make_doc("        x", path="x.py", cursor=(0, 8), tab_size=4)
    doc.backspace()
    assert doc.text() == "    x"
    doc.backspace()
    assert doc.text() == "x"


def test_backspace_joins_lines():
    doc = make_doc("ab\ncd", cursor=(1, 0))
    doc.backspace()
    assert doc.text() == "abcd" and doc.cursor == (0, 2)


def test_delete_forward_joins_lines():
    doc = make_doc("ab\ncd", cursor=(0, 2))
    doc.delete_forward()
    assert doc.text() == "abcd"


def test_newline_python_block_and_electric_else():
    doc = make_doc("", path="x.py")
    type_text(doc, "if x:\npass\nelse:\ny")
    assert doc.text() == "if x:\n    pass\nelse:\n    y"


def test_newline_c_braces_with_auto_pair():
    doc = make_doc("", path="x.c")
    type_text(doc, "int f() {\nreturn 1;")
    assert doc.text() == "int f() {\n    return 1;\n}"


def test_typed_closer_is_dedented_without_auto_pair():
    doc = make_doc("", path="x.c", auto_pair=False)
    type_text(doc, "int f() {\nreturn 1;\n}")
    assert doc.text() == "int f() {\n    return 1;\n}"


def test_newline_between_braces_opens_block():
    doc = make_doc("", path="x.c")
    type_text(doc, "{")
    assert doc.text() == "{}"
    doc.newline()
    assert doc.lines == ["{", "    ", "}"] and doc.cursor == (1, 4)


def test_comment_continuation_uses_highlighter():
    doc = make_doc('x = "# not"', path="x.py", cursor=(0, 11))
    doc.newline()
    assert doc.lines == ['x = "# not"', ""]
    doc = make_doc("    # a comment", path="x.py", cursor=(0, 15))
    doc.newline()
    type_text(doc, "more")
    doc.newline()
    doc.newline()  # empty continuation ends the comment
    assert doc.lines == ["    # a comment", "    # more", "    "]


def test_trailing_comment_colon_does_not_indent():
    doc = make_doc("x = 1  # like this:", path="x.py", cursor=(0, 19))
    doc.newline()
    assert doc.lines[1] == ""


def test_newline_inside_multiline_string_keeps_indent_only():
    doc = make_doc('    s = """abc', path="x.py", cursor=(0, 14))
    doc.newline()
    assert doc.lines == ['    s = """abc', "    "]


def test_tab_inserts_spaces_to_next_stop():
    doc = make_doc("ab", path="x.py", cursor=(0, 2))
    doc.tab()
    assert doc.text() == "ab  "
    doc = make_doc("", path="Makefile")
    doc.tab()
    assert doc.text() == "\t"


def test_shift_selection_typing_replaces_and_wraps():
    doc = make_doc("hello world", path="x.py")
    for _ in range(5):
        doc.move_right(select=True)
    type_text(doc, "(")
    assert doc.text() == "(hello) world"
    doc = make_doc("hello world", path="x.txt")
    for _ in range(5):
        doc.move_right(select=True)
    doc.type_char("J")
    assert doc.text() == "J world"


def test_mark_selection_is_kept_while_moving():
    doc = make_doc("abc", path="x.txt")
    doc.set_mark()
    doc.move_right()
    doc.move_right()
    assert doc.selection() == ((0, 0), (0, 2))


# ------------------------------------------------------------------ movement


def test_vertical_movement_keeps_goal_column():
    doc = make_doc("long line\nx\nanother line")
    doc.set_cursor((0, 7))
    doc.move_down()
    assert doc.cursor == (1, 1)
    doc.move_down()
    assert doc.cursor == (2, 7)


def test_vertical_movement_with_tabs_uses_display_columns():
    doc = make_doc("\tx\n12345678", tab_size=8)
    doc.set_cursor((0, 1))
    doc.move_down()
    assert doc.cursor == (1, 8)


def test_up_on_first_line_goes_home_like_nano():
    doc = make_doc("abc\ndef", cursor=(0, 2))
    doc.move_up()
    assert doc.cursor == (0, 0)
    doc.set_cursor((1, 1))
    doc.move_down()
    assert doc.cursor == (1, 3)


def test_smart_home():
    doc = make_doc("    abc", cursor=(0, 6))
    doc.move_home()
    assert doc.cursor == (0, 4)
    doc.move_home()
    assert doc.cursor == (0, 0)


def test_word_movement_and_deletion():
    doc = make_doc("foo bar baz", cursor=(0, 0))
    doc.move_word_right()
    assert doc.cursor == (0, 4)
    doc.delete_word_forward()
    assert doc.text() == "foo baz"
    doc.move_end()
    doc.delete_word_back()
    assert doc.text() == "foo "


def test_matching_bracket_jump():
    doc = make_doc("f(a, (b))", cursor=(0, 1))
    assert doc.goto_matching_bracket()
    assert doc.cursor == (0, 8)


# --------------------------------------------------------------- line ops


def test_cut_line_and_last_line():
    doc = make_doc("a\nb\nc", cursor=(1, 0))
    assert doc.cut_line() == "b\n"
    assert doc.text() == "a\nc"
    doc.set_cursor((1, 0))
    assert doc.cut_line() == "c"
    assert doc.lines == ["a", ""]


def test_indent_unindent_selection():
    doc = make_doc("a\nb\nc", path="x.py")
    doc.set_mark()
    doc.set_cursor((1, 1))
    doc.indent_selection()
    assert doc.text() == "    a\n    b\nc"
    doc.unindent_selection()
    assert doc.text() == "a\nb\nc"


def test_toggle_comment_on_line_and_undo():
    doc = make_doc("x = 1", path="x.py")
    doc.toggle_comment()
    assert doc.text() == "# x = 1"
    doc.toggle_comment()
    assert doc.text() == "x = 1"
    doc.undo()
    assert doc.text() == "# x = 1"


def test_justify_comment_in_document():
    doc = make_doc("# aaa bbb\n# ccc\nx = 1", path="x.py", fill_width=80)
    assert doc.justify()
    assert doc.text() == "# aaa bbb ccc\nx = 1"
    assert doc.cursor == (1, 0)
    doc.undo()
    assert doc.text() == "# aaa bbb\n# ccc\nx = 1"


def test_justify_selection_only_touches_selected_lines():
    doc = make_doc("a b\nc\n\nd\ne", path="x.txt")
    doc.set_mark()
    doc.set_cursor((1, 1))
    doc.justify()
    assert doc.text() == "a b c\n\nd\ne"


def test_duplicate_and_move_lines():
    doc = make_doc("a\nb\nc", cursor=(0, 0))
    doc.duplicate_line()
    assert doc.text() == "a\na\nb\nc" and doc.cursor == (1, 0)
    doc.move_lines(1)
    assert doc.text() == "a\nb\na\nc" and doc.cursor == (2, 0)
    doc.set_cursor((0, 0))
    assert not doc.move_lines(-1)


def test_join_lines_merges_comment_continuations():
    doc = make_doc("# one\n# two\nx", path="x.py")
    doc.join_lines()
    assert doc.text() == "# one two\nx"


def test_transform_selection_upper():
    doc = make_doc("abc def")
    doc.set_mark()
    doc.set_cursor((0, 3))
    doc.transform_selection(str.upper)
    assert doc.text() == "ABC def"


def test_readonly_document_refuses_edits():
    doc = make_doc("x")
    doc.readonly = True
    with pytest.raises(ReadOnlyError):
        doc.type_char("a")


# -------------------------------------------------------------- save/load


def test_save_trims_only_edited_lines(tmp_path):
    path = tmp_path / "f.py"
    path.write_bytes(b"keep   \nx = 1\n")
    doc = Document.from_file(str(path))
    doc.set_cursor((1, 5))
    type_text(doc, "   ")
    doc.save()
    assert path.read_bytes() == b"keep   \nx = 1\n"
    assert not doc.modified


def test_save_adds_final_newline_only_if_file_had_one(tmp_path):
    p1 = tmp_path / "a.txt"
    p1.write_bytes(b"no newline")
    doc = Document.from_file(str(p1))
    doc.move_end()
    doc.type_char("!")
    doc.save()
    assert p1.read_bytes() == b"no newline!"
    p2 = tmp_path / "new.txt"
    doc = Document.from_file(str(p2))
    type_text(doc, "hi")
    doc.save()
    assert p2.read_bytes() == b"hi\n"


def test_crlf_roundtrip(tmp_path):
    p = tmp_path / "dos.txt"
    p.write_bytes(b"a\r\nb\r\n")
    doc = Document.from_file(str(p))
    assert doc.lines == ["a", "b", ""] and doc.eol == "\r\n"
    doc.set_cursor((0, 1))
    doc.type_char("x")
    doc.save()
    assert p.read_bytes() == b"ax\r\nb\r\n"


def test_invalid_utf8_roundtrips(tmp_path):
    p = tmp_path / "bin.txt"
    p.write_bytes(b"caf\xe9\n")
    doc = Document.from_file(str(p))
    doc.save()
    assert p.read_bytes() == b"caf\xe9\n"


def test_language_and_indent_detection(tmp_path):
    p = tmp_path / "x.js"
    p.write_text("function f() {\n  return 1;\n}\n")
    doc = Document.from_file(str(p))
    assert doc.lang.name == "javascript"
    assert doc.settings.tab_size == 2 and doc.settings.expand_tabs
    g = tmp_path / "x.go"
    g.write_text("func f() {\n\treturn\n}\n")
    assert not Document.from_file(str(g)).settings.expand_tabs


def test_save_as_detects_language(tmp_path):
    doc = Document("x = 1\n")
    assert doc.lang is languages.TEXT
    doc.save(str(tmp_path / "script.py"))
    assert doc.lang.name == "python"
    assert os.path.exists(tmp_path / "script.py")
