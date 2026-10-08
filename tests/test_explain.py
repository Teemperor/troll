"""what-is-this: token picking, the request loop, the HTTP client and the tooltip."""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from conftest import editor_with

from troll import explain, languages
from troll.view import build_frame

PY = languages.get("python")


class FakeLLM:
    """Replies with `replies` in turn (the last one repeats); records every request."""

    def __init__(self, *replies):
        self.replies = list(replies) or ["It is a thing."]
        self.calls = []

    def chat(self, messages):
        self.calls.append([dict(m) for m in messages])
        reply = self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
        if isinstance(reply, Exception):
            raise reply
        return reply


# ------------------------------------------------------------------ tokens


def test_token_at_picks_symbol_word_operator_or_selection():
    lines = ["x = self.scale(p)  -> return", "", "   "]
    t = explain.token_at(lines, PY, 0, 10)
    assert (t.text, t.start, t.end) == ("scale", 9, 14)
    assert explain.token_at(lines, PY, 0, 23).text == "return"  # keywords too
    assert explain.token_at(lines, PY, 0, 19).text == "->"
    assert explain.token_at(lines, PY, 1, 0) is None
    assert explain.token_at(lines, PY, 2, 1) is None
    t = explain.token_at(lines, PY, 0, 0, selection=((0, 4), (0, 15)))
    assert (t.text, t.start) == ("self.scale(", 4)
    # a selection over several lines doesn't count: the cursor's token is used
    assert explain.token_at(lines, PY, 0, 0, selection=((0, 4), (1, 0))).text == "x"


# --------------------------------------------------------------- the loop


def question(lines, row, col, context=2):
    return explain.Question(explain.token_at(lines, PY, row, col), "a.py", "python", lines, context)


def test_question_has_context_lines_and_marks_the_token():
    lines = [f"v{i} = {i}" for i in range(20)]
    msg = explain.question_message(question(lines, 10, 0, context=2))
    assert "What is 'v10'?" in msg
    assert "»11  v10 = 10" in msg
    assert "v8 = 8" in msg and "v12 = 12" in msg
    assert "v7 = 7" not in msg and "v13 = 13" not in msg


def test_parse_requests():
    assert explain.parse_requests("It's a variable.") is None
    assert explain.parse_requests('{"requests": [{"tool": "search", "pattern": "x"}]}') == [
        {"tool": "search", "pattern": "x"}]
    assert explain.parse_requests('```json\n{"requests": []}\n```') == []
    assert explain.parse_requests("{not json") is None


class Tools:
    def definition(self, name):
        return f"def {name}(): ..."

    def search(self, pattern):
        return f"hits for {pattern}"

    def read(self, path, start, end):
        return f"{path} {start}-{end}"


def test_model_can_ask_for_more_before_answering():
    llm = FakeLLM(
        '{"requests": [{"tool": "definition", "name": "f"}, {"tool": "read", "path": "b.py", "start": 3, "end": 9}]}',
        '{"requests": [{"tool": "search", "pattern": "f\\\\("}, {"tool": "bogus"}]}',
        "  f computes things.  ",
    )
    answer = explain.explain(question(["f()"], 0, 0), llm, Tools())
    assert answer == "f computes things."
    assert llm.calls[0][0]["role"] == "system" and llm.calls[0][1]["role"] == "user"
    first = llm.calls[1][-1]["content"]
    assert "def f(): ..." in first and "b.py 3-9" in first
    second = llm.calls[2][-1]["content"]
    assert "hits for f\\(" in second and "unknown tool" in second
    assert llm.calls[2][-2] == {"role": "assistant", "content": llm.replies[1]}


def test_rounds_are_limited():
    llm = FakeLLM('{"requests": [{"tool": "search", "pattern": "x"}]}')
    with pytest.raises(explain.ExplainError, match="kept asking"):
        explain.explain(question(["x"], 0, 0), llm, Tools(), rounds=2)
    assert len(llm.calls) == 3
    assert "last round" in llm.calls[2][-1]["content"]


def test_project_tools(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "util.py").write_text("import os\n\ndef helper(x):\n    return x + 1\n")
    (tmp_path.parent / "secret.txt").write_text("nope")
    here = explain.defs.Source("main.py", ["from pkg.util import helper", "helper(2)"], PY,
                               path=str(tmp_path / "main.py"))
    other = explain.defs.Source("pkg/util.py", (tmp_path / "pkg" / "util.py").read_text().split("\n"), PY,
                                path=str(tmp_path / "pkg" / "util.py"))
    tools = explain.ProjectTools(str(tmp_path), [here, other])
    d = tools.definition("helper")
    assert "function in pkg/util.py" in d and "3  def helper(x):" in d
    assert "pkg/util.py:3: def helper(x):" in tools.search(r"def helper")
    assert tools.read("pkg/util.py", 3, 4) == "3  def helper(x):\n4      return x + 1"
    assert "outside the project" in tools.read("../secret.txt", 1, 5)
    assert "no such file" in tools.read("missing.py", 1, 5)
    assert "bad regex" in tools.search("(")
    assert "lines that mention it" in tools.definition("nothing_like_this")


