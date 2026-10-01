from troll import autoformat as af
from troll import languages
from troll.settings import Settings

PY = languages.BY_NAME["python"]
C = languages.BY_NAME["c"]
MD = languages.BY_NAME["markdown"]
HTML = languages.BY_NAME["html"]
TEXT = languages.TEXT


def S(**kw):
    s = Settings()
    for k, v in kw.items():
        setattr(s, k, v)
    return s


# ----------------------------------------------------------------- indentation


def test_detect_indent():
    assert af.detect_indent(["def f():", "  x", "  if y:", "    z"]) == (False, 2)
    assert af.detect_indent(["a", "\tb", "\tc"]) == (True, None)
    assert af.detect_indent(["no", "indent"]) == (None, None)


def test_dedent_and_make_indent():
    s = S(tab_size=4)
    assert af.dedent_once("        ", s) == "    "
    assert af.dedent_once("      ", s) == "    "
    assert af.dedent_once("  ", s) == ""
    s.expand_tabs = False
    assert af.make_indent(10, s) == "\t\t  "


def test_indent_unindent_lines():
    s = S(tab_size=2)
    assert af.indent_lines(["a", "", "b"], s) == ["  a", "", "  b"]
    assert af.unindent_lines(["   a", "\tb", "c"], s) == [" a", "b", "c"]


# --------------------------------------------------------------------- newline


def nl(line, col, lang=PY, comment_start=None, is_comment=None, **kw):
    return af.plan_newline(line, col, lang, S(**kw), comment_start, is_comment)


def test_newline_keeps_indentation():
    p = nl("    x = 1", 9)
    assert p.lines == ["    x = 1", "    "] and p.cursor == (1, 4)


def test_newline_indents_after_colon_but_not_after_comment_colon():
    assert nl("if x:", 5).lines == ["if x:", "    "]
    p = nl("x = 1  # note:", 14, comment_start=7)
    assert p.lines[1] == ""


def test_newline_dedents_after_return():
    assert nl("        return x", 16).lines[1] == "    "


def test_newline_splits_brackets():
    p = nl("foo(", 4, C)
    assert p.lines == ["foo(", "    "]
    p = nl("int f() {}", 9, C)
    assert p.lines == ["int f() {", "    ", "}"] and p.cursor == (1, 4)


def test_newline_html_tags_split():
    p = af.plan_newline("<div></div>", 5, HTML, S(tab_size=2))
    assert p.lines == ["<div>", "  ", "</div>"]


def test_newline_moves_text_after_cursor_and_strips_spaces():
    p = nl("    a   b", 5)
    assert p.lines == ["    a", "    b"]


def test_whitespace_only_line_is_cleared():
    p = nl("    ", 4)
    assert p.lines == ["", "    "]


def test_newline_continues_comments():
    p = nl("    # some text", 15, comment_start=4, is_comment=True)
    assert p.lines == ["    # some text", "    # "]
    p = nl("    // text", 11, C, comment_start=4, is_comment=True)
    assert p.lines[1] == "    // "
    p = nl("/// doc", 7, C, comment_start=0, is_comment=True)
    assert p.lines[1] == "/// "


def test_newline_in_middle_of_comment_splits_it():
    p = nl("# hello world", 8, comment_start=0, is_comment=True)
    assert p.lines == ["# hello", "# world"]


def test_enter_on_empty_comment_line_ends_comment():
    p = nl("    # ", 6, comment_start=4, is_comment=True)
    assert p.lines == ["    "] and p.cursor == (0, 4)


def test_block_comment_continuation():
    p = nl("/* start", 8, C, comment_start=0, is_comment=True)
    assert p.lines[1] == " * "
    p = nl(" * middle", 9, C, comment_start=0, is_comment=True)
    assert p.lines[1] == " * "
    p = nl("/**", 3, C, comment_start=0, is_comment=True)
    assert p.lines[1] == " * "  # an empty opener still continues
    p = nl("/* done */", 10, C, comment_start=0, is_comment=True)
    assert p.lines[1] == ""


def test_no_comment_continuation_for_strings_or_when_disabled():
    p = nl('    "# not a comment"', 21, comment_start=None, is_comment=False)
    assert p.lines[1] == "    "
    p = nl("# x", 3, comment_start=0, is_comment=True, comment_continue=False)
    assert p.lines[1] == ""


def test_shebang_is_not_continued():
    assert nl("#!/usr/bin/env python", 21, comment_start=0, is_comment=True).lines[1] == ""


def test_markdown_list_continuation():
    assert nl("- item", 6, MD).lines[1] == "- "
    assert nl("3. item", 7, MD).lines[1] == "4. "
    assert nl("  * [x] done", 12, MD).lines[1] == "  * [ ] "
    assert nl("- ", 2, MD).lines == [""]


def test_newline_without_autoindent():
    p = nl("    x", 5, auto_indent=False)
    assert p.lines == ["    x", ""] and p.cursor == (1, 0)


# ------------------------------------------------------------------- electric


def test_electric_closer_matches_opener_indentation():
    lines = ["int f() {", "    x;", "    }"]
    assert af.electric_indent(lines, 2, C, S()) == "}"
    lines = ["  foo(", "      a,", "      )"]
    assert af.electric_indent(lines, 2, C, S()) == "  )"
    assert af.electric_indent(["x", "}"], 1, C, S()) is None  # unmatched


