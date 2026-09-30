"""Find where the symbol under the cursor is defined (LLVM IR, Python, C/C++).

Pure functions over lists of lines. `symbol_at` finds the name under the
cursor and `find_definitions` lists candidate definitions, best first.
LLVM IR is regular enough to resolve exactly; Python and C++ are matched with
patterns plus a rough notion of scope (indentation / brace blocks), so they
are heuristics that are usually, not always, right.

Searching works on "code lines": the text with comments and string literals
blanked out (same columns), so names inside them don't count.
"""

from __future__ import annotations

import keyword
import os
import re
from dataclasses import dataclass, field

from .highlight import Tokenizer
from .languages import Language

Span = tuple[int, int, str]

# Tiers order candidates across files: lower is better. WEAK candidates
# (declarations, imports) make the caller look for a real definition elsewhere.
LOCAL, DEFINITION, OTHER, WEAK, UNLIKELY = 0, 1, 2, 3, 4

SUPPORTED = ("llvm", "python", "c", "cpp")


@dataclass(frozen=True)
class Symbol:
    name: str  # LLVM: with its sigil ("%x", "@f", "!12", "#0"); otherwise the identifier
    row: int
    col: int  # where the name starts in the line
    end: int
    qualifier: str | None = None  # the identifier before `access` ("self" in self.x)
    access: str | None = None  # ".", "->" or "::" right before the name


@dataclass
class Definition:
    row: int
    col: int
    kind: str  # "function", "value", "metadata", "class", "variable", "import", ...
    start: int  # rows start..end (inclusive) are shown in the definition panel
    end: int
    tier: int = DEFINITION
    related: list[tuple[int, int]] = field(default_factory=list)  # more spans to show (LLVM metadata)
    module: str | None = None  # imports: the module ("os.path", ".util")
    target: str | None = None  # imports: the imported name (differs from the symbol for `x as y`)
    sort: tuple = ()


def code_lines(lines: list[str], lang: Language, spans=None) -> list[str]:
    """`lines` with comments and strings replaced by spaces. `spans(row)` can
    supply cached highlight spans; otherwise the lines are tokenized here."""
    if spans is None:
        tok = Tokenizer(lang)
        state = None
        out = []
        for line in lines:
            sp, state = tok.tokenize(line, state)
            out.append(_blank(line, sp))
        return out
    return [_blank(line, spans(i)) for i, line in enumerate(lines)]


def _blank(line: str, spans: list[Span]) -> str:
    chars = None
    for s, e, token in spans:
        if token in ("comment", "string"):
            if chars is None:
                chars = list(line)
            chars[s:e] = " " * (e - s)
    return line if chars is None else "".join(chars)


def _pick(matches, col: int):
    """The match containing `col`, else one ending right at it."""
    touching = None
    for m in matches:
        if m.start() <= col < m.end():
            return m
        if m.end() == col:
            touching = m
    return touching


def symbol_at(lines: list[str], lang: Language, row: int, col: int) -> Symbol | None:
    if not 0 <= row < len(lines):
        return None
    line = lines[row]
    if lang.name == "llvm":
        return _ll_symbol(line, row, col)
    m = _pick(IDENT.finditer(line), col)
    if m is None or m.group()[0].isdigit():
        return None
    name = m.group()
    if (lang.name == "python" and keyword.iskeyword(name)) or (lang.name != "python" and name in CPP_KEYWORDS):
        return None
    q = re.search(r"(\w*)\s*(\.|->|::)\s*$", line[: m.start()])
    if q is None:
        return Symbol(name, row, m.start(), m.end())
    return Symbol(name, row, m.start(), m.end(), q.group(1) or None, q.group(2))


def find_definitions(lines: list[str], code: list[str], lang: Language, sym: Symbol,
                     row: int | None) -> list[Definition]:
    """Candidates for `sym`, best first. `row` is the cursor row when searching
    the file the symbol is in, None when searching another file."""
    if lang.name == "llvm":
        found = _ll_find(lines, code, sym, row)
    elif lang.name == "python":
        found = _py_find(code, sym, row)
    elif lang.name in ("c", "cpp"):
        found = _cpp_find(code, sym, row)
    else:
        return []
    found.sort(key=lambda d: (d.tier, d.sort))
    return found


