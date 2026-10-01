"""jump-to-definition / show-definition for LLVM IR, Python and C++."""

import os

from conftest import editor_with

from troll import definitions as defs
from troll import languages
from troll.view import build_frame

LL = """\
%struct.Point = type { i32, i32 }
@g = global i32 0, align 4

define i32 @add(i32 %a, i32 %b) #0 !dbg !10 {
entry:
  %sum = add nsw i32 %a, %b, !dbg !15
  br label %end

end:
  ret i32 %sum
}

declare i32 @ext(i32)

define i32 @main() {
  %sum = call i32 @add(i32 1, i32 2)
  %p = alloca %struct.Point
  %r = call i32 @ext(i32 %sum)
  ret i32 %r
}

attributes #0 = { noinline }

!0 = distinct !DICompileUnit(language: DW_LANG_C11, file: !1)
!1 = !DIFile(filename: "t.c", directory: "/tmp")
!10 = distinct !DISubprogram(name: "add", scope: !1, unit: !0)
!15 = !DILocation(line: 4, scope: !10)
"""


def find(text, lang_name, needle, occurrence=0, offset=1):
    lang = languages.BY_NAME[lang_name]
    lines = text.split("\n")
    hits = [(r, c) for r, line in enumerate(lines) for c in range(len(line)) if line.startswith(needle, c)]
    r, c = hits[occurrence]
    sym = defs.symbol_at(lines, lang, r, c + offset)
    found = defs.find_definitions(lines, defs.code_lines(lines, lang), lang, sym, r)
    return sym, found, lines


def target(text, lang_name, needle, occurrence=0, offset=1):
    _sym, found, lines = find(text, lang_name, needle, occurrence, offset)
    assert found, needle
    d = found[0]
    return lines[d.row].strip(), d.kind


def jump(ed, doc, needle, occurrence=0, offset=1):
    hits = [(r, c) for r, line in enumerate(doc.lines) for c in range(len(line)) if line.startswith(needle, c)]
    r, c = hits[occurrence]
    doc.goto(r, c + offset)
    ed.keys("M-f")
    return ed.doc.lines[ed.doc.cursor[0]].strip()


# ------------------------------------------------------------------ LLVM IR


def test_llvm_values_are_resolved_within_their_function():
    assert target(LL, "llvm", "%sum)")[0].startswith("%sum = call")  # @main's %sum, not @add's
    assert target(LL, "llvm", "ret i32 %sum", offset=9)[0].startswith("%sum = add")
    assert target(LL, "llvm", "%a, %b") == ("define i32 @add(i32 %a, i32 %b) #0 !dbg !10 {", "argument")
    assert target(LL, "llvm", "%end") == ("end:", "label")


def test_llvm_globals_types_attributes():
    assert target(LL, "llvm", "@add(i32 1")[1] == "function"
    assert target(LL, "llvm", "@ext(i32 %sum")[1] == "declaration"
    assert target(LL, "llvm", "%struct.Point", occurrence=1)[1] == "type"
    assert target(LL, "llvm", "#0 !dbg")[0] == "attributes #0 = { noinline }"


def test_llvm_metadata_follows_references():
    sym, found, lines = find(LL, "llvm", "!15")
    assert sym.name == "!15"
    d = found[0]
    assert lines[d.row].startswith("!15 = !DILocation")
    related = [lines[s] for s, _e in d.related]
    assert related[0].startswith("!10 = ") and any(r.startswith("!1 = ") for r in related)
    assert any(r.startswith("!0 = ") for r in related)
    # a node type is not a reference
    lines = LL.split("\n")
    r = next(i for i, line in enumerate(lines) if "DILocation" in line)
    assert defs.symbol_at(lines, languages.BY_NAME["llvm"], r, lines[r].index("DILocation")) is None


def test_show_definition_panel_for_metadata(editor):
    doc = editor_with(editor, LL, path="t.ll")
    r = next(i for i, line in enumerate(doc.lines) if "!dbg !15" in line)
    doc.goto(r, doc.lines[r].index("!15") + 1)
    editor.keys("M-v")
    frame = build_frame(editor, 30, 140).text().split("\n")
    assert "!15 · metadata" in frame[1] and "│" in frame[1]
    right = [line.split("│", 1)[1] for line in frame[1:28] if "│" in line]
    assert any("!15 = !DILocation" in line for line in right)
    assert any("referenced:" in line for line in right)
    assert any("!10 = distinct !DISubprogram" in line for line in right)
    assert frame[r + 1].split("│")[0].rstrip().endswith("!dbg !15")  # the file is still on the left
    y, x = build_frame(editor, 30, 140).cursor
    assert x < 140 - defs_panel_width(140)
    editor.keys("Esc")
    assert editor.definition is None and "│" not in build_frame(editor, 30, 140).text().split("\n")[1]