def test_python_else_lines_up_with_if():
    lines = ["if a:", "    if b:", "        x", "        else:"]
    assert af.electric_indent(lines, 3, PY, S()) == "    else:"
    lines = ["try:", "    x", "    except ValueError:"]
    assert af.electric_indent(lines, 2, PY, S()) == "except ValueError:"
    lines = ["if a:", "    x", "else:"]
    assert af.electric_indent(lines, 2, PY, S()) is None
    lines = ["def f():", "    x = {", "        'a': 1,", "        else:"]
    assert af.electric_indent(lines, 3, PY, S()) is None  # no matching clause opener


# ----------------------------------------------------------------- auto pair


def test_pairing_rules():
    assert af.plan_pair("", 0, "(", PY, None) == ("pair", "()")
    assert af.plan_pair("()", 1, ")", PY, None) == ("skip", ")")
    assert af.plan_pair("abc", 0, "(", PY, None) is None  # before a word
    assert af.plan_pair("don", 3, "'", PY, None) is None  # apostrophe
    assert af.plan_pair("x = ", 4, '"', PY, None) == ("pair", '""')
    assert af.plan_pair("# it", 4, "'", PY, "comment") is None
    assert af.plan_pair("# (", 3, "(", PY, "comment") is None
    assert af.plan_pair('"ab"', 3, '"', PY, "string") == ("skip", '"')
    assert af.plan_pair("fn f<'a>", 5, "'", languages.BY_NAME["rust"], None) is None


def test_empty_pair_detection():
    assert af.is_empty_pair("()", 1, PY)
    assert not af.is_empty_pair("(x)", 1, PY)


# ------------------------------------------------------------------ comments


def test_toggle_comment():
    assert af.toggle_comment(["  a", "", "    b"], PY) == ["  # a", "", "  #   b"]
    assert af.toggle_comment(["  # a", "", "  #   b"], PY) == ["  a", "", "    b"]
    assert af.toggle_comment(["#a"], PY) == ["a"]
    assert af.toggle_comment(["x = 1  # c"], PY) == ["# x = 1  # c"]


def test_toggle_block_comment_language():
    css = languages.BY_NAME["css"]
    assert af.toggle_comment(["a { }"], css) == ["/* a { } */"]
    html = HTML
    assert af.toggle_comment(["<b>"], html) == ["<!-- <b> -->"]
    assert af.toggle_comment(["<!-- <b> -->"], html) == ["<b>"]


def test_toggle_comment_unknown_language():
    assert af.toggle_comment(["x"], TEXT) is None


# ------------------------------------------------------------------- justify


def test_justify_python_comment_block():
    lines = [
        "def f():",
        "    # This is a long comment that was written without",
        "    # any care for",
        "    # line length at all.",
        "    return 1",
    ]
    first, last, new = af.justify(lines, 2, PY, 30, 4)
    assert (first, last) == (1, 3)
    assert new == [
        "    # This is a long comment",
        "    # that was written without",
        "    # any care for line length",
        "    # at all.",
    ]


def test_justify_stops_at_different_comment_style_or_code():
    lines = ["// one", "// two", "/// doc", "int x; // trailing"]
    assert af.find_paragraph(lines, 0, C) == (0, 1)
    assert af.find_paragraph(lines, 2, C) == (2, 2)


def test_justify_block_comment():
    lines = ["/* Some words here", " * and more words", " * to wrap. */", "int x;"]
    first, last, new = af.justify(lines, 1, C, 20, 4)
    assert (first, last) == (0, 2)
    assert new == ["/* Some words here", " * and more words to", " * wrap. */"]


def test_justify_doxygen_block_skips_opener():
    lines = ["/**", " * aa bb cc dd", " * ee", " */"]
    first, last, new = af.justify(lines, 2, C, 12, 4)
    assert (first, last) == (1, 2)
    assert new == [" * aa bb cc", " * dd ee"]


def test_justify_plain_text_keeps_hanging_indent():
    lines = ["  first line of a", "    paragraph that", "    continues", "", "next"]
    first, last, new = af.justify(lines, 0, TEXT, 30, 4)
    assert (first, last) == (0, 2)
    assert new == ["  first line of a paragraph", "    that continues"]


def test_justify_markdown_list_items():
    lines = ["- one two three four", "  five", "- six"]
    assert af.find_paragraph(lines, 1, MD) == (0, 1)
    first, last, new = af.justify(lines, 0, MD, 12, 4)
    assert new == ["- one two", "  three four", "  five"]
    assert af.find_paragraph(lines, 2, MD) == (2, 2)


def test_justify_blank_line_returns_none():
    assert af.justify(["a", "", "b"], 1, TEXT, 80, 4) is None
    assert af.justify(["#", "# x"], 0, PY, 80, 4) is None


def test_long_words_are_not_broken():
    assert af.fill(["supercalifragilistic", "x"], "# ", "# ", 10, 4) == ["# supercalifragilistic", "# x"]


def test_justify_range_handles_multiple_paragraphs():
    lines = ["# a b", "# c", "x = 1", "# d", "# e"]
    assert af.justify_range(lines, 0, 4, PY, 80, 4) == ["# a b c", "x = 1", "# d e"]
