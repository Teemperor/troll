"""Formatting of commit messages: the 72-column width, guide stripe and wrapping rules."""

from conftest import editor_with, make_doc

from diffedit import autoformat as af
from diffedit.editor import Editor
from diffedit.settings import Settings
from diffedit.view import build_frame, gutter_width

MSG = """\
Fix the frobnicator so that it no longer crashes when it is given an empty list of widgets
Some body text that is quite long and will need to be wrapped at some point because it just goes on.

- a list item that is also long enough that it needs wrapping when justified at 72
Signed-off-by: A <a@b.c>
Reviewed-by: B <b@c.d>
# Please enter the commit message for your changes. Lines starting
# with '#' will be ignored, and an empty message aborts the commit.
# ------------------------ >8 ------------------------
diff --git a/x b/x
+ a very long diff line a very long diff line a very long diff line a very long diff line
"""


def message_doc(text=MSG, **settings):
    return make_doc(text, path="/tmp/repo/.git/COMMIT_EDITMSG", **settings)


def guide_x(frame, y):
    """Screen column of the guide stripe in row y, or None."""
    x = 0
    for text, _fg, bg in frame.rows[y]:
        if bg == "guide":
            return x
        x += len(text)
    return None


def test_justify_keeps_subject_trailers_comments_and_diff():
    doc = message_doc()
    doc.justify(whole=True)
    original = MSG.split("\n")
    assert doc.lines[0] == original[0]  # the subject is never reflowed
    assert doc.lines[1:3] == [
        "Some body text that is quite long and will need to be wrapped at some",
        "point because it just goes on.",
    ]
    assert all(len(line) <= 72 for line in doc.lines[1:6])
    assert doc.lines[4:6] == ["- a list item that is also long enough that it needs wrapping when", "  justified at 72"]
    assert doc.lines[6:] == original[4:]  # trailers, '#' lines, the scissors and the diff are untouched


def test_justify_on_the_subject_does_nothing():
    doc = message_doc()
    doc.set_cursor((0, 5))
    assert not doc.justify()
    assert doc.lines == MSG.split("\n")


def test_hard_wrap_is_on_for_the_body_only():
    doc = message_doc("Subject\n\n")
    doc.set_cursor((2, 0))
    for ch in "word " * 20:
        doc.type_char(ch)
    assert [len(line.rstrip()) <= 72 for line in doc.lines[2:]] == [True, True]
    assert len(doc.lines) == 4
    doc.set_cursor((0, len(doc.lines[0])))
    for ch in " and more" * 10:
        doc.type_char(ch)
    assert len(doc.lines[0]) > 72 and len(doc.lines) == 4  # the subject isn't broken
    doc.set_cursor((3, len(doc.lines[3])))
    doc.newline()
    for ch in "# a comment line " * 6:
        doc.type_char(ch)
    assert len(doc.lines[4]) > 72  # neither are '#' lines


def test_hard_wrap_can_be_turned_off_for_messages():
    doc = message_doc("Subject\n\n", message_hard_wrap=False)
    doc.set_cursor((2, 0))
    for ch in "word " * 20:
        doc.type_char(ch)
    assert len(doc.lines) == 3


def test_subject_limit_follows_the_width():
    doc = message_doc()
    subject = doc.lines[0]
    spans = doc.highlighter.spans(0)
    assert (72, len(subject), "error") in spans
    doc.settings.message_width = 50
    assert (50, len(subject), "error") in doc.highlighter.spans(0)


def test_is_message_prose():
    lines = MSG.split("\n")
    assert not af.is_message_prose(lines, 0)
    assert af.is_message_prose(lines, 1)
    assert not af.is_message_prose(lines, 4)  # Signed-off-by
    assert not af.is_message_prose(lines, 6)  # '#' comment
    assert not af.is_message_prose(lines, 10)  # below the scissors


def test_guide_stripe_at_column_73_in_messages_only(editor):
    doc = editor_with(editor, MSG, path="COMMIT_EDITMSG")
    frame = build_frame(editor, 30, 120)
    assert guide_x(frame, 2) == gutter_width(doc) + 72  # right after the 72nd character
    editor.run_line("set msgguide 0")
    assert guide_x(build_frame(editor, 30, 120), 2) is None
    code = editor_with(editor, "x = 1\n", path="t.py")
    assert code.fill_width == 80 and code.guide_column == 0 and not code.hard_wrap  # code files: unchanged
    assert guide_x(build_frame(editor, 30, 120), 1) is None


def test_justify_width_argument_sets_the_message_width(editor):
    doc = editor_with(editor, "Subject\n\n" + "word " * 30 + "\n", path="COMMIT_EDITMSG")
    doc.set_cursor((2, 0))
    editor.run_line("justify 40")
    assert doc.settings.message_width == 40 and doc.settings.fill_width == 80
    assert all(len(line) <= 40 for line in doc.lines)


def test_message_settings_reach_the_commit_message_from_a_file(repo):
    repo.commit("base", {"a.py": "x = 1\n"})
    rev = repo.commit("Change a", {"a.py": "x = 2\n"})
    ed = Editor(Settings(), cwd=repo.path, raise_errors=True)
    ed.open_commit(rev)
    ed.open_entry(1)  # a.py
    ed.keys("C-t")
    ed.type("set msgwidth 60")
    ed.keys("Enter")
    msg = ed.commit.entries[0].doc
    assert msg.is_message and msg.fill_width == 60
    ed.open_entry(0)
    doc = ed.doc
    doc.set_cursor((len(doc.lines) - 1, 0))
    ed.keys("Enter")
    ed.type("word " * 30)
    body = [line for line in doc.lines[1:] if line.strip()]
    assert len(body) > 1 and all(len(line.rstrip()) <= 60 for line in body)