def _stmt_end(code: list[str], row: int, limit: int = 60) -> int:
    """Last row of the statement starting at `row` (until brackets balance)."""
    depth = 0
    for r in range(row, min(len(code), row + limit)):
        for ch in code[r]:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
        if depth <= 0:
            return r
    return row


def _trim_blank(lines: list[str], start: int, end: int) -> int:
    while end > start and not lines[end].strip():
        end -= 1
    return end


# ================================================================== LLVM IR

LL_NAME = r'(?:[-\w$.]+|"[^"]*")'
LL_REF = re.compile(rf"[%@]{LL_NAME}|![-\w$.]+|#\d+")
LL_LABEL = re.compile(rf"^\s*({LL_NAME}):")
LL_METADATA_DEF = re.compile(r"^\s*!([-\w$.]+)\s*=")
LL_METADATA_REF = re.compile(r"!(\d+|[A-Za-z$._][-\w$.]*)(?![-\w$.(])")


def _ll_bare(name: str) -> str:
    return name[1:-1] if len(name) >= 2 and name[0] == '"' == name[-1] else name


def _ll_name_rx(bare: str) -> str:
    e = re.escape(bare)
    return rf'(?:{e}(?![-\w$.])|"{e}")'


def _ll_symbol(line: str, row: int, col: int) -> Symbol | None:
    m = LL_LABEL.match(line)
    if m and m.start(1) <= col <= m.end(1):
        return Symbol("%" + _ll_bare(m.group(1)), row, m.start(1), m.end(1))
    m = _pick(LL_REF.finditer(line), col)
    if m is None:
        return None
    text = m.group()
    if text[0] == "!" and line[m.end() : m.end() + 1] == "(":
        return None  # a node type like !DILocation(...), not a reference
    if text[0] in "%@":
        text = text[0] + _ll_bare(text[1:])
    return Symbol(text, row, m.start(), m.end())


def _ll_function(code: list[str], row: int) -> tuple[int, int] | None:
    """Rows of the `define` line and the closing `}` of the function around `row`."""
    start = None
    for r in range(row, -1, -1):
        if code[r].startswith("define"):
            start = r
            break
        if code[r].startswith("}") and r != row:
            return None
    if start is None:
        return None
    end = start
    while end + 1 < len(code) and not code[end].startswith("}"):
        end += 1
    return (start, end) if end >= row else None


def _ll_related(code: list[str], index: dict[str, int], rows: list[int], limit: int = 12) -> list[tuple[int, int]]:
    """Definitions of the metadata the given rows refer to, transitively."""
    seen = set(rows)
    queue = list(rows)
    out = []
    while queue and len(out) < limit:
        r = queue.pop(0)
        for m in LL_METADATA_REF.finditer(code[r]):
            target = index.get(m.group(1))
            if target is None or target in seen:
                continue
            seen.add(target)
            out.append((target, _stmt_end(code, target)))
            queue.append(target)
            if len(out) >= limit:
                break
    return out


