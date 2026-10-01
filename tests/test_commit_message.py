"""Formatting of commit messages: the 72-column width, guide stripe and wrapping rules."""

from conftest import editor_with, make_doc

from troll import autoformat as af
from troll.editor import Editor
from troll.settings import Settings
from troll.view import build_frame, gutter_width

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


# ------------------------------------------------------------ fenced code blocks

FENCED = '''\
Subject

Text with `inline` code:

```python
def f(x):
    """A docstring
    over two lines"""
    return x + 1  # note
```

~~~c++
int main() { return 0; }
```
still c++: a ``` fence doesn't close a ~~~ block
~~~

````
plain block
```
````
Signed-off-by: A <a@b.c>
'''


def spans_of(doc, row):
    line = doc.lines[row]
    return [(line[a:b], t) for a, b, t in doc.highlighter.spans(row)]


def test_fenced_code_is_highlighted_in_its_language():
    doc = message_doc(FENCED)
    assert spans_of(doc, 4) == [("```", "code"), ("python", "label")]
    assert ("def", "keyword") in spans_of(doc, 5) and ("f", "function") in spans_of(doc, 5)
    assert spans_of(doc, 7) == [('    over two lines"""', "string")]  # multi-line state carries over
    assert ("# note", "comment") in spans_of(doc, 8)  # a Python comment, not a git '#' line
    assert spans_of(doc, 9) == [("```", "code")]
    assert ("int", "type") in spans_of(doc, 12)  # c++ via the language aliases
    assert spans_of(doc, 13) == []  # a ``` line doesn't close a ~~~ block: it's just c++ text
    assert ("still", "code") not in spans_of(doc, 14)
    assert spans_of(doc, 15) == [("~~~", "code")]
    assert ("plain block", "code") not in spans_of(doc, 18)  # no language: C++, not plain code
    assert spans_of(doc, 19) == []  # too short to close a ```` fence: C++ content
    assert spans_of(doc, 20) == [("````", "code")]
    assert spans_of(doc, 21) == [("Signed-off-by:", "key")]  # back to message highlighting


def test_unknown_language_is_plain_code():
    doc = message_doc("Subject\n\n```nosuchlang\nif x:\n```\n")
    assert spans_of(doc, 3) == [("if x:", "code")]


def test_fence_without_a_language_is_cpp():
    doc = message_doc("Subject\n\n```\nint x = 0; // c\n```\n")
    assert spans_of(doc, 2) == [("```", "code")]
    assert ("int", "type") in spans_of(doc, 3) and ("// c", "comment") in spans_of(doc, 3)


def test_typing_a_fence_rehighlights_the_lines_below():
    doc = message_doc("Subject\n\ndef f(): pass\n")
    assert ("def", "keyword") not in spans_of(doc, 2)
    doc.set_cursor((1, 0))
    for ch in "```py":
        doc.type_char(ch)
    assert ("def", "keyword") in spans_of(doc, 2)


def test_fenced_code_in_the_frame(editor):
    editor_with(editor, FENCED, path="COMMIT_EDITMSG")
    frame = build_frame(editor, 30, 100)
    row = frame.rows[1 + 5]  # title bar, then line 6: def f(x):
    assert ("def", "keyword", None) in row or any(t.startswith("def") and fg == "keyword" for t, fg, _bg in row)


def test_code_blocks_are_not_reflowed_or_wrapped():
    long_code = "x = [a_long_name_here, another_long_name_here, yet_another_long_name, and_one_more]"
    text = f"Subject\n\nSome prose that is long enough to be reflowed when justified at seventy two.\n```py\n{long_code}\ny = 2\n```\nMore prose.\n"
    doc = message_doc(text)
    doc.justify(whole=True)
    assert doc.lines[4:8] == ["```py", long_code, "y = 2", "```"]
    assert doc.lines[2].endswith("seventy") and doc.lines[3] == "two."
    doc.set_cursor((5, 0))
    assert not doc.justify()  # ^J inside a code block does nothing
    doc.set_cursor((6, len(doc.lines[6])))
    for ch in " + some more words to make this line longer than seventy two columns":
        doc.type_char(ch)
    assert len(doc.lines[6]) > 72 and doc.lines[7] == "```"  # no hard wrap in code
