import pytest

from troll.settings import OPTIONS, Settings, option_names, set_option
from troll.settings_screen import SettingsScreen
from troll.view import build_frame

from conftest import editor_with


def open_panel(editor):
    editor.run_line("settings")
    assert isinstance(editor.overlay, SettingsScreen)
    return editor.overlay


def select(panel, key):
    panel.selected = [o.key for o in panel.options].index(key)


def test_every_setting_is_listed_once():
    keys = [o.key for o in OPTIONS]
    assert sorted(keys) == sorted(option_names())
    assert len(set(keys)) == len(keys)


def test_toggle_bool_applies_to_file_and_defaults(editor):
    doc = editor_with(editor, "x")
    panel = open_panel(editor)
    select(panel, "line_numbers")
    editor.keys("Enter")
    assert not doc.settings.line_numbers and not editor.settings.line_numbers
    editor.keys(" ")
    assert doc.settings.line_numbers


def test_numbers_step_and_clamp(editor):
    doc = editor_with(editor, "x", tab_size=4)
    panel = open_panel(editor)
    select(panel, "tab_size")
    editor.keys("Right", "Right")
    assert doc.settings.tab_size == 6
    for _ in range(30):
        editor.keys("Right")
    assert doc.settings.tab_size == 16  # maximum
    for _ in range(30):
        editor.keys("Left")
    assert doc.settings.tab_size == 1  # minimum


def test_type_a_number(editor):
    doc = editor_with(editor, "x")
    panel = open_panel(editor)
    select(panel, "fill_width")
    editor.keys("7", "2", "Enter")
    assert doc.settings.fill_width == 72 and panel.editing is None
    editor.keys("Enter", "C-u")  # Enter edits the current value; ^U clears the field
    editor.type("5")
    editor.keys("Enter")
    assert panel.error and "between 10 and 500" in panel.error
    assert doc.settings.fill_width == 72  # unchanged
    editor.keys("Esc")
    assert panel.editing is None and not panel.done


def test_cycle_choice_and_reset(editor):
    doc = editor_with(editor, "x")
    panel = open_panel(editor)
    select(panel, "trim_trailing")
    editor.keys("Right")
    assert doc.settings.trim_trailing == "all"
    editor.keys("Right", "Right")
    assert doc.settings.trim_trailing == "edited"
    editor.keys("Left")
    assert doc.settings.trim_trailing == "off"
    editor.keys("d")
    assert doc.settings.trim_trailing == "edited"


def test_editor_wide_options_do_not_touch_the_file(editor):
    doc = editor_with(editor, "x")
    panel = open_panel(editor)
    select(panel, "case_sensitive")
    editor.keys("Enter")
    assert editor.settings.case_sensitive
    assert not doc.settings.case_sensitive


def test_navigation_wraps_and_closes(editor):
    editor_with(editor, "x")
    panel = open_panel(editor)
    editor.keys("Up")
    assert panel.selected == len(OPTIONS) - 1
    editor.keys("Down")
    assert panel.selected == 0
    editor.keys("Esc")
    assert editor.overlay is None and not editor.quit_requested


def test_set_without_arguments_opens_panel(editor):
    editor_with(editor, "x")
    editor.run_line("set")
    assert isinstance(editor.overlay, SettingsScreen)


def test_works_without_an_open_file(editor):
    panel = open_panel(editor)
    select(panel, "tab_size")
    editor.keys("Right")
    assert editor.settings.tab_size == 5


def test_panel_frame(editor):
    editor_with(editor, "x", path="demo.py")
    panel = open_panel(editor)
    select(panel, "expand_tabs")
    editor.keys("Enter")
    f = build_frame(editor, 30, 90)
    text = f.text()
    assert "changes apply to" in text and "demo.py" in text
    assert "INDENTATION" in text and "[ ] off  (changed)" in text
    assert "Insert spaces instead of tab characters" in text  # description of the selection
    assert "Enter Change" in text and "D Default" in text
    assert f.cursor is None
    select(panel, "tab_size")
    editor.keys("8")
    f = build_frame(editor, 30, 90)
    y, x = f.cursor
    assert f.text().split("\n")[y][x - 1] == "8"


def test_selected_option_scrolls_into_view(editor):
    editor_with(editor, "x")
    panel = open_panel(editor)
    editor.keys("End")
    f = build_frame(editor, 14, 80)
    assert "Context lines" in f.text()


def test_set_option_uses_the_same_limits():
    s = Settings()
    with pytest.raises(ValueError, match="between 1 and 16"):
        set_option(s, "tabsize", "40")
    with pytest.raises(ValueError, match="one of: edited, all, off"):
        set_option(s, "trim", "sometimes")
    assert set_option(s, "fill", "100") == "fill_width = 100"