def _ll_find(lines: list[str], code: list[str], sym: Symbol, row: int | None) -> list[Definition]:
    sigil, bare = sym.name[0], sym.name[1:]
    nm = _ll_name_rx(bare)
    out: list[Definition] = []
    if sigil == "@":
        glob = re.compile(rf"^\s*(@{nm})\s*=")
        func = re.compile(rf"^(define|declare)\b.*?(@{nm})\s*\(")
        for i, c in enumerate(code):
            m = glob.match(c)
            if m:
                out.append(Definition(i, m.start(1), "global", i, _stmt_end(code, i)))
                continue
            m = func.match(c)
            if m and m.group(1) == "define":
                fn = _ll_function(code, i)
                out.append(Definition(i, m.start(2), "function", i, fn[1] if fn else i))
            elif m:
                out.append(Definition(i, m.start(2), "declaration", i, i, tier=WEAK))
    elif sigil == "%":
        fn = _ll_function(code, row) if row is not None else None
        if fn is not None:
            first, last = fn
            m = re.search(rf"%{nm}", code[first])
            if m:
                out.append(Definition(first, m.start(), "argument", first, first, tier=LOCAL))
            value = re.compile(rf"^\s*(%{nm})\s*=")
            label = re.compile(rf"^\s*({nm}):")
            for i in range(first + 1, last):
                m = value.match(code[i])
                if m:
                    out.append(Definition(i, m.start(1), "value", i, _stmt_end(code, i), tier=LOCAL))
                    break
                m = label.match(code[i])
                if m:
                    end = i
                    while end + 1 < last and not LL_LABEL.match(code[end + 1]):
                        end += 1
                    out.append(Definition(i, m.start(1), "label", i, _trim_blank(lines, i, end), tier=LOCAL))
                    break
        typedef = re.compile(rf"^(%{nm})\s*=\s*type\b")
        for i, c in enumerate(code):
            m = typedef.match(c)
            if m:
                out.append(Definition(i, 0, "type", i, _stmt_end(code, i)))
    elif sigil == "!":
        rx = re.compile(rf"^\s*(!{re.escape(bare)})\s*=")
        for i, c in enumerate(code):
            m = rx.match(c)
            if m:
                out.append(Definition(i, m.start(1), "metadata", i, _stmt_end(code, i)))
                break
    elif sigil == "#":
        rx = re.compile(rf"^\s*attributes\s+({re.escape(sym.name)})\s*=")
        for i, c in enumerate(code):
            m = rx.match(c)
            if m:
                out.append(Definition(i, m.start(1), "attributes", i, i))
    if out:
        index = {}
        for i, c in enumerate(code):
            m = LL_METADATA_DEF.match(c)
            if m:
                index.setdefault(m.group(1), i)
        for d in out:
            if d.kind == "metadata":
                d.related = _ll_related(code, index, [d.row])
            elif d.kind in ("value", "global", "function", "declaration"):
                d.related = _ll_related(code, index, [d.row], limit=6)  # e.g. its !dbg location
    return out


# ================================================================== Python

IDENT = re.compile(r"[^\W\d]\w*|\d\w*")
PY_SCOPE = re.compile(r"^(\s*)(?:async\s+)?(def|class)\s+([^\W\d]\w*)")


@dataclass
class _Scope:
    kind: str  # "def" | "class"
    row: int
    indent: int
    body: int  # first row after the header
    end: int
    name: str
    col: int  # where the name starts


def _depths(code: list[str]) -> list[int]:
    """Bracket depth at the start of each line."""
    out = []
    d = 0
    for c in code:
        out.append(d)
        for ch in c:
            if ch in "([{":
                d += 1
            elif ch in ")]}":
                d = max(0, d - 1)
    return out


def _indent(text: str) -> int:
    return len(text) - len(text.lstrip())


def _py_scopes(code: list[str], depth: list[int]) -> list[_Scope]:
    scopes = []
    n = len(code)
    for i, c in enumerate(code):
        m = PY_SCOPE.match(c)
        if not m or depth[i]:
            continue
        indent = len(m.group(1))
        h = i
        while h + 1 < n and depth[h + 1] > 0:
            h += 1
        end = h
        for j in range(h + 1, n):
            t = code[j]
            if not t.strip():
                continue
            if depth[j] == 0 and _indent(t) <= indent:
                break
            end = j
        scopes.append(_Scope(m.group(2), i, indent, h + 1, end, m.group(3), m.start(3)))
    return scopes


def _py_params(code: list[str], sc: _Scope) -> list[tuple[str, int, int]]:
    out = []
    depth = 0
    expect = False
    for r in range(sc.row, sc.body):
        c = code[r]
        j = sc.col + len(sc.name) if r == sc.row else 0
        while j < len(c):
            ch = c[j]
            if ch in "([{":
                depth += 1
                expect = depth == 1
            elif ch in ")]}":
                depth -= 1
                if depth == 0:
                    return out
            elif depth == 1 and ch == ",":
                expect = True
            elif depth == 1 and expect and (ch.isalpha() or ch == "_"):
                m = IDENT.match(c, j)
                out.append((m.group(), r, j))
                expect = False
                j = m.end()
                continue
            elif depth == 1 and expect and ch not in " \t*":
                expect = False
            j += 1
    return out