def test_pick_model_prefers_the_newest_opus():
    ids = ["aws:anthropic.claude-opus-4-5-20251101-v1:0", "aws:anthropic.claude-opus-4-20250514-v1:0",
           "aws:anthropic.claude-opus-5", "aws:anthropic.claude-sonnet-9"]
    assert explain.pick_model(ids) == "aws:anthropic.claude-opus-5"
    with pytest.raises(explain.ExplainError, match="Set ai_model"):
        explain.pick_model(["gpt-x"])


# -------------------------------------------------------------- the client


@pytest.fixture(autouse=True)
def no_proxy(monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def server():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, data):
            body = json.dumps(data).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            seen.append(("GET", self.path, self.headers.get("Authorization"), None))
            self._send(200, {"data": [{"id": "claude-opus-4-5"}, {"id": "claude-opus-5-1"}]})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(("POST", self.path, self.headers.get("Authorization"), body))
            if body["model"] == "broken":
                self._send(400, {"error": {"message": "no such model"}})
            else:
                self._send(200, {"choices": [{"message": {"content": "an answer"}}]})

    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    except PermissionError:
        pytest.skip("can't open a local server here")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/v1", seen
    httpd.shutdown()


def test_client_talks_openai_chat_completions(server):
    url, seen = server
    client = explain.Client(url + "/", api_key="sk-test")
    assert client.chat([{"role": "user", "content": "hi"}]) == "an answer"
    assert seen[0][:3] == ("GET", "/v1/models", "Bearer sk-test")
    method, path, auth, body = seen[1]
    assert (method, path) == ("POST", "/v1/chat/completions")
    assert body == {"model": "claude-opus-5-1", "messages": [{"role": "user", "content": "hi"}]}
    with pytest.raises(explain.ExplainError, match="HTTP 400: no such model"):
        explain.Client(url, model="broken").chat([])


def test_client_reports_unreachable_server():
    with pytest.raises(explain.ExplainError, match="Can't reach"):
        explain.Client("http://127.0.0.1:9/v1", model="m", timeout=2).chat([])


# ------------------------------------------------------------- the editor

TEXT = "\n".join(
    ["def scale(point, factor):", "    return tuple(c * factor for c in point)", ""]
    + [f"x{i} = {i}" for i in range(4)]
    + ["p = scale((1, 2), 3)"]
    + [f"y{i} = {i}" for i in range(20)]
)


def setup(editor, *replies, cursor=(7, 5)):
    editor.llm = FakeLLM(*replies)
    return editor_with(editor, TEXT, path="t.py", cursor=cursor, soft_wrap=False)


def screen(editor, h=30, w=90):
    return build_frame(editor, h, w).text().split("\n")


def test_what_is_this_shows_a_tooltip_pointing_at_the_token(editor):
    setup(editor, '{"requests": [{"tool": "definition", "name": "scale"}]}',
          "`scale` multiplies each coordinate by `factor`.")
    editor.keys("M-h")
    assert "def scale(point, factor):" in editor.llm.calls[1][-1]["content"]
    rows = screen(editor)
    token_y = next(i for i, r in enumerate(rows) if "p = scale((1, 2), 3)" in r)
    x = rows[token_y].index("scale") + 2  # the middle of the token
    assert rows[token_y + 1][x] == "│"
    assert rows[token_y + 2][x] == "┴" and "╭" in rows[token_y + 2]
    assert "What is 'scale'?" in rows[token_y + 3]
    assert any("scale multiplies each coordinate by factor." in r for r in rows)
    bottom = next(r for r in rows if "╰" in r)
    assert "Esc" not in bottom.strip("─╰╯ ")
    # the token is highlighted
    segs = build_frame(editor, 30, 90).rows[token_y]
    assert any(text == "scale" and bg == "tooltip.token" for text, _fg, bg in segs)
    editor.keys("Esc")
    assert editor.tooltip is None
    assert not any("What is" in r for r in screen(editor))


def test_answers_are_cached_per_token_and_line(editor):
    doc = setup(editor, "first answer", "second answer")
    editor.keys("M-h", "Esc", "M-h")
    assert len(editor.llm.calls) == 1
    assert any("first answer" in r for r in screen(editor))
    assert "Cached" in editor.message.text
    doc.set_cursor((7, 0))  # another token on the same line
    editor.keys("M-h")
    assert len(editor.llm.calls) == 2
    doc.set_cursor((7, 6))
    editor.keys("End", "!")  # editing the line changes the key
    doc.set_cursor((7, 6))
    editor.keys("M-h")
    assert len(editor.llm.calls) == 3


def test_editing_closes_the_tooltip_but_moving_does_not(editor):
    setup(editor, "an answer")
    editor.keys("M-h", "Down", "Down")
    assert editor.tooltip is not None and any("an answer" in r for r in screen(editor))
    editor.type("z")
    assert editor.tooltip is None


