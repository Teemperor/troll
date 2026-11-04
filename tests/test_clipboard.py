import base64
import os

from conftest import editor_with
from test_commit_mode import make_history, open_editor

from troll import clipboard
from troll.prompt import ShowText
from troll.view import build_frame


def run(ed, line):
    ed.keys("C-t")
    ed.type(line)
    ed.keys("Enter")


def fake_clipboard(ed):
    copied = []
    ed.copy_to_clipboard = lambda text: copied.append(text) or "pbcopy"
    return copied


def test_copy_commands(editor):
    editor_with(editor, "a\nbcdef\n", path="sub/x.py", cursor=(1, 3))
    copied = fake_clipboard(editor)
    path = os.path.join(editor.cwd, "sub", "x.py")
    for line, expected in [("copy-path", path), ("copy-file-name", "x.py"), ("copy-path-line", path + ":2"),
                           ("copy-path-line-col", path + ":2:4")]:
        run(editor, line)
        assert copied[-1] == expected
        assert isinstance(editor.prompt, ShowText)
        assert editor.terminal_writes == []
        editor.keys("Enter")
        assert editor.prompt is None


def test_copied_text_is_shown_on_the_status_line(editor):
    editor_with(editor, "hello\n", path="x.py")
    fake_clipboard(editor)
    run(editor, "copy-file-name")
    text = build_frame(editor, 10, 60).text()
    assert "Copied file name: x.py" in text
    assert "Close" in text
    editor.keys("a")  # read-only: typing doesn't change it or the buffer
    assert editor.prompt.text == "x.py"
    assert editor.doc.lines[0] == "hello"
    editor.keys("Esc")
    assert editor.prompt is None


def test_mouse_reporting_is_off_while_the_text_is_shown(editor):
    editor_with(editor, "hello\n", path="x.py")
    fake_clipboard(editor)
    editor.settings.mouse = True
    assert editor.mouse_enabled
    run(editor, "copy-path")
    assert not editor.mouse_enabled
    editor.keys("Enter")
    assert editor.mouse_enabled


def test_without_a_clipboard_tool_it_asks_the_terminal(editor, monkeypatch):
    monkeypatch.delenv("TMUX", raising=False)
    editor_with(editor, "hello\n", path="x.py")
    run(editor, "copy-file-name")
    assert editor.terminal_writes == ["\x1b]52;c;" + base64.b64encode(b"x.py").decode() + "\x07"]
    assert editor.prompt.label == "File name"


def test_buffer_without_a_file_name(editor):
    editor.raise_errors = False
    editor.run("new", "")
    run(editor, "copy-path")
    assert editor.prompt is None
    assert "no file name" in editor.message.text


def test_commit_mode_uses_the_work_tree_path(repo):
    target = make_history(repo)
    ed = open_editor(repo, target)
    copied = fake_clipboard(ed)
    ed.commit.open(next(i for i, e in enumerate(ed.commit.entries) if e.label == "src/app.py"))
    run(ed, "copy-path")
    assert copied == [os.path.join(repo.path, "src/app.py")]


def test_copy_uses_the_first_working_tool(monkeypatch, tmp_path):
    out = tmp_path / "out"
    monkeypatch.setattr(clipboard, "tools", lambda: [["false"], ["sh", "-c", f"cat > {out}"]])
    assert clipboard.copy("päth") == "sh"
    assert out.read_text() == "päth"