def _targets(text: str, offset: int):
    """Names bound by an assignment/for target list like `a, (b, *c)`."""
    for m in re.finditer(r"[^,()\[\]]+", text):
        t = m.group().split(":")[0]
        name = t.strip().lstrip("*").strip()
        if name:
            yield name, offset + m.start() + t.index(name)


def _py_find(code: list[str], sym: Symbol, row: int | None) -> list[Definition]:
    name = sym.name
    n_rx = re.escape(name)
    depth = _depths(code)
    scopes = _py_scopes(code, depth)
    by_row = {s.row: s for s in scopes}
    attr = sym.access == "."
    via_self = attr and sym.qualifier in ("self", "cls")
    cands: list[tuple[Definition, _Scope | None]] = []

    def scope_of(r: int) -> _Scope | None:
        best = None
        for s in scopes:
            if s.body <= r <= s.end and (best is None or s.row > best.row):
                best = s
        return best

    def add(r, c, kind, start, end, scope="auto", **kw):
        d = Definition(r, c, kind, start, end, **kw)
        cands.append((d, scope_of(r) if scope == "auto" else scope))

    for i, c in enumerate(code):
        sc = by_row.get(i)
        if sc is not None:
            if sc.name == name:
                start = i
                while start > 0 and code[start - 1].strip().startswith("@"):
                    start -= 1
                add(i, sc.col, "function" if sc.kind == "def" else "class", start,
                    _trim_blank(code, i, sc.end))
            if sc.kind == "def" and not attr:
                for p, r, col in _py_params(code, sc):
                    if p == name:
                        add(r, col, "parameter", sc.row, sc.body - 1, scope=sc)
            continue
        m = re.match(r"^\s*import\s+(.+)", c)
        if m and not attr:
            for part in m.group(1).split(","):
                pm = re.match(r"\s*([\w.]+)(?:\s+as\s+(\w+))?", part)
                if pm and (pm.group(2) or pm.group(1).split(".")[0]) == name:
                    col = re.search(rf"\b{n_rx}\b", c[m.start(1):]).start() + m.start(1)
                    add(i, col, "import", i, i, module=pm.group(1) if pm.group(2) else name)
            continue
        m = re.match(r"^\s*from\s+(\.*[\w.]*)\s+import\b", c)
        if m and not attr:
            end = _stmt_end(code, i)
            for r in range(i, end + 1):
                text = code[r][m.end():] if r == i else code[r]
                offset = m.end() if r == i else 0
                for im in re.finditer(r"([^\W\d]\w*)(?:\s+as\s+([^\W\d]\w*))?", text):
                    if (im.group(2) or im.group(1)) == name:
                        col = offset + (im.start(2) if im.group(2) else im.start(1))
                        add(r, col, "import", i, end, module=m.group(1), target=im.group(1))
            continue
        if depth[i] == 0:
            m = re.match(r"^(\s*)([^=]*?)(?<![=!<>+\-*/%&|^@:~])=(?!=)", c)
            if m and "(" not in m.group(2).split(":")[0]:
                for t, col in _targets(m.group(2), m.start(2)):
                    if t == name and not attr or via_self and t == f"{sym.qualifier}.{name}" \
                            or attr and not via_self and t.endswith("." + name):
                        if "." in t:
                            col += t.rindex(".") + 1
                        add(i, col, "variable", i, _stmt_end(code, i))
                    elif attr and t == name and (scope_of(i) or _Scope("", 0, 0, 0, 0, "", 0)).kind == "class":
                        add(i, col, "variable", i, _stmt_end(code, i))  # a class attribute
            elif not attr:
                m = re.match(rf"^(\s*)({n_rx})\s*:\s*[^\s=][^=]*$", c)
                if m and not c.rstrip().endswith(":"):
                    add(i, m.start(2), "variable", i, i)
        if attr:
            continue
        for m in re.finditer(r"\bfor\s+(.+?)\s+in\b", c):
            for t, col in _targets(m.group(1), m.start(1)):
                if t == name:
                    add(i, col, "variable", i, i)
        for m in re.finditer(rf"\bas\s+({n_rx})\b|(?<![\w.])({n_rx})\s*:=", c):
            add(i, m.start(1) if m.group(1) else m.start(2), "variable", i, i)

    enclosing = [s for s in scopes if row is not None and s.row <= row <= s.end]
    innermost = max(enclosing, key=lambda s: s.row) if enclosing else None
    own_class = next((s for s in sorted(enclosing, key=lambda s: -s.row) if s.kind == "class"), None)
    out = []
    for d, sc in cands:
        if via_self:
            in_class = own_class is not None and own_class.row < d.row <= own_class.end
            d.tier = LOCAL if in_class else OTHER
            d.sort = (d.kind != "function", d.row)
        elif attr:
            d.tier = DEFINITION if d.kind in ("function", "class") else OTHER
            d.sort = (d.row,)
        else:
            visible = sc is None or (sc in enclosing and (sc.kind == "def" or sc is innermost))
            if d.kind == "import":
                d.tier = WEAK
            elif not visible:
                d.tier = OTHER if d.kind in ("function", "class") or sc.kind == "class" else UNLIKELY
            else:
                d.tier = DEFINITION if sc is None else LOCAL
            before = row is not None and d.row <= row
            d.sort = (-(sc.indent + 1) if visible and sc else 0, not before, -d.row if before else d.row)
        out.append(d)
    return out


