from troll.merge import merge3, merge3_bytes


def test_trivial_cases():
    assert merge3(["a"], ["a"], ["b"]) == ["b"]
    assert merge3(["a"], ["b"], ["a"]) == ["b"]
    assert merge3(["a"], ["b"], ["b"]) == ["b"]


def test_adjacent_changes_merge_cleanly():
    base = ["# comment", "return 1"]
    ours = ["# better comment", "return 1"]
    theirs = ["# comment", "return 2"]
    assert merge3(base, ours, theirs) == ["# better comment", "return 2"]


def test_disjoint_changes_and_insertions():
    base = ["a", "b", "c", "d"]
    ours = ["a", "B", "c", "d"]
    theirs = ["a", "b", "c", "x", "d"]
    assert merge3(base, ours, theirs) == ["a", "B", "c", "x", "d"]
    assert merge3(base, ["z", *base], [*base, "y"]) == ["z", "a", "b", "c", "d", "y"]


def test_overlapping_changes_conflict():
    assert merge3(["a", "b"], ["A", "b"], ["x", "b"]) is None
    assert merge3(["a", "b", "c"], ["a", "B", "c"], ["a", "c"]) is None
    assert merge3(["a", "b"], ["a", "1", "b"], ["a", "2", "b"]) is None  # both insert at the same spot


def test_same_change_on_both_sides():
    assert merge3(["a", "b", "c"], ["a", "B", "c"], ["a", "B", "c", "d"]) == ["a", "B", "c", "d"]


def test_bytes_wrapper():
    assert merge3_bytes(b"a\nb\n", b"A\nb\n", b"a\nB\n") == b"A\nB\n"
    assert merge3_bytes(b"\0", b"\0x", b"\0") == b"\0x"
    assert merge3_bytes(b"\0", b"\0x", b"\0y") is None
