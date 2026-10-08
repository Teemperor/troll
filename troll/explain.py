"""what-is-this: ask an LLM what the token under the cursor is for.

The request goes to an OpenAI-compatible chat completions API (stdlib
`urllib`, see `Client`). The question carries the lines around the token; the
model may answer right away or first ask for more with a JSON object of
`requests` (a definition, a project-wide search, lines of a file), which
`ProjectTools` answers, for a few rounds. Everything but `Client` is pure, so
the editor can snapshot its state and run `explain` in a background thread.
`layout` turns the answer into the styled rows of the tooltip view.py draws.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from . import definitions as defs
from . import project
from .languages import Language
from .textutil import is_word_char, text_width, wrap_starts

DEFAULT_URL = "http://localhost:11211/api/openai/v1"
MAX_ROUNDS = 3  # rounds of requests for more information before the model has to answer
MAX_REQUESTS = 5  # per round
MAX_READ_LINES = 300
MAX_SEARCH_HITS = 40
MAX_DEFINITION_LINES = 80


class ExplainError(Exception):
    pass


@dataclass
class Token:
    text: str
    row: int
    start: int
    end: int


def token_at(lines: list[str], lang: Language, row: int, col: int, selection=None) -> Token | None:
    """The selection (if it's on one line), else the symbol/word/operator at (row, col)."""
    if selection is not None:
        (r1, c1), (r2, c2) = selection
        text = lines[r1][c1:c2] if r1 == r2 else ""
        if text.strip():
            lead = len(text) - len(text.lstrip())
            return Token(text.strip(), r1, c1 + lead, c1 + lead + len(text.strip()))
    if not 0 <= row < len(lines):
        return None
    sym = defs.symbol_at(lines, lang, row, col)
    if sym is not None:
        return Token(sym.name, row, sym.col, sym.end)
    line = lines[row]

    def span(pred):
        s = e = col
        while s > 0 and pred(line[s - 1]):
            s -= 1
        while e < len(line) and pred(line[e]):
            e += 1
        return (s, e) if e > s else None

    # a keyword or number, else an operator like `->` or `:=`
    found = span(is_word_char) or span(lambda ch: not ch.isspace() and not is_word_char(ch) and ch not in "()[]{},;")
    if found is None:
        return None
    s, e = found
    return Token(line[s:e], row, s, e)


# --------------------------------------------------------------- the prompt


@dataclass
class Question:
    token: Token
    label: str  # the file as the model should see it
    language: str
    lines: list[str]  # the whole file
    context: int  # lines above and below the token that go with the question


SYSTEM_PROMPT = f"""\
You explain code to a programmer who is reading it in a text editor. They point at a \
token and ask what it is. Explain what it is and what its point is right here: its \
role in this code, where it comes from, what it means for the surrounding lines. Don't \
explain general programming basics unless the token is a language feature.

Your answer is shown in a small tooltip: at most about 120 words, plain text, short \
paragraphs, no headings and no bullet lists. Use `backticks` for code.

If you need more information to answer well (e.g. the token is defined elsewhere), \
reply with ONLY a JSON object instead of an answer:
{{"requests": [REQUEST, ...]}}
where each REQUEST is one of
  {{"tool": "definition", "name": "NAME"}}  - the definition of a function, class, variable, macro, ...
  {{"tool": "search", "pattern": "REGEX"}}  - lines in the project matching a Python regex (at most {MAX_SEARCH_HITS})
  {{"tool": "read", "path": "PATH", "start": N, "end": M}}  - lines N to M (1-based) of a project file
Ask for at most {MAX_REQUESTS} things at a time, and only when it really improves the answer."""


def context_block(q: Question) -> str:
    row = q.token.row
    a = max(0, row - q.context)
    b = min(len(q.lines), row + q.context + 1)
    w = len(str(b))
    out = []
    for i in range(a, b):
        mark = "»" if i == row else " "
        out.append(f"{mark}{i + 1:>{w}}  {q.lines[i]}")
    return "\n".join(out)


def question_message(q: Question) -> str:
    t = q.token
    a = max(0, t.row - q.context) + 1
    b = min(len(q.lines), t.row + q.context + 1)
    return (
        f"File: {q.label} ({q.language})\n"
        f"Lines {a}-{b} of {len(q.lines)} (the token is on line {t.row + 1}, marked with », column {t.start + 1}):\n"
        f"```\n{context_block(q)}\n```\n\n"
        f"What is '{t.text}'?"
    )


def parse_requests(reply: str) -> list[dict] | None:
    """The requests in a reply that asks for more information, None for an answer."""
    text = reply.strip()
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    if not text.startswith("{"):
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    reqs = data.get("requests") if isinstance(data, dict) else None
    if not isinstance(reqs, list):
        return None
    return [r for r in reqs if isinstance(r, dict)]


class Tools(Protocol):
    def definition(self, name: str) -> str: ...
    def search(self, pattern: str) -> str: ...
    def read(self, path: str, start: int, end: int) -> str: ...


def run_request(req: dict, tools: Tools) -> str:
    tool = req.get("tool")
    try:
        if tool == "definition":
            name = str(req.get("name", ""))
            return f"## definition of {name}\n{tools.definition(name)}"
        if tool == "search":
            pattern = str(req.get("pattern", ""))
            return f"## search for /{pattern}/\n{tools.search(pattern)}"
        if tool == "read":
            path = str(req.get("path", ""))
            start, end = int(req.get("start", 1)), int(req.get("end", MAX_READ_LINES))
            return f"## {path} lines {start}-{end}\n{tools.read(path, start, end)}"
    except (TypeError, ValueError) as e:
        return f"## {json.dumps(req)}\nbad request: {e}"
    return f"## {json.dumps(req)}\nunknown tool (use definition, search or read)"


class Chat(Protocol):
    def chat(self, messages: list[dict]) -> str: ...


def explain(q: Question, client: Chat, tools: Tools, rounds: int = MAX_ROUNDS) -> str:
    """Ask the model; answer its requests for more information up to `rounds` times."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question_message(q)},
    ]
    for n in range(rounds + 1):
        reply = client.chat(messages)
        reqs = parse_requests(reply)
        if reqs is None:
            answer = reply.strip()
            if not answer:
                raise ExplainError("The model sent an empty answer")
            return answer
        if n == rounds:
            break
        messages.append({"role": "assistant", "content": reply})
        results = "\n\n".join(run_request(r, tools) for r in reqs[:MAX_REQUESTS]) or "(no requests)"
        if n == rounds - 1:
            results += "\n\nThat was the last round: answer the question now, no more requests."
        else:
            results += "\n\nAnswer the question now, or ask for more (JSON only) if you still need to."
        messages.append({"role": "user", "content": results})
    raise ExplainError("The model kept asking for more information instead of answering")


# ---------------------------------------------------------------- the tools


def _numbered(lines: list[str], start: int) -> str:
    """`lines` with 1-based line numbers, `start` being the index of the first one."""
    w = len(str(start + len(lines)))
    return "\n".join(f"{start + i + 1:>{w}}  {line}" for i, line in enumerate(lines))


class ProjectTools:
    """Answers the model's requests from a snapshot of the open files plus the
    files below `root` (nothing outside it is ever read)."""

    def __init__(self, root: str, sources: list[defs.Source]):
        self.root = os.path.abspath(root)
        self.sources = sources  # the file of the question first
        self._files: list[str] | None = None

    def _rel(self, path: str | None) -> str:
        if not path:
            return "?"
        rel = os.path.relpath(path, self.root)
        return path if rel.startswith("..") else rel

    def _resolve(self, path: str) -> str | None:
        full = os.path.normpath(os.path.join(self.root, os.path.expanduser(path)))
        if os.path.commonpath([full, self.root]) != self.root:
            return None
        return full

    def _lines(self, full: str) -> list[str] | None:
        for src in self.sources:
            if src.path and os.path.abspath(src.path) == full:
                return src.lines
        try:
            if os.path.getsize(full) > 4 << 20:
                return None
            with open(full, "rb") as f:
                return f.read().decode("utf-8", "replace").split("\n")
        except OSError:
            return None

    def definition(self, name: str) -> str:
        if not name:
            return "no name given"
        sources = list(self.sources)
        here = sources[0] if sources else None
        if here is not None and here.lang.name in ("c", "cpp"):
            known = {os.path.abspath(s.path) for s in sources if s.path}
            for path in defs.cpp_related_files(here.lines, here.path, self.root):
                lines = None if path in known else self._lines(path)
                if lines is not None:
                    sources.append(defs.Source(self._rel(path), lines, here.lang, path=path))
        out = []
        sym = defs.Symbol(name, -1, 0, 0)
        for src in sources:
            if src.lang.name not in defs.SUPPORTED:
                continue
            for d in src.find(sym, None)[:3]:
                end = min(d.end, d.start + MAX_DEFINITION_LINES - 1)
                out.append(f"{d.kind} in {src.label}:\n{_numbered(src.lines[d.start:end + 1], d.start)}")
            if len(out) >= 3:
                break
        if out:
            return "\n\n".join(out[:3])
        hits = self.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)")
        return f"no definition found; lines that mention it:\n{hits}"

    def search(self, pattern: str) -> str:
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return f"bad regex: {e}"
        if self._files is None:
            self._files = project.list_files(self.root)
        hits = project.search_files(self.root, self._files, rx)
        if not hits:
            return "no matches"
        out = [f"{h.path}:{h.row + 1}: {h.text.strip()[:200]}" for h in hits[:MAX_SEARCH_HITS]]
        if len(hits) > MAX_SEARCH_HITS:
            out.append(f"... and {len(hits) - MAX_SEARCH_HITS} more")
        return "\n".join(out)

    def read(self, path: str, start: int, end: int) -> str:
        full = self._resolve(path)
        if full is None:
            return "that file is outside the project"
        lines = self._lines(full)
        if lines is None:
            return "no such file (or it can't be read)"
        start = max(1, start)
        end = min(len(lines), end, start + MAX_READ_LINES - 1)
        if start > end:
            return f"the file has {len(lines)} lines"
        return _numbered(lines[start - 1:end], start - 1)


# --------------------------------------------------------------- the client


def _opus_version(model_id: str) -> tuple[int, int] | None:
    """(major, minor) of a Claude Opus model id ("claude-opus-4-5-20251101", "aws:anthropic.claude-opus-5")."""
    m = re.search(r"claude-opus-(\d+)(?:-(\d{1,2})(?!\d))?", model_id)
    if m is None:
        return None
    return int(m.group(1)), int(m.group(2) or 0)


def pick_model(ids: list[str]) -> str:
    """The newest Claude Opus among `ids` (what the ai-review script uses)."""
    found = sorted((v, i) for i in ids if (v := _opus_version(i)) is not None)
    if not found:
        shown = ", ".join(ids[:8]) + (", ..." if len(ids) > 8 else "")
        raise ExplainError(f"Set ai_model to one of the server's models ({shown or 'none listed'})")
    return found[-1][1]


class Client:
    """Minimal OpenAI chat completions client."""

    _models: dict[str, str] = {}  # url -> auto-picked model, for the rest of the session

    def __init__(self, url: str, model: str = "", api_key: str | None = None, timeout: float = 120):
        self.url = url.rstrip("/")
        self.model = model
        self.api_key = api_key or "not-needed"
        self.timeout = timeout

    def _request(self, path: str, body: dict | None = None) -> dict:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.url + path, data=data, method="GET" if body is None else "POST", headers={
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace").strip()
            try:
                detail = json.loads(detail)["error"]["message"]
            except (ValueError, KeyError, TypeError):
                pass
            raise ExplainError(f"{self.url}: HTTP {e.code}: {str(detail)[:200]}") from None
        except urllib.error.URLError as e:
            raise ExplainError(f"Can't reach {self.url}: {e.reason}") from None
        except (OSError, ValueError) as e:  # timeouts, bad JSON
            raise ExplainError(f"{self.url}: {e}") from None

    def list_models(self) -> list[str]:
        data = self._request("/models").get("data") or []
        return [m["id"] for m in data if isinstance(m, dict) and isinstance(m.get("id"), str)]

    def model_id(self) -> str:
        if self.model:
            return self.model
        if self.url not in Client._models:
            Client._models[self.url] = pick_model(self.list_models())
        return Client._models[self.url]

    def chat(self, messages: list[dict]) -> str:
        resp = self._request("/chat/completions", {"model": self.model_id(), "messages": messages})
        try:
            return resp["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise ExplainError(f"Unexpected answer from {self.url}: {str(resp)[:200]}") from None


# --------------------------------------------------------------- the tooltip


@dataclass
class Tooltip:
    """The answer box drawn at a token (`ed.tooltip`)."""

    doc: object  # the Document the token is in
    token: Token
    version: int  # buffer version when it was opened: any edit closes it
    text: str | None = None  # None while the model is thinking
    error: str | None = None
    model: str = ""
    scroll: int = 0


Styled = list[tuple[str, str]]  # (text, fg style) parts of one tooltip row


def layout(text: str, width: int) -> list[Styled]:
    """The rows of an answer wrapped to `width` cells. `code` spans and fenced
    code blocks get the code style; `**` emphasis markers are dropped."""
    rows: list[Styled] = []
    in_block = False
    for para in text.strip("\n").split("\n"):
        if para.lstrip().startswith("```"):
            in_block = not in_block
            continue
        if in_block:
            chars, styles = list(para.replace("\t", "    ")), None
        else:
            para = para.replace("**", "")
            chars, styles, code = [], [], False
            for ch in para:
                if ch == "`":
                    code = not code
                    continue
                chars.append(ch)
                styles.append("tooltip.code" if code else "tooltip.text")
        line = "".join(chars)
        if not line.strip():
            if rows and rows[-1]:
                rows.append([])
            continue
        starts = wrap_starts(line, width, 4)
        for k, s in enumerate(starts):
            e = starts[k + 1] if k + 1 < len(starts) else len(line)
            piece = line[s:e] if styles is None or k + 1 == len(starts) else line[s:e].rstrip(" ")
            if styles is None:
                rows.append([(piece, "tooltip.code")])
                continue
            row: Styled = []
            for i, ch in enumerate(piece):
                st = styles[s + i]
                if row and row[-1][1] == st:
                    row[-1] = (row[-1][0] + ch, st)
                else:
                    row.append((ch, st))
            rows.append(row)
    while rows and not rows[-1]:
        rows.pop()
    return rows


def row_width(row: Styled) -> int:
    return sum(text_width(t) for t, _ in row)