def python_module_path(module: str, from_path: str | None, cwd: str) -> str | None:
    """Best guess at the file of `module` (e.g. "pkg.mod", "..util") imported from `from_path`."""
    here = os.path.dirname(os.path.abspath(from_path)) if from_path else cwd
    dots = len(module) - len(module.lstrip("."))
    rest = module[dots:].replace(".", os.sep)
    if dots:
        bases = [here]
        for _ in range(dots - 1):
            bases = [os.path.dirname(bases[0])]
    else:
        bases = [here, cwd]
        d = here
        for _ in range(4):
            d = os.path.dirname(d)
            bases.append(d)
    for base in bases:
        stem = os.path.join(base, rest) if rest else base
        for cand in (stem + ".py", os.path.join(stem, "__init__.py")):
            if os.path.isfile(cand):
                return cand
    return None


# ================================================================== C / C++

CPP_KEYWORDS = frozenset(
    "alignas alignof and asm auto bool break case catch char class concept const consteval constexpr "
    "constinit const_cast continue co_await co_return co_yield decltype default delete do double "
    "dynamic_cast else enum explicit export extern false float for friend goto if inline int long "
    "mutable namespace new noexcept not nullptr operator or private protected public register "
    "reinterpret_cast requires return short signed sizeof static static_assert static_cast struct "
    "switch template this thread_local throw true try typedef typeid typename union unsigned using "
    "virtual void volatile while".split()
)
NOT_TYPE = frozenset(
    "return else new delete case throw goto sizeof co_return co_await co_yield typeid alignof and or not "
    "if while for switch do catch using namespace public private protected operator template "
    "static_assert".split()
)
TYPE_INTRO = re.compile(
    r"\b(class|struct|union|enum(?:\s+(?:class|struct))?|namespace|concept)\s+"
    r"(?:(?:alignas\s*\([^)]*\)|\[\[.*?\]\]|[A-Z_][A-Z0-9_]*)\s+)*$"
)


@dataclass
class _Block:
    orow: int
    ocol: int
    crow: int
    ccol: int
    kind: str = "code"  # "code" | "class" | "enum" | "namespace"

    def contains(self, r: int, c: int) -> bool:
        return (self.orow, self.ocol) < (r, c) < (self.crow, self.ccol)


def _blocks(code: list[str]) -> list[_Block]:
    out = []
    stack = []
    for i, c in enumerate(code):
        for j, ch in enumerate(c):
            if ch == "{":
                stack.append((i, j))
            elif ch == "}" and stack:
                r, cc = stack.pop()
                out.append(_Block(r, cc, i, j))
    last = max(0, len(code) - 1)
    out += [_Block(r, cc, last, len(code[last]) if code else 0) for r, cc in stack]
    for b in out:
        head = code[b.orow][: b.ocol]
        r = b.orow
        while not head.strip() and r > 0 and b.orow - r < 3:
            r -= 1
            head = code[r] + " " + head
        if re.search(r"\benum\b", head):
            b.kind = "enum"
        elif re.search(r"\bnamespace\b", head):
            b.kind = "namespace"
        elif re.search(r"\b(class|struct|union)\b[^()]*$", head):
            b.kind = "class"
    out.sort(key=lambda b: (b.orow, b.ocol))
    return out


