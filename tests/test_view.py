from diffedit.prompt import Picker
from diffedit.view import build_frame, fit, help_bar_rows

from conftest import editor_with


def styles_at(frame, row, text):
    """Style of the segment containing `text` on a frame row."""
    for seg_text, fg, bg in frame.rows[row]:
        if text in seg_text:
            return fg, bg
    raise AssertionError(f"{text!r} not on row {row}: {frame.rows[row]}")


def test_frame_layout_and_cursor(editor):
    editor_with(editor, "def f():\n    return 1\n", cursor=(1, 4))
    f = build_frame(editor, 12, 60)
    lines = f.text().split("\n")
    assert len(lines) == 12 and f.width == 60
    assert "diffedit" in lines[0] and "t.py" in lines[0]
    assert lines[1].startswith("  1 def f():")
    assert lines[2].startswith("  2     return 1")
    assert "^G Help" in lines[-2] and "^X Exit" in lines[-1]
    assert f.cursor == (2, 4 + 4)  # row 1 of body, gutter of 4 + 4 columns


def test_syntax_colors_reach_the_frame(editor):
    editor_with(editor, "def f():  # hi\n")
    f = build_frame(editor, 10, 60)
    assert styles_at(f, 1, "def")[0] == "keyword"
    assert styles_at(f, 1, "# hi")[0] == "comment"


def test_modified_flag_and_messages(editor):
    editor_with(editor, "x")
    editor.type("y")
    f = build_frame(editor, 10, 60)
    assert "Modified" in f.text().split("\n")[0]
    editor.error("Boom")
    f = build_frame(editor, 10, 60)
    assert "[ Boom ]" in f.text().split("\n")[-3]
    assert styles_at(f, 7, "Boom")[0] == "status.error"


def test_prompt_row_and_cursor(editor):
    editor_with(editor, "x")
    editor.keys("C-w")
    editor.type("abc")
    f = build_frame(editor, 10, 60)
    status = f.text().split("\n")[7]
    assert status.startswith("Search: abc")
    assert f.cursor == (7, len("Search: abc"))
    assert "M-C Case Sens" in f.text()


def test_choice_row(editor):
    editor_with(editor, "x")
    editor.type("y")
    editor.keys("C-x")
    f = build_frame(editor, 10, 80)
    assert "Save modified buffer?" in f.text()
    assert " Y Yes" in f.text() and " N No" in f.text()


def test_selection_and_search_highlight(editor):
    editor_with(editor, "hello world")
    editor.keys("S-Right", "S-Right")
    f = build_frame(editor, 10, 60)
    assert styles_at(f, 1, "he")[1] == "selection"
    editor.keys("Home", "C-w")
    editor.type("world")
    editor.keys("Enter")
    f = build_frame(editor, 10, 60)
    assert styles_at(f, 1, "world")[1] == "match"


def test_matching_bracket_highlight(editor):
    editor_with(editor, "f(x)", path="x.txt")
    f = build_frame(editor, 10, 60)
    assert all(bg is None for _t, _f, bg in f.rows[1])
    editor.doc.set_cursor((0, 1))
    f = build_frame(editor, 10, 60)
    brackets = [t for t, _f, bg in f.rows[1] if bg == "bracket"]
    assert brackets == ["(", ")"]


def test_tabs_and_control_chars_are_expanded(editor):
    editor_with(editor, "\tx\x01", path="x.txt", tab_size=4)
    f = build_frame(editor, 10, 60)
    assert f.text().split("\n")[1] == "  1     x^A"


def test_show_whitespace(editor):
    editor_with(editor, "\tx  ", path="x.txt", tab_size=4, show_whitespace=True)
    f = build_frame(editor, 10, 60)
    assert f.text().split("\n")[1] == "  1 ›   x··"


def test_horizontal_scroll_follows_cursor(editor):
    doc = editor_with(editor, "x" * 200, path="x.txt")
    doc.move_end()
    f = build_frame(editor, 10, 40)
    assert doc.scroll_col > 0
    row, col = f.cursor
    assert row == 1 and 4 <= col < 40


def test_vertical_scroll_and_center(editor):
    doc = editor_with(editor, "\n".join(f"l{i}" for i in range(100)), path="x.txt")
    doc.goto(80, 0)
    build_frame(editor, 12, 40)
    body = editor.body_height
    assert doc.scroll_row <= 80 < doc.scroll_row + body
    assert abs((80 - doc.scroll_row) - body // 2) <= 1


def test_wide_characters_in_frame(editor):
    editor_with(editor, "漢字x", path="x.txt")
    f = build_frame(editor, 10, 20)
    assert f.text().split("\n")[1] == "  1 漢字x"


def test_palette_overlay(editor):
    editor_with(editor, "x")
    editor.keys("C-t")
    editor.type("just")
    f = build_frame(editor, 20, 80)
    lines = f.text().split("\n")
    assert lines[1].startswith(" › just")
    assert "justify" in lines[2] and "^J" in lines[2]
    assert f.cursor == (1, 3 + 4)
    assert isinstance(editor.overlay, Picker)


def test_help_screen_frame(editor):
    editor_with(editor, "x")
    editor.keys("C-g")
    f = build_frame(editor, 20, 100)
    assert "nano-compatible" in f.text()
    assert "^X Close" in f.text()


def test_help_bar_drops_columns_when_narrow():
    items = [(f"^{c}", f"Label{c}") for c in "ABCDEFGH"]
    rows = help_bar_rows(items, 40)
    top = "".join(t for t, _f, _b in rows[0])
    assert top.startswith("^A LabelA") and "^B" in top and "^E" not in top


def test_tiny_terminal_does_not_crash(editor):
    editor_with(editor, "hello\nworld")
    for h, w in [(1, 1), (2, 5), (3, 10), (5, 3)]:
        f = build_frame(editor, h, w)
        assert len(f.rows) == h


def test_fit():
    assert fit("abc", 5) == "abc  "
    assert fit("abcdef", 3) == "abc"
    assert fit("漢字", 3) == "漢 "


def test_picker_ranking_prefers_label_then_aliases():
    from diffedit.prompt import PickerItem

    items = [PickerItem("justify-all"), PickerItem("justify", search_text="justify reflow wrap"), PickerItem("goto", search_text="goto line g")]
    p = Picker("t", items, lambda *a: None)
    p.field.set("justify")
    assert [i.label for i in p.filtered()][:2] == ["justify", "justify-all"]
    p.field.set("reflow")
    assert p.filtered()[0].label == "justify"
    p.field.set("line")
    assert p.filtered()[0].label == "goto"


def test_cursor_sits_on_the_character_it_points_at():
    from diffedit.diffmodel import DiffState
    from diffedit.document import Document
    from diffedit.editor import Editor

    for numbers in (True, False):
        for diff in (False, True):
            doc = Document("abcdef\n")
            doc.settings.line_numbers = numbers
            if diff:
                doc.diff = DiffState(["x", ""], ["abcdef", ""])
            ed = Editor()
            ed.add_doc(doc)
            doc.set_cursor((0, 2))
            f = build_frame(ed, 12, 60)
            y, x = f.cursor
            assert f.text().split("\n")[y][x] == "c", (numbers, diff)