def test_tooltip_goes_above_near_the_bottom(editor):
    setup(editor, "near the bottom", cursor=(26, 0))
    editor.keys("M-h")
    rows = screen(editor, h=30, w=90)
    token_y = next(i for i, r in enumerate(rows) if " y18 = 18" in r)
    x = rows[token_y].index("y18") + 1
    assert rows[token_y - 1][x] == "│" and rows[token_y - 2][x] == "┬"
    assert any("near the bottom" in r for r in rows[:token_y])


def test_long_answers_scroll(editor):
    setup(editor, "\n\n".join(f"paragraph {i}" for i in range(30)))
    editor.keys("M-h")
    rows = screen(editor)
    assert any("more: M-PgDn" in r for r in rows) and not any("paragraph 29" in r for r in rows)
    editor.keys(*["M-PageDown"] * 3)
    rows = screen(editor)
    assert any("paragraph 29" in r for r in rows) and any("M-PgUp" in r for r in rows)


def test_errors_show_in_the_tooltip_and_are_not_cached(editor):
    setup(editor, explain.ExplainError("Can't reach the server"), "works now")
    editor.keys("M-h")
    assert any("⚠ Can't reach the server" in r for r in screen(editor))
    editor.keys("Esc", "M-h")
    assert any("works now" in r for r in screen(editor))


def test_while_waiting_a_spinner_shows_and_the_editor_keeps_working(editor):
    gate = threading.Event()

    class Slow(FakeLLM):
        def chat(self, messages):
            gate.wait(5)
            return "done"

    doc = setup(editor)
    editor.llm = Slow()
    editor.handle_key("M-h")
    assert editor.busy
    assert any("asking" in r for r in screen(editor))
    editor.handle_key("Down")  # not blocked
    assert doc.cursor[0] == 8
    gate.set()
    editor.poll_jobs(wait=True)
    assert not editor.busy and editor.tooltip.text == "done"


def test_context_setting_and_palette(editor):
    setup(editor, "ok")
    editor.keys("C-t")
    editor.type("set ai_context 1")
    editor.keys("Enter", "C-t")
    editor.type("what-is-this")
    editor.keys("Enter")
    msg = editor.llm.calls[0][1]["content"]
    assert "x3 = 3" in msg and "y0 = 0" in msg and "x2 = 2" not in msg
    assert "File: t.py (python)" in msg


def test_no_token(editor):
    setup(editor, cursor=(2, 0))
    editor.keys("M-h")
    assert editor.tooltip is None and "No token" in editor.message.text


def test_settings_screen_edits_text_options(editor):
    editor_with(editor, "x")
    editor.keys("C-t")
    editor.type("set")
    editor.keys("Enter")
    sc = editor.overlay
    while sc.current.key != "ai_model":
        editor.keys("Down")
    assert "(empty)" in "\n".join(screen(editor))
    editor.keys("Enter")
    editor.type("my-model")
    editor.keys("Enter")
    assert editor.settings.ai_model == "my-model"


def open_model_option(editor):
    editor_with(editor, "x")
    editor.keys("C-t")
    editor.type("set")
    editor.keys("Enter")
    while editor.overlay.current.key != "ai_model":
        editor.keys("Down")
    return editor.overlay


def test_model_dropdown_lists_the_servers_models(editor, monkeypatch):
    monkeypatch.setattr(type(editor), "option_choices", lambda ed, key: ["gpt-b", "claude-opus-5", "gpt-a"])
    sc = open_model_option(editor)
    editor.settings.ai_model = "gpt-b"
    editor.keys("Enter")
    assert sc.dropdown is not None
    text = "\n".join(screen(editor))
    assert "(auto)" in text and "claude-opus-5" in text and "gpt-a" in text
    assert sc.dropdown.current().value == "gpt-b"  # the current one is preselected
    editor.type("opus")
    editor.keys("Enter")
    assert editor.settings.ai_model == "claude-opus-5" and sc.dropdown is None
    editor.keys("Enter", "Up", "Enter")  # (auto) is at the top
    assert editor.settings.ai_model == ""
    editor.keys("Enter")
    editor.type("my-own-model")
    editor.keys("Enter")  # not listed: taken as typed
    assert editor.settings.ai_model == "my-own-model"
    editor.keys("Enter", "Esc")
    assert sc.dropdown is None and editor.overlay is sc and editor.settings.ai_model == "my-own-model"


def test_model_dropdown_falls_back_to_typing(editor, monkeypatch):
    def fail(ed, key):
        raise explain.ExplainError("Can't reach the server")

    monkeypatch.setattr(type(editor), "option_choices", fail)
    sc = open_model_option(editor)
    editor.keys("Enter")
    assert sc.dropdown is None and sc.editing is not None
    assert "Can't reach the server" in "\n".join(screen(editor))
    editor.type("typed-model")
    editor.keys("Enter")
    assert editor.settings.ai_model == "typed-model"