def _innermost(blocks: list[_Block], r: int, c: int) -> _Block | None:
    best = None
    for b in blocks:
        if (b.orow, b.ocol) > (r, c):
            break
        if b.contains(r, c):
            best = b
    return best


def _block_from(blocks: list[_Block], r: int, c: int) -> _Block | None:
    """The first block that opens at or after (r, c)."""
    for b in blocks:
        if (b.orow, b.ocol) >= (r, c):
            return b
    return None


def _typeish(before: str) -> bool:
    bs = before.rstrip()
    if not bs or bs.endswith(("::", ".", "->")) or bs[-1] not in "*&>" and not (bs[-1].isalnum() or bs[-1] == "_"):
        return False
    words = re.findall(r"\w+", bs)
    return bool(words) and words[-1] not in NOT_TYPE


def _after_paren(code: list[str], r: int, c: int) -> tuple[str, int, int] | None:
    """Text after the `)` matching the `(` at (r, c), and the `)` position."""
    depth = 0
    for i in range(r, min(len(code), r + 30)):
        line = code[i]
        for j in range(c if i == r else 0, len(line)):
            if line[j] == "(":
                depth += 1
            elif line[j] == ")":
                depth -= 1
                if depth == 0:
                    tail = line[j + 1 :]
                    k = i + 1
                    while k < len(code) and k <= i + 2:
                        tail += " " + code[k]
                        k += 1
                    return tail, i, j
    return None


def _paren_owner(before: str) -> str | None:
    """The word before the innermost unclosed `(` in `before`."""
    depth = 0
    for j in range(len(before) - 1, -1, -1):
        ch = before[j]
        if ch == ")":
            depth += 1
        elif ch == "(":
            if depth == 0:
                m = re.search(r"(\w+)\s*$", before[:j])
                return m.group(1) if m else ""
            depth -= 1
    return None


FUNC_TAIL = re.compile(
    r"^\s*(?:(?:const|volatile|noexcept(?:\s*\([^)]*\))?|override|final|mutable|&&?|throw\s*\([^)]*\)"
    r"|->\s*[\w:<>,\s*&]+?(?=[{;=]|$)|\[\[[^\]]*\]\]|[A-Z_][A-Z0-9_]*(?:\([^)]*\))?)\s*)*"
)


