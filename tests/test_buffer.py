from diffedit.buffer import Buffer


def test_insert_and_delete_single_line():
    b = Buffer("hello")
    assert b.insert((0, 5), " world") == (0, 11)
    assert b.text() == "hello world"
    assert b.delete((0, 0), (0, 6)) == "hello "
    assert b.text() == "world"


def test_multiline_insert_and_delete():
    b = Buffer("ab\ncd")
    end = b.insert((0, 1), "X\nY\nZ")
    assert b.lines == ["aX", "Y", "Zb", "cd"]
    assert end == (2, 1)
    assert b.delete((0, 1), (2, 1)) == "X\nY\nZ"
    assert b.lines == ["ab", "cd"]


def test_delete_accepts_reversed_positions_and_clamps():
    b = Buffer("abc\ndef")
    assert b.delete((1, 99), (0, 1)) == "bc\ndef"
    assert b.lines == ["a"]


def test_get_text_across_lines():
    b = Buffer("one\ntwo\nthree")
    assert b.get_text((0, 1), (2, 2)) == "ne\ntwo\nth"


def test_undo_redo_roundtrip():
    b = Buffer("x")
    b.insert((0, 1), "yz")
    b.delete((0, 0), (0, 1))
    assert b.text() == "yz"
    b.undo()
    assert b.text() == "xyz"
    b.undo()
    assert b.text() == "x"
    assert b.undo() is None
    b.redo()
    b.redo()
    assert b.text() == "yz"
    assert b.redo() is None


def test_groups_merge_with_same_key_until_sealed():
    b = Buffer("")
    for i, ch in enumerate("abc"):
        b.begin_group((0, i), "type")
        b.insert((0, i), ch)
        b.end_group((0, i + 1))
    assert len(b.undo_stack) == 1
    b.seal()
    b.begin_group((0, 3), "type")
    b.insert((0, 3), "d")
    b.end_group((0, 4))
    assert len(b.undo_stack) == 2
    assert b.undo() == (0, 3)
    assert b.text() == "abc"
    assert b.undo() == (0, 0)
    assert b.text() == ""


def test_nested_groups_form_one_undo_step():
    b = Buffer("a b")
    b.begin_group((0, 0))
    b.begin_group((0, 0))
    b.insert((0, 0), "1")
    b.end_group((0, 1))
    b.insert((0, 4), "2")
    b.end_group((0, 5))
    assert b.text() == "1a b2"
    b.undo()
    assert b.text() == "a b"


def test_new_edit_clears_redo():
    b = Buffer("a")
    b.insert((0, 1), "b")
    b.undo()
    assert b.can_redo()
    b.insert((0, 1), "c")
    assert not b.can_redo()


def test_modified_tracks_saved_point_through_undo():
    b = Buffer("a")
    assert not b.modified
    b.insert((0, 1), "b")
    assert b.modified
    b.mark_saved()
    assert not b.modified
    b.undo()
    assert b.modified
    b.redo()
    assert not b.modified


def test_typing_after_save_does_not_merge_into_saved_group():
    b = Buffer("")
    b.begin_group((0, 0), "type")
    b.insert((0, 0), "a")
    b.end_group((0, 1))
    b.mark_saved()
    b.begin_group((0, 1), "type")
    b.insert((0, 1), "b")
    b.end_group((0, 2))
    assert b.modified
    b.undo()
    assert not b.modified and b.text() == "a"


def test_listeners_get_row_and_line_delta():
    b = Buffer("a\nb\nc")
    calls = []
    b.add_listener(lambda row, delta: calls.append((row, delta)))
    b.insert((1, 0), "x\ny\n")
    b.delete((0, 1), (2, 0))
    assert calls == [(1, 2), (0, -2)]


def test_replace_lines():
    b = Buffer("1\n2\n3\n4")
    b.replace_lines(1, 2, ["a", "b", "c"])
    assert b.lines == ["1", "a", "b", "c", "4"]
