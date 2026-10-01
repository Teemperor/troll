from troll.search import compile_query, count_words, find, find_all, replacement_text


def test_find_forward_skips_match_at_cursor_and_wraps():
    lines = ["foo bar foo", "x foo"]
    p = compile_query("foo", False, False)
    m, wrapped = find(lines, (0, 0), p)
    assert m.start == (0, 8) and not wrapped
    m, wrapped = find(lines, (1, 2), p)
    assert m.start == (0, 0) and wrapped


def test_find_inclusive():
    p = compile_query("foo", False, False)
    m, _ = find(["foo"], (0, 0), p, inclusive=True)
    assert m.start == (0, 0)


def test_find_backwards():
    lines = ["foo bar foo", "x foo"]
    p = compile_query("foo", False, False)
    m, wrapped = find(lines, (0, 8), p, forward=False)
    assert m.start == (0, 0) and not wrapped
    m, wrapped = find(lines, (0, 0), p, forward=False)
    assert m.start == (1, 2) and wrapped


def test_case_and_regex_options():
    assert find(["Foo"], (0, 0), compile_query("foo", False, True), inclusive=True) is None
    assert find(["Foo"], (0, 0), compile_query("foo", False, False), inclusive=True)
    assert find(["a.c"], (0, 0), compile_query(".", False, False), inclusive=True)[0].start == (0, 1)
    m, _ = find(["x12y"], (0, 0), compile_query(r"\d+", True, False), inclusive=True)
    assert m.start == (0, 1) and m.end == (0, 3)


def test_no_match_and_no_wrap():
    p = compile_query("zz", False, False)
    assert find(["abc"], (0, 0), p) is None
    assert find(["zz", "a"], (1, 0), p, wrap=False) is None


def test_empty_regex_matches_are_ignored():
    p = compile_query("x*", True, False)
    assert [m.start for m in find_all(["axxb", ""], p)] == [(0, 1)]


def test_regex_replacement_groups():
    p = compile_query(r"(\w+)@(\w+)", True, False)
    m = find_all(["mail bob@home"], p)[0]
    assert replacement_text(m, r"\2 at \1", True) == "home at bob"
    assert replacement_text(m, r"\2", False) == r"\2"


def test_count_words():
    assert count_words("one two\nthree\n") == (2, 3, 14)
    assert count_words("") == (0, 0, 0)