def _cpp_find(code: list[str], sym: Symbol, row: int | None) -> list[Definition]:
    name = sym.name
    n_rx = re.compile(rf"(?<![\w$]){re.escape(name)}(?![\w$])")
    blocks = _blocks(code)
    member = sym.access in (".", "->")
    qual = sym.qualifier if sym.access == "::" else None
    cursor = (row, sym.col) if row is not None else None
    out: list[Definition] = []

    def stmt_end(r: int, c: int) -> int:
        for i in range(r, min(len(code), r + 15)):
            if ";" in code[i][c if i == r else 0 :]:
                return i
        return r

    def block_end(r: int, c: int, fallback: int) -> int:
        b = _block_from(blocks, r, c)
        return b.crow if b is not None else fallback

    for i, c in enumerate(code):
        for m in n_rx.finditer(c):
            s, e = m.span()
            before, after = c[:s], c[e:]
            here = _innermost(blocks, i, s)
            kind = None
            if re.search(r"#\s*define\s+$", before):
                end = i
                while code[end].rstrip().endswith("\\") and end + 1 < len(code):
                    end += 1
                out.append(Definition(i, s, "macro", i, end))
                continue
            if TYPE_INTRO.search(before):
                if re.match(r"\s*;", after):
                    out.append(Definition(i, s, "declaration", i, i, tier=WEAK))
                elif re.match(r"\s*(?:final\b\s*)?(?::(?!:)[^;{]*)?(?:\{.*)?$", after) or re.match(r"\s*<", after):
                    start = i - 1 if i > 0 and code[i - 1].strip().startswith("template") else i
                    out.append(Definition(i, s, "type", start, block_end(i, e, i)))
                continue
            if re.search(r"\busing\s+$", before) and re.match(r"\s*=", after) or \
                    re.search(r"\btypedef\b", before) and re.match(r"\s*(?:\[[^\]]*\]\s*)*;", after) or \
                    re.search(r"\btypedef\b.*\(\s*\*\s*$", before):
                out.append(Definition(i, s, "typedef", i, stmt_end(i, e)))
                continue
            if re.match(r"^\s*\}\s*$", before) and re.match(r"\s*;", after):
                b = next((b for b in blocks if (b.crow, b.ccol) == (i, before.index("}"))), None)
                start = b.orow if b is not None else i
                typedef = "typedef" in " ".join(code[max(0, start - 1) : start + 1])
                out.append(Definition(i, s, "typedef" if typedef else "variable", start, i))
                continue
            if here is not None and here.kind == "enum" and re.match(r"\s*(?:=[^,}]*)?(?:,|\}|$)", after) \
                    and (not before.strip() or before.rstrip()[-1] in "{,"):
                out.append(Definition(i, s, "enumerator", i, i, sort=(here.orow,)))
                continue
            in_code = here is not None and here.kind == "code"
            if re.match(r"\s*\(", after) and not in_code:
                bs = before.rstrip().removesuffix("~").rstrip()
                qualifier = None
                while bs.endswith("::"):
                    qm = re.search(r"(\w+)\s*(?:<[^<>]*>)?\s*::$", bs)
                    if qm is None:
                        break
                    qualifier = qualifier or qm.group(1)
                    bs = bs[: qm.start()].rstrip()
                if bs.endswith((".", "->")) or "=" in bs:
                    continue
                if bs:
                    if not _typeish(bs) or bs.count("(") > bs.count(")"):
                        continue
                elif not (_indent(c) == 0 or qualifier or (here is not None and here.kind in ("class", "namespace"))):
                    continue
                paren = _after_paren(code, i, e + after.index("("))
                if paren is None:
                    continue
                tail, pr, pc = paren
                tail = tail[FUNC_TAIL.match(tail).end():]
                qmiss = qual is not None and qualifier != qual and not re.search(rf"\b{re.escape(qual)}\s*::", c)
                if tail.startswith("{") or re.match(r":(?!:)", tail) or tail.startswith("try"):
                    start = i - 1 if i > 0 and code[i - 1].strip().startswith("template") else i
                    body = _block_from(blocks, pr, pc)
                    if not tail.startswith("{"):  # skip brace initializers in a constructor's init list
                        for b in blocks:
                            prev = code[b.orow][: b.ocol].rstrip()
                            if (b.orow, b.ocol) > (pr, pc) and (not prev or prev[-1] in ")}" or prev.endswith("try")):
                                body = b
                                break
                    out.append(Definition(i, s, "function", start, body.crow if body else i, sort=(qmiss,)))
                elif tail.startswith(("=", ";")):
                    out.append(Definition(i, s, "declaration", i, stmt_end(pr, pc), tier=WEAK, sort=(qmiss,)))
                continue
            binding = re.search(r"\bauto\s*&{0,2}\s*\[[^\]]*$", before)  # auto [a, b] = ...
            if re.match(r"\s*(?:\[|=(?!=)|;|,|\)|\{|:(?!:)|\()", after) and (_typeish(before) or binding):
                kind = "variable"
            if kind is None or in_code and not binding and re.search(r"(?<![=!<>])=(?!=)", before):
                continue  # part of an expression
            scope = here
            param = re.match(r"\s*[,)]", after) is not None and not binding
            if param and _paren_owner(before) in ("if", "while", "switch", "for", "return", "sizeof", "decltype",
                                                  "alignof", "static_assert", "assert", "noexcept"):
                continue  # `if (a & b)`: an expression, not a parameter
            if param:  # a parameter: its scope is the function body that follows
                scope = None
                for k in range(i, min(len(code), i + 10)):
                    text = code[k][e if k == i else 0 :]
                    brace, semi = text.find("{"), text.find(";")
                    if brace >= 0 and (semi < 0 or brace < semi):
                        scope = _block_from(blocks, k, (e if k == i else 0) + brace)
                        break
                    if semi >= 0:
                        scope = "nowhere"
                        break
                kind = "parameter"
            d = Definition(i, s, kind, i, i if param else stmt_end(i, e))
            if scope == "nowhere":
                d.tier = UNLIKELY
            elif scope is None or scope.kind == "namespace":
                d.tier = DEFINITION
            elif member:
                d.tier = DEFINITION if scope.kind == "class" else UNLIKELY
            elif cursor is not None and scope.contains(*cursor) and (i, s) <= cursor:
                d.tier = LOCAL
                d.sort = (-scope.orow, -scope.ocol, -i)
            else:
                d.tier = OTHER if scope.kind == "class" else UNLIKELY
            out.append(d)
    if member:
        for d in out:
            if d.kind in ("macro", "typedef", "type", "enumerator"):
                d.tier = max(d.tier, OTHER)
    return out


