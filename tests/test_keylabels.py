import pytest

from troll import keylabels
from troll.keylabels import key_label, localize
from troll.view import build_frame

from conftest import editor_with


@pytest.mark.parametrize("key, mac, pc, nano", [
    ("C-x", "⌃ X", "Ctrl+X", "^X"),
    ("M-z", "⌥ Z", "Alt+Z", "M-Z"),
    ("C-\\", "⌃ \\", "Ctrl+\\", "^\\"),
    ("C-Space", "⌃ Space", "Ctrl+Space", "^Space"),
    ("M-S-Up", "⌥ ⇧ ↑", "Alt+Shift+↑", "M-Sh-↑"),
    ("S-Tab", "⇧ Tab", "Shift+Tab", "Sh-Tab"),
    ("M--", "⌥ -", "Alt+-", "M--"),
    ("F7", "F7", "F7", "F7"),
])
def test_key_label(key, mac, pc, nano):
    assert (key_label(key, "mac"), key_label(key, "pc"), key_label(key, "nano")) == (mac, pc, nano)


def test_localize_prose():
    text = "^X quits, M-Z folds, M-Down/M-Up jump, M-+/M-- context, ^Y/^V page, M-PgDn, M-S-Up"
    assert localize(text, "pc") == ("Ctrl+X quits, Alt+Z folds, Alt+↓/Alt+↑ jump, Alt++/Alt+- context, "
                                    "Ctrl+Y/Ctrl+V page, Alt+PgDn, Alt+Shift+↑")
    assert localize(text, "mac").startswith("⌃ X quits, ⌥ Z folds, ⌥ ↓/⌥ ↑ jump, ⌥ +/⌥ - context")
    assert localize(text, "nano") == text
    assert localize("set tabsize 2, see M-x-ray?", "pc") == "set tabsize 2, see Alt+X-ray?"
    assert localize("the HEAD^ and HEAD~2 ^", "pc") == "the HEAD^ and HEAD~2 ^"


def test_help_bar_uses_host_labels(editor, monkeypatch):
    editor_with(editor, "x\n")
    monkeypatch.setattr(keylabels, "STYLE", "pc")
    lines = build_frame(editor, 12, 100).text().split("\n")
    assert "Ctrl+G Help" in lines[-2] and "Ctrl+X Exit" in lines[-1] and "^X" not in lines[-1]
    monkeypatch.setattr(keylabels, "STYLE", "mac")
    lines = build_frame(editor, 12, 100).text().split("\n")
    assert "⌃ X Exit" in lines[-1] and "⌃ O Write Out" in lines[-2]


def test_narrow_help_bar_drops_columns_instead_of_cutting(editor, monkeypatch):
    editor_with(editor, "x\n")
    monkeypatch.setattr(keylabels, "STYLE", "pc")
    lines = build_frame(editor, 12, 80).text().split("\n")
    assert "Ctrl+R Read File " in lines[-1] and "Ctrl+J" not in lines[-1]


def test_help_screen_and_messages(editor, monkeypatch):
    monkeypatch.setattr(keylabels, "STYLE", "pc")
    editor_with(editor, "x\n")
    editor.keys("C-g")
    text = build_frame(editor, 40, 120).text()
    assert "Press Ctrl+T (or Alt+X)" in text and "Alt+key can also be typed as Esc" in text
    assert "M-" not in text and "^T" not in text


def test_default_style_follows_host(monkeypatch):
    import importlib
    import sys
    monkeypatch.setattr(sys, "platform", "darwin")
    assert importlib.reload(keylabels).STYLE == "mac"
    monkeypatch.setattr(sys, "platform", "linux")
    assert importlib.reload(keylabels).STYLE == "pc"