def defs_panel_width(width):
    from troll.view import panel_width

    return panel_width(width)


def test_jump_and_jump_back(editor):
    doc = editor_with(editor, LL, path="t.ll")
    assert jump(editor, doc, "@add(i32 1").startswith("define i32 @add")
    assert "M-B jumps back" in editor.message.text
    editor.keys("M-b")
    assert doc.lines[doc.cursor[0]].strip().startswith("%sum = call i32 @add")
    editor.keys("M-b")
    assert "No earlier position" in editor.message.text


def test_jump_on_the_definition_itself(editor):
    doc = editor_with(editor, LL, path="t.ll")
    doc.goto(doc.lines.index("!10 = distinct !DISubprogram(name: \"add\", scope: !1, unit: !0)"), 1)
    editor.keys("M-f")
    assert "This is the definition of !10" in editor.message.text


def test_unsupported_language_and_no_symbol(editor):
    editor_with(editor, "hello world\n", path="t.txt")
    editor.run_line("jump-to-definition")
    assert "LLVM IR, Python and C/C++" in editor.message.text
    doc = editor_with(editor, "x = 1\n\n", path="t.py")
    doc.goto(1, 0)
    editor.run_line("show-definition")
    assert "No symbol" in editor.message.text


# ------------------------------------------------------------------- Python

PY = """\
import os
from helpers import util as u


class Shape:
    sides = 0

    def __init__(self, name, size=1):
        self.name = name

    def area(self):
        return self.size

    def describe(self):
        total = self.area()
        for i, part in enumerate(self.name):
            total += i
        return part, total


def make(kind, *args):
    shape = Shape(kind)
    size = 3
    if (n := len(args)) > 0:
        size = n
    return shape.describe(), size, u, os


def other():
    size = 10
    return size
"""


def test_python_scopes_and_bindings():
    assert target(PY, "python", "Shape(kind") == ("class Shape:", "class")
    assert target(PY, "python", "kind)") == ("def make(kind, *args):", "parameter")
    assert target(PY, "python", "size, u") == ("size = n", "variable")  # the latest assignment before
    assert target(PY, "python", "return size", offset=8) == ("size = 10", "variable")  # other()'s own
    assert target(PY, "python", "part, total") == ("for i, part in enumerate(self.name):", "variable")
    assert target(PY, "python", "size = n", offset=7) == ("if (n := len(args)) > 0:", "variable")
    assert target(PY, "python", "u, os") == ("from helpers import util as u", "import")


def test_python_attributes():
    assert target(PY, "python", "self.area", offset=6) == ("def area(self):", "function")
    assert target(PY, "python", "self.name)", offset=6) == ("self.name = name", "variable")
    assert target(PY, "python", "shape.describe", offset=7) == ("def describe(self):", "function")


def test_python_follows_imports_into_modules(editor):
    root = editor.cwd
    os.makedirs(os.path.join(root, "pkg"))
    open(os.path.join(root, "pkg", "__init__.py"), "w").close()
    with open(os.path.join(root, "pkg", "sub.py"), "w") as f:
        f.write("from .inner import CONST\n\n\ndef thing():\n    return CONST\n")
    with open(os.path.join(root, "pkg", "inner.py"), "w") as f:
        f.write("CONST = 42\n")
    doc = editor_with(editor, "from pkg.sub import thing, CONST\nfrom pkg import sub\n\nthing(CONST, sub.thing)\n",
                      path="main.py")
    assert jump(editor, doc, "thing(") == "def thing():"
    assert editor.doc.path.endswith(os.path.join("pkg", "sub.py"))
    editor.keys("M-b")
    assert editor.doc is doc
    assert jump(editor, doc, "CONST,") == "CONST = 42"  # re-exported by pkg.sub
    editor.keys("M-b")
    assert jump(editor, doc, "sub.thing", offset=5) == "def thing():"  # module.attr
    editor.keys("M-b")
    assert jump(editor, doc, "sub.", offset=1) == "from .inner import CONST"  # a submodule: its top


# ---------------------------------------------------------------------- C++

HEADER = """\
#pragma once
#define SQUARE(x) ((x) * (x))

namespace geo {
enum class Color { Red, Green = 2, Blue };
struct Point;

class Shape : public Base {
public:
  Shape(int sides);
  int area() const;
  int sides_;
};

typedef std::vector<Shape> Shapes;
using Id = unsigned long;
int helper(int a, int b);
}
"""

SOURCE = """\
#include "shape.h"

namespace geo {
static int counter = 0;

Shape::Shape(int sides) : sides_(sides), color{Color::Green} {
  counter++;
}

int Shape::area() const {
  int result = SQUARE(sides_);
  for (int i = 0; i < 16; ++i) {
    int result = i;
    result += helper(i, result);
  }
  if (result & counter) {
    return result;
  }
  return result;
}

int helper(int a, int b) {
  Id id = a;
  Shapes all;
  Point *p = nullptr;
  return a + b + (int)id + all.size();
}
}
"""