C_HEADER_EXTS = (".h", ".hh", ".hpp", ".hxx", ".inl", ".ipp")
C_SOURCE_EXTS = (".c", ".cc", ".cpp", ".cxx", ".m", ".mm", ".cu")


def cpp_related_files(lines: list[str], path: str | None, cwd: str) -> list[str]:
    """Files a C/C++ definition may be in: `#include "..."` targets and the
    matching header/source file (foo.h <-> foo.cpp)."""
    here = os.path.dirname(os.path.abspath(path)) if path else cwd
    out: list[str] = []
    if path:
        stem, ext = os.path.splitext(os.path.abspath(path))
        for e in C_SOURCE_EXTS if ext.lower() in C_HEADER_EXTS else C_HEADER_EXTS:
            if os.path.isfile(stem + e):
                out.append(stem + e)
    for line in lines:
        m = re.match(r'\s*#\s*(?:include|import)\s*"([^"]+)"', line)
        if not m:
            continue
        for base in (here, cwd, os.path.join(cwd, "include")):
            cand = os.path.normpath(os.path.join(base, m.group(1)))
            if os.path.isfile(cand):
                if cand not in out:
                    out.append(cand)
                break
    return out


# ================================================================== the panel


@dataclass
class PanelRow:
    number: int | None  # 1-based line number; None for headings
    text: str
    spans: list[Span]
    current: bool = False  # the defining line itself


@dataclass
class DefinitionPanel:
    title: str
    rows: list[PanelRow]
    scroll: int = 0


def build_panel(title: str, lines: list[str], spans, d: Definition, limit: int = 400) -> DefinitionPanel:
    """Rows for the definition panel. `spans(row)` gives highlight spans."""

    def section(start: int, end: int) -> list[PanelRow]:
        rows = []
        for r in range(start, min(end, start + limit - 1) + 1):
            rows.append(PanelRow(r + 1, lines[r], spans(r), r == d.row))
        if end - start + 1 > limit:
            rows.append(PanelRow(None, f"… {end - start + 1 - limit} more lines", []))
        return rows

    rows = section(d.start, d.end)
    if d.related:
        rows.append(PanelRow(None, "", []))
        rows.append(PanelRow(None, "referenced:", []))
        for s, e in d.related:
            rows += section(s, e)
    return DefinitionPanel(title, rows)


class Source:
    """A file to search: an open document or a file read from disk."""

    def __init__(self, label: str, lines: list[str], lang: Language, doc=None, path: str | None = None):
        self.label = label
        self.lines = lines
        self.lang = lang
        self.doc = doc
        self.path = path
        self._code: list[str] | None = None
        self._spans: list[list[Span]] | None = None

    def code(self) -> list[str]:
        if self._code is None:
            self._code = code_lines(self.lines, self.lang, self.spans)
        return self._code

    def spans(self, row: int) -> list[Span]:
        if self.doc is not None:
            return self.doc.highlighter.spans(row)
        if self._spans is None:
            tok = Tokenizer(self.lang)
            state = None
            self._spans = []
            for line in self.lines:
                sp, state = tok.tokenize(line, state)
                self._spans.append(sp)
        return self._spans[row]

    def find(self, sym: Symbol, row: int | None) -> list[Definition]:
        return find_definitions(self.lines, self.code(), self.lang, sym, row)
