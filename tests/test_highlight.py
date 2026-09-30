import random

from diffedit import languages
from diffedit.buffer import Buffer
from diffedit.highlight import Highlighter, Tokenizer

PY = languages.BY_NAME["python"]
C = languages.BY_NAME["c"]


def tokens(lang, text, state=None):
    spans, end = Tokenizer(lang).tokenize(text, state)
    return [(text[s:e], t) for s, e, t in spans], end


def test_python_basic_tokens():
    toks, end = tokens(PY, 'def foo(x=None):  # note')
    assert ("def", "keyword") in toks
    assert ("foo", "function") in toks
    assert ("None", "constant") in toks
    assert ("# note", "comment") in toks
    assert end is None


def test_keywords_need_word_boundaries():
    toks, _ = tokens(PY, "my_if = iffy")
    assert all(t != "keyword" for _, t in toks)


def test_string_prefixes_and_escapes():
    toks, _ = tokens(PY, r'x = rb"a\"b" + f"{y}"')
    assert (r'rb"a\"b"', "string") in toks
    assert ('f"{y}"', "string") in toks


def test_hash_inside_string_is_not_a_comment():
    toks, _ = tokens(PY, 'x = "# not a comment"')
    assert toks[-1] == ('"# not a comment"', "string")


def test_triple_quoted_string_spans_lines():
    t = Tokenizer(PY)
    spans, state = t.tokenize('x = """start')
    assert state is not None
    spans, state2 = t.tokenize("middle # not comment", state)
    assert spans == [(0, 20, "string")] and state2 == state
    spans, state3 = t.tokenize('end""" + 1', state)
    assert spans[0] == (0, 6, "string") and state3 is None
    assert (9, 10, "number") in spans


def test_unterminated_single_quote_does_not_leak():
    t = Tokenizer(PY)
    _, state = t.tokenize('x = "oops')
    assert state is None


def test_c_block_comment_and_preproc():
    t = Tokenizer(C)
    spans, state = t.tokenize("#include <stdio.h> /* start")
    assert spans[0][2] == "preproc"
    assert state is not None
    spans, state = t.tokenize("still */ int x;", state)
    assert spans[0] == (0, 8, "comment")
    assert (9, 12, "type") in spans
    assert state is None


def test_diff_language():
    diff = languages.BY_NAME["diff"]
    assert tokens(diff, "+added")[0] == [("+added", "diff_add")]
    assert tokens(diff, "--- a/file")[0] == [("--- a/file", "diff_header")]
    assert tokens(diff, "@@ -1 +1 @@")[0][0][1] == "diff_hunk"


def test_gitcommit_first_line_limit():
    b = Buffer("x" * 80 + "\n\nbody\n# comment")
    h = Highlighter(b, languages.BY_NAME["gitcommit"])
    spans = h.spans(0)
    assert spans[0] == (0, 72, "heading")
    assert spans[1] == (72, 80, "error")
    assert h.spans(3) == [(0, 9, "comment")]


def test_language_detection():
    assert languages.detect("foo.py").name == "python"
    assert languages.detect("Makefile").name == "make"
    assert languages.detect("x/COMMIT_EDITMSG").name == "gitcommit"
    assert languages.detect("script", "#!/usr/bin/env bash").name == "shell"
    assert languages.detect("script", "#!/usr/bin/python3").name == "python"
    assert languages.detect("unknown.zzz").name == "text"
    assert languages.get("c++").name == "cpp"


def test_every_language_tokenizes_sample_text():
    sample = ['int main() { return "x" + 1; } // c', "# hi", "  - key: 'v'", "<a href=\"x\">", '"""', "`code`"]
    for lang in languages.LANGUAGES:
        t = Tokenizer(lang)
        state = None
        for line in sample:
            spans, state = t.tokenize(line, state)
            for s, e, _tok in spans:
                assert 0 <= s < e <= len(line)


def full_states(lang, lines):
    t = Tokenizer(lang)
    states = [None]
    for line in lines[:-1]:
        states.append(t.tokenize(line, states[-1])[1])
    return states


def test_highlighter_follows_edits_incrementally():
    b = Buffer('a = 1\nb = """\nc\n"""\nd = 2')
    h = Highlighter(b, PY)
    assert h.spans(2) == [(0, 1, "string")]
    b.delete((1, 4), (1, 7))  # remove the opening quotes
    assert h.spans(2) == []
    b.insert((0, 0), '"""\n')  # now a string opens on line 0
    assert h.spans(1) == [(0, 5, "string")]


def test_highlighter_matches_full_recompute_under_random_edits():
    rng = random.Random(1234)
    pieces = ['"""', "x", "# c", "\n", "'", " ", "/*", "*/", "y = 1", '"']
    for lang in (PY, C):
        b = Buffer("\n".join(rng.choice(pieces) for _ in range(30)))
        h = Highlighter(b, lang)
        for _step in range(200):
            if rng.random() < 0.6:
                r = rng.randrange(len(b.lines))
                c = rng.randrange(len(b.lines[r]) + 1)
                b.insert((r, c), rng.choice(pieces))
            elif len(b.lines) > 1:
                r = rng.randrange(len(b.lines) - 1)
                b.delete((r, 0), (r + 1, rng.randrange(len(b.lines[r + 1]) + 1)))
            probe = rng.randrange(len(b.lines))
            expected = full_states(lang, b.lines)
            assert h.state_at(probe) == expected[probe]
        expected = full_states(lang, b.lines)
        assert [h.state_at(i) for i in range(len(b.lines))] == expected


def test_context_at():
    b = Buffer('x = "abc  # y\nz = 1  # note\n/* open')
    h = Highlighter(b, PY)
    assert h.context_at(0, 6) == "string"
    assert h.context_at(0, 3) is None
    assert h.context_at(1, 14) == "comment"
    assert h.context_at(1, 5) is None
    hc = Highlighter(Buffer("/* open\nstill"), C)
    assert hc.context_at(1, 3) == "comment"
    assert hc.context_at(0, 1) is None  # between '/' and '*'
