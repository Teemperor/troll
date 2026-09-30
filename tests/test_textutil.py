from diffedit.textutil import (
    char_width,
    display_col,
    find_enclosing_opener,
    find_matching_bracket,
    index_at_display_col,
    next_word_start,
    prev_word_start,
)


def test_widths():
    assert char_width("a") == 1
    assert char_width("漢") == 2
    assert char_width("\x01") == 2
    assert char_width("́") == 0


def test_display_col_with_tabs():
    assert display_col("\tx", 1, 4) == 4
    assert display_col("ab\tx", 3, 4) == 4
    assert display_col("漢x", 1, 4) == 2


def test_index_at_display_col():
    assert index_at_display_col("\tabc", 2, 4) == 0  # inside the tab
    assert index_at_display_col("\tabc", 5, 4) == 2
    assert index_at_display_col("ab", 99, 4) == 2


def test_word_motion_across_lines():
    lines = ["foo bar", "  baz"]
    assert next_word_start(lines, (0, 0)) == (0, 4)
    assert next_word_start(lines, (0, 4)) == (1, 2)
    assert prev_word_start(lines, (1, 2)) == (0, 4)
    assert prev_word_start(lines, (0, 5)) == (0, 4)
    assert prev_word_start(lines, (0, 0)) == (0, 0)


def test_matching_brackets_forward_and_backward():
    lines = ["f(a[1],", "  {b})"]
    assert find_matching_bracket(lines, (0, 1)) == (1, 5)
    assert find_matching_bracket(lines, (1, 5)) == (0, 1)
    assert find_matching_bracket(lines, (0, 7)) is None  # ',' is not a bracket
    assert find_matching_bracket(lines, (0, 4)) == (0, 5)  # '[' just before the cursor counts
    assert find_matching_bracket(["(("], (0, 0)) is None


def test_matching_bracket_skips_empty_lines():
    assert find_matching_bracket(["{", "", "", "}"], (0, 0)) == (3, 0)


def test_enclosing_opener():
    lines = ["call(a, [1, 2],", "     b"]
    assert find_enclosing_opener(lines, (1, 6)) == (0, 4)