def test_cpp_locals_shadowing_and_parameters():
    assert target(SOURCE, "cpp", "result += ") == ("int result = i;", "variable")
    assert target(SOURCE, "cpp", "result & ") == ("int result = SQUARE(sides_);", "variable")
    assert target(SOURCE, "cpp", "sides)") == ("Shape::Shape(int sides) : sides_(sides), color{Color::Green} {",
                                               "parameter")
    assert target(SOURCE, "cpp", "a + b") == ("int helper(int a, int b) {", "parameter")
    assert target(SOURCE, "cpp", "counter++") == ("static int counter = 0;", "variable")
    assert target(SOURCE, "cpp", "helper(i") == ("int helper(int a, int b) {", "function")
    # the & in `if (result & counter)` isn't a parameter declaration
    _sym, found, _lines = find(SOURCE, "cpp", "counter) {")
    assert found[0].kind == "variable" and found[0].row == 3


def test_cpp_header_kinds():
    assert target(HEADER, "cpp", "Color {", offset=0) == ("enum class Color { Red, Green = 2, Blue };", "type")
    assert target(HEADER, "cpp", "Green")[1] == "enumerator"
    assert target(HEADER, "cpp", "Shapes;")[1] == "typedef"
    assert target(HEADER, "cpp", "Id =")[1] == "typedef"
    assert target(HEADER, "cpp", "SQUARE")[1] == "macro"
    assert target(HEADER, "cpp", "Point;")[1] == "declaration"
    _sym, found, lines = find(HEADER, "cpp", "Shape :")
    assert found[0].kind == "type" and lines[found[0].end] == "};"


def test_cpp_jumps_between_header_and_source(editor):
    with open(os.path.join(editor.cwd, "shape.h"), "w") as f:
        f.write(HEADER)
    with open(os.path.join(editor.cwd, "shape.cpp"), "w") as f:
        f.write(SOURCE)
    doc = editor.open_file("shape.cpp")
    assert jump(editor, doc, "SQUARE") == "#define SQUARE(x) ((x) * (x))"
    assert editor.doc.path.endswith("shape.h")
    header = editor.doc
    # from the declaration in the header to the definition in the .cpp
    assert jump(editor, header, "helper") == "int helper(int a, int b) {"
    assert editor.doc is doc
    # and back from the definition to the declaration
    assert jump(editor, doc, "area() const {") == "int area() const;"
    editor.keys("M-b")
    assert jump(editor, doc, "Shapes all") == "typedef std::vector<Shape> Shapes;"
    editor.keys("M-b")
    assert jump(editor, doc, "sides_(s", offset=1) == "int sides_;"


def test_show_definition_through_the_palette(editor):
    doc = editor_with(editor, SOURCE, path="x.cpp")
    r = next(i for i, line in enumerate(doc.lines) if "helper(i" in line)
    doc.goto(r, doc.lines[r].index("helper") + 2)
    editor.keys("C-t")
    editor.type("show-definition")
    editor.keys("Enter")
    text = build_frame(editor, 20, 120).text()
    assert "helper · function · x.cpp:22" in text
    assert "Point *p = nullptr;" in text  # the whole body is shown
    editor.run_line("hide-definition")
    assert editor.definition is None


def test_panel_scrolls(editor):
    body = "".join(f"  x{i} = {i}\n" for i in range(80))
    doc = editor_with(editor, f"def big():\n{body}    return 1\n\nbig()\n", path="t.py")
    doc.goto(len(doc.lines) - 2, 1)
    editor.keys("M-v")
    text = build_frame(editor, 20, 120).text()
    assert "more lines (M-PgDn)" in text and "x70" not in text
    editor.keys("M-PageDown", "M-PageDown", "M-PageDown", "M-PageDown", "M-PageDown")
    assert "x70" in build_frame(editor, 20, 120).text()
    editor.keys("M-PageUp")
    assert editor.definition.scroll > 0


def test_definitions_in_commit_mode(repo):
    from troll.editor import Editor
    from troll.settings import Settings

    repo.commit("base", {"a.py": "def f():\n    return 1\n"})
    target_rev = repo.commit("use", {"a.py": "def f():\n    return 1\n\n\nx = f()\n"})
    ed = Editor(Settings(), cwd=repo.path, raise_errors=True)
    ed.open_commit(target_rev)
    ed.open_entry(1)
    doc = ed.doc
    assert jump(ed, doc, "f()", occurrence=1, offset=0) == "def f():"
    ed.keys("M-v")
    assert "f · function · a.py:1" in build_frame(ed, 30, 160).text()
