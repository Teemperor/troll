"""Language definitions used for highlighting and language-aware formatting.

A language is a set of single-line token `rules` (regex -> token name) plus
`regions` (strings, block comments, ...) that may span multiple lines. The
tokenizer in highlight.py turns these into spans.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Region:
    token: str
    start: str
    end: str
    escape: str | None = None
    multiline: bool = True


@dataclass
class Language:
    name: str
    extensions: tuple[str, ...] = ()
    filenames: tuple[str, ...] = ()
    shebang: str | None = None
    rules: tuple[tuple[str | None, str], ...] = ()
    regions: tuple[Region, ...] = ()
    line_comment: str | None = None
    block_comment: tuple[str, str] | None = None
    indent_after: str | None = None  # regex matched against code before the cursor
    dedent_after: str | None = None  # regex: the line after this one is dedented
    tab_size: int | None = None
    use_tabs: bool | None = None
    pairs: tuple[str, ...] = ("()", "[]", "{}", '""', "''")
    formatters: tuple[tuple[str, ...], ...] = ()
    first_line_token: str | None = None
    first_line_limit: int = 0  # characters beyond this on the first line are flagged
    list_continuation: bool = False
    comment_prefixes: tuple[str, ...] = field(default=())

    def all_comment_prefixes(self) -> tuple[str, ...]:
        found = list(self.comment_prefixes)
        if self.line_comment:
            found.append(self.line_comment)
        return tuple(found)


def kw(*words: str) -> str:
    return r"\b(?:" + "|".join(sorted(words, key=len, reverse=True)) + r")\b"


NUMBER = r"\b(?:0[xX][0-9a-fA-F_']+|0[bB][01_']+|0[oO][0-7_]+|\d[\d_']*(?:\.[\d_]*)?(?:[eE][+-]?\d+)?)[uUlLfFjJ]*\b|(?<![\w.])\.\d+\b"
FUNCTION = r"\b[A-Za-z_]\w*(?=\s*\()"
IDENT = r"[A-Za-z_]\w*"
DQ = Region("string", r'"', r'"', r"\\.", multiline=False)
SQ = Region("string", r"'", r"'", r"\\.", multiline=False)
C_BLOCK = Region("comment", r"/\*", r"\*/")
C_INDENT = r"[{\[(]\s*$"


def c_like(
    name: str,
    extensions: tuple[str, ...],
    keywords: tuple[str, ...],
    types: tuple[str, ...],
    constants: tuple[str, ...],
    *,
    extra_rules: tuple[tuple[str | None, str], ...] = (),
    extra_regions: tuple[Region, ...] = (),
    strings: tuple[Region, ...] = (DQ, SQ),
    builtins: tuple[str, ...] = (),
    **kwargs,
) -> Language:
    rules: list[tuple[str | None, str]] = [("comment", r"//.*")]
    rules += list(extra_rules)
    rules += [("keyword", kw(*keywords)), ("type", kw(*types)), ("constant", kw(*constants))]
    if builtins:
        rules.append(("builtin", kw(*builtins)))
    rules += [("number", NUMBER), ("function", FUNCTION), (None, IDENT)]
    kwargs.setdefault("indent_after", C_INDENT)
    return Language(
        name=name,
        extensions=extensions,
        rules=tuple(rules),
        regions=(C_BLOCK, *extra_regions, *strings),
        line_comment="//",
        block_comment=("/*", "*/"),
        **kwargs,
    )


C_KEYWORDS = (
    "auto break case const continue default do else enum extern for goto if inline register "
    "restrict return sizeof static struct switch typedef union volatile while _Atomic _Bool "
    "_Generic _Noreturn _Static_assert _Thread_local"
).split()
CPP_KEYWORDS = C_KEYWORDS + (
    "alignas alignof and asm catch class constexpr consteval constinit co_await co_return "
    "co_yield decltype delete explicit export final friend mutable namespace new noexcept not "
    "operator or override private protected public requires static_assert static_cast "
    "dynamic_cast reinterpret_cast const_cast template this throw try typeid typename using "
    "virtual concept"
).split()
C_TYPES = (
    "void char short int long float double signed unsigned bool size_t ssize_t ptrdiff_t "
    "int8_t int16_t int32_t int64_t uint8_t uint16_t uint32_t uint64_t intptr_t uintptr_t "
    "wchar_t char8_t char16_t char32_t FILE id BOOL NSString NSInteger NSUInteger"
).split()

PREPROC = (
    ("preproc", r'^\s*#\s*(?:include|import)\s*(?:<[^>]*>|"[^"]*")'),
    ("preproc", r"^\s*#\s*\w+"),
    ("keyword", r"@\w+"),  # Objective-C
)

LANGUAGES: list[Language] = [
    Language(
        name="python",
        extensions=(".py", ".pyw", ".pyi"),
        filenames=("SConstruct", "SConscript"),
        shebang=r"python",
        regions=(
            Region("string", r"""(?:\b[rRbBuUfF]{1,2})?\"\"\"""", r'"""', r"\\."),
            Region("string", r"(?:\b[rRbBuUfF]{1,2})?'''", r"'''", r"\\."),
            Region("string", r'(?:\b[rRbBuUfF]{1,2})?"', r'"', r"\\.", multiline=False),
            Region("string", r"(?:\b[rRbBuUfF]{1,2})?'", r"'", r"\\.", multiline=False),
        ),
        rules=(
            ("comment", r"#.*"),
            ("decorator", r"(?<![\w)\]])@[\w.]+"),
            (
                "keyword",
                kw(*"and as assert async await break class continue def del elif else except "
                   "finally for from global if import in is lambda nonlocal not or pass raise "
                   "return try while with yield match case".split()),
            ),
            ("constant", kw("True", "False", "None", "NotImplemented", "Ellipsis", "__name__", "__file__")),
            (
                "builtin",
                kw(*"self cls print len range str int float bool list dict set tuple object type "
                   "isinstance issubclass super open enumerate zip map filter sorted reversed min "
                   "max sum any all abs repr hash iter next getattr setattr hasattr delattr "
                   "Exception ValueError TypeError KeyError IndexError RuntimeError OSError "
                   "StopIteration AttributeError NotImplementedError bytes bytearray frozenset "
                   "property staticmethod classmethod callable format input vars dir id".split()),
            ),
            ("number", NUMBER),
            ("function", FUNCTION),
            (None, IDENT),
        ),
        line_comment="#",
        indent_after=r"[:{\[(]\s*$",
        dedent_after=r"^\s*(return|pass|break|continue|raise)\b.*$",
        formatters=(("ruff", "format", "-"), ("black", "-q", "-")),
    ),
    c_like(
        "c",
        (".c", ".h", ".m"),
        tuple(C_KEYWORDS),
        tuple(C_TYPES),
        ("true", "false", "NULL", "nil", "YES", "NO"),
        extra_rules=PREPROC,
        formatters=(("clang-format", "--assume-filename={path}"),),
    ),
    c_like(
        "cpp",
        (".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx", ".mm", ".ipp", ".inl", ".cu"),
        tuple(CPP_KEYWORDS),
        tuple(C_TYPES) + ("string", "vector", "map", "unique_ptr", "shared_ptr", "optional"),
        ("true", "false", "NULL", "nullptr", "nil", "YES", "NO"),
        extra_rules=PREPROC + (("type", r"\bstd::\w+"),),
        extra_regions=(Region("string", r'R"\(', r'\)"'),),
        formatters=(("clang-format", "--assume-filename={path}"),),
    ),
    c_like(
        "javascript",
        (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".mts"),
        tuple(
            "break case catch class const continue debugger default delete do else export extends "
            "finally for function if import in instanceof let new of return super switch this throw "
            "try typeof var void while with yield async await static get set from as interface type "
            "enum implements namespace declare readonly private public protected abstract keyof "
            "infer is satisfies".split()
        ),
        tuple("string number boolean any unknown never object symbol bigint".split()),
        ("true", "false", "null", "undefined", "NaN", "Infinity"),
        extra_regions=(Region("string", r"`", r"`", r"\\."),),
        shebang=r"node|deno|bun",
        builtins=tuple("console window document Math JSON Promise Array Object String Number Map Set Error require module exports".split()),
        tab_size=2,
        formatters=(("prettier", "--stdin-filepath", "{path}"),),
    ),
    c_like(
        "rust",
        (".rs",),
        tuple(
            "as async await break const continue crate dyn else enum extern fn for if impl in let "
            "loop match mod move mut pub ref return self Self static struct super trait type unsafe "
            "use where while".split()
        ),
        tuple(
            "i8 i16 i32 i64 i128 isize u8 u16 u32 u64 u128 usize f32 f64 bool char str String Vec "
            "Option Result Box Rc Arc HashMap HashSet".split()
        ),
        ("true", "false", "None", "Some", "Ok", "Err"),
        extra_rules=(
            ("decorator", r"#!?\[[^\]]*\]"),
            ("string", r"'(?:\\.|[^\\'])'"),
            ("type", r"'[A-Za-z_]\w*\b"),
            ("builtin", r"\b[A-Za-z_]\w*!"),
        ),
        strings=(DQ,),
        pairs=("()", "[]", "{}", '""'),
        formatters=(("rustfmt", "--emit", "stdout", "--quiet"),),
    ),
    c_like(
        "go",
        (".go",),
        tuple(
            "break case chan const continue default defer else fallthrough for func go goto if "
            "import interface map package range return select struct switch type var".split()
        ),
        tuple(
            "bool byte complex64 complex128 error float32 float64 int int8 int16 int32 int64 rune "
            "string uint uint8 uint16 uint32 uint64 uintptr any".split()
        ),
        ("true", "false", "nil", "iota"),
        builtins=tuple("append cap close copy delete len make new panic print println recover".split()),
        extra_regions=(Region("string", r"`", r"`"),),
        use_tabs=True,
        formatters=(("gofmt",),),
    ),
    c_like(
        "java",
        (".java", ".kt", ".kts", ".scala", ".cs", ".dart"),
        tuple(
            "abstract assert break case catch class continue default do else enum extends final "
            "finally for if implements import instanceof interface native new package private "
            "protected public return static strictfp super switch synchronized this throw throws "
            "transient try var val fun void volatile while record yield sealed permits override "
            "namespace using".split()
        ),
        tuple("boolean byte char short int long float double String Object Integer List Map".split()),
        ("true", "false", "null"),
        extra_rules=(("decorator", r"@\w+"),),
    ),
    c_like(
        "swift",
        (".swift",),
        tuple(
            "associatedtype class deinit enum extension fileprivate func import init inout internal "
            "let open operator private protocol public rethrows static struct subscript typealias "
            "var break case continue default defer do else fallthrough for guard if in repeat "
            "return switch where while as catch is super self Self throw throws try async await "
            "actor some any nonisolated mutating override final lazy weak unowned convenience "
            "required dynamic".split()
        ),
        tuple("Int Double Float Bool String Character Array Dictionary Set Optional Void UInt".split()),
        ("true", "false", "nil"),
        extra_rules=(("decorator", r"@\w+"),),
        extra_regions=(Region("string", r'"""', r'"""', r"\\."),),
        strings=(DQ,),
        pairs=("()", "[]", "{}", '""'),
        formatters=(("swift-format",),),
    ),
    Language(
        name="shell",
        extensions=(".sh", ".bash", ".zsh", ".ksh", ".fish"),
        filenames=(".bashrc", ".zshrc", ".profile", ".bash_profile", ".zprofile", "PKGBUILD"),
        shebang=r"\b(?:ba|z|k|da|fi)?sh\b",
        regions=(
            Region("string", r'"', r'"', r"\\."),
            Region("string", r"'", r"'"),
        ),
        rules=(
            ("variable", r"\$\{[^}]*\}|\$\(|\$[\w@#?$!*-]"),
            ("comment", r"(?:^|(?<=[\s;]))#.*"),
            (
                "keyword",
                kw(*"if then else elif fi case esac for select while until do done in function time "
                   "coproc return exit break continue local export readonly declare typeset unset "
                   "shift source alias".split()),
            ),
            ("builtin", kw(*"echo printf cd pwd test read eval exec set trap wait kill true false".split())),
            ("function", r"^\s*[\w-]+(?=\s*\(\))"),
            ("number", r"\b\d+\b"),
            (None, r"[\w-]+"),
        ),
        line_comment="#",
        indent_after=r"(\bthen|\bdo|\belse|[{(]|\bin)\s*$",
        pairs=("()", "[]", "{}", '""', "''"),
        formatters=(("shfmt",),),
    ),
    Language(
        name="make",
        extensions=(".mk", ".make"),
        filenames=("Makefile", "makefile", "GNUmakefile"),
        rules=(
            ("comment", r"#.*"),
            ("variable", r"\$\([^)]*\)|\$\{[^}]*\}|\$[@<^+?*%]"),
            ("keyword", kw(*"ifeq ifneq ifdef ifndef else endif include define endef export override vpath".split())),
            ("function", r"^[\w./%$(){} -]+(?=\s*::?(?!=))"),
        ),
        regions=(DQ, SQ),
        line_comment="#",
        use_tabs=True,
        tab_size=8,
    ),
    Language(
        name="json",
        extensions=(".json", ".jsonc", ".json5", ".geojson"),
        filenames=(".babelrc", ".eslintrc"),
        rules=(
            ("key", r'"(?:\\.|[^"\\])*"(?=\s*:)'),
            ("string", r'"(?:\\.|[^"\\])*"'),
            ("constant", kw("true", "false", "null")),
            ("number", r"-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b"),
            ("comment", r"//.*"),
        ),
        regions=(C_BLOCK,),
        indent_after=C_INDENT,
        pairs=("()", "[]", "{}", '""'),
        tab_size=2,
        formatters=(("python3", "-m", "json.tool", "--indent", "2"),),
    ),
    Language(
        name="yaml",
        extensions=(".yaml", ".yml"),
        filenames=(".clang-format",),
        rules=(
            ("comment", r"(?:^|(?<=\s))#.*"),
            ("preproc", r"^(?:---|\.\.\.)\s*$"),
            ("key", r"""^\s*(?:-\s+)?[^\s#'"{}\[\],][^#:]*?(?=\s*:(?:\s|$))"""),
            ("variable", r"[&*][\w-]+"),
            ("constant", kw("true", "false", "null", "yes", "no", "on", "off", "True", "False")),
            ("number", r"\b\d+(?:\.\d+)?\b"),
        ),
        regions=(DQ, Region("string", r"'", r"'", multiline=False)),
        line_comment="#",
        indent_after=r":\s*$",
        tab_size=2,
    ),
    Language(
        name="toml",
        extensions=(".toml",),
        filenames=("Cargo.lock", "Pipfile"),
        regions=(
            Region("string", r'"""', r'"""', r"\\."),
            Region("string", r"'''", r"'''"),
            DQ,
            Region("string", r"'", r"'", multiline=False),
        ),
        rules=(
            ("comment", r"#.*"),
            ("heading", r"^\s*\[\[?[^\]]*\]\]?"),
            ("key", r"""^\s*[\w.\-"']+(?=\s*=)"""),
            ("constant", kw("true", "false")),
            ("number", r"\b\d[\d_]*(?:\.\d+)?\b"),
        ),
        line_comment="#",
    ),
    Language(
        name="ini",
        extensions=(".ini", ".cfg", ".conf", ".properties", ".gitconfig", ".editorconfig"),
        filenames=(".gitconfig", ".gitmodules", ".editorconfig", "config"),
        rules=(
            ("comment", r"^\s*[;#].*"),
            ("heading", r"^\s*\[[^\]]*\]"),
            ("key", r"^\s*[^=;#\s\[][^=]*?(?=\s*=)"),
            ("constant", kw("true", "false", "yes", "no", "on", "off")),
            ("number", r"\b\d+\b"),
        ),
        regions=(DQ,),
        line_comment="#",
    ),
    Language(
        name="markdown",
        extensions=(".md", ".markdown", ".mdown", ".rst"),
        filenames=("README", "CHANGELOG"),
        regions=(Region("code", r"^\s*```.*", r"^\s*```\s*$"),),
        rules=(
            ("heading", r"^#{1,6}\s.*"),
            ("comment", r"^\s*>.*"),
            ("preproc", r"^\s*(?:[-*+]|\d+[.)])(?=\s)"),
            ("code", r"`[^`]+`"),
            ("keyword", r"\*\*[^*]+\*\*|__[^_]+__"),
            ("emph", r"(?<![*\w])\*[^*\s][^*]*\*(?!\*)|(?<![_\w])_[^_\s][^_]*_(?!\w)"),
            ("link", r"!?\[[^\]]*\]\([^)]*\)|<https?://[^>]*>|https?://\S+"),
            ("preproc", r"^\s*(?:---+|\*\*\*+|===+)\s*$"),
        ),
        pairs=("()", "[]", "``"),
        list_continuation=True,
    ),
    Language(
        name="diff",
        extensions=(".diff", ".patch", ".rej"),
        rules=(
            ("diff_header", r"^(?:diff |index |--- |\+\+\+ |new file|deleted file|similarity|rename |old mode|new mode).*"),
            ("diff_hunk", r"^@@.*"),
            ("diff_add", r"^\+.*"),
            ("diff_del", r"^-.*"),
            ("comment", r"^\\.*"),
        ),
        pairs=(),
    ),
    Language(
        name="html",
        extensions=(".html", ".htm", ".xml", ".xhtml", ".svg", ".plist", ".vue", ".xsd", ".storyboard", ".xib"),
        regions=(
            Region("comment", r"<!--", r"-->"),
            Region("preproc", r"<!\[CDATA\[", r"\]\]>"),
            Region("string", r'(?<==)"', r'"'),
            Region("string", r"(?<==)'", r"'"),
        ),
        rules=(
            ("preproc", r"<[!?][^>]*>"),
            ("tag", r"</?[\w:.-]+|/?>"),
            ("attr", r"\b[\w:-]+(?==)"),
            ("constant", r"&[#\w]+;"),
        ),
        block_comment=("<!--", "-->"),
        indent_after=r"<[\w][^/>]*>\s*$|[{(\[]\s*$",
        pairs=("()", "[]", "{}", '""', "<>"),
        tab_size=2,
    ),
    Language(
        name="css",
        extensions=(".css", ".scss", ".sass", ".less"),
        regions=(C_BLOCK, DQ, SQ),
        rules=(
            ("comment", r"(?<!:)//.*"),
            ("keyword", r"@[\w-]+|!important"),
            ("key", r"[\w-]+(?=\s*:[^:])"),
            ("number", r"#[0-9a-fA-F]{3,8}\b|-?\b\d+(?:\.\d+)?(?:px|em|rem|%|vh|vw|s|ms|deg|pt)?"),
            ("variable", r"--[\w-]+|\$[\w-]+"),
            ("function", FUNCTION),
            ("tag", r"[.#][\w-]+"),
        ),
        block_comment=("/*", "*/"),
        indent_after=C_INDENT,
        tab_size=2,
    ),
    Language(
        name="lua",
        extensions=(".lua",),
        shebang=r"lua",
        regions=(
            Region("comment", r"--\[\[", r"\]\]"),
            Region("string", r"\[\[", r"\]\]"),
            DQ,
            SQ,
        ),
        rules=(
            ("comment", r"--.*"),
            (
                "keyword",
                kw(*"and break do else elseif end for function goto if in local not or repeat return then until while".split()),
            ),
            ("constant", kw("true", "false", "nil")),
            ("number", NUMBER),
            ("function", FUNCTION),
            (None, IDENT),
        ),
        line_comment="--",
        indent_after=r"(\bthen|\bdo|\belse|\bfunction\b.*\)|[{(\[])\s*$",
    ),
    Language(
        name="sql",
        extensions=(".sql",),
        regions=(C_BLOCK, SQ, DQ),
        rules=(
            ("comment", r"--.*"),
            (
                "keyword",
                r"(?i:\b(?:select|from|where|and|or|not|insert|into|values|update|set|delete|create|"
                r"table|index|view|drop|alter|add|join|left|right|inner|outer|on|group|by|order|"
                r"having|limit|offset|as|distinct|union|all|case|when|then|else|end|is|null|in|"
                r"exists|primary|key|foreign|references|default|begin|commit|rollback|with)\b)",
            ),
            ("number", NUMBER),
            ("function", FUNCTION),
        ),
        line_comment="--",
        block_comment=("/*", "*/"),
        indent_after=r"\(\s*$",
    ),
    Language(
        name="gitcommit",
        filenames=("COMMIT_EDITMSG", "MERGE_MSG", "TAG_EDITMSG", "SQUASH_MSG", "git-rebase-todo"),
        rules=(
            ("comment", r"^#.*"),
            ("key", r"^(?:[A-Z][\w-]*-by|Fixes|Closes|Refs|Change-Id|Link|Bug|Reviewed-on|Co-authored-by):(?= )"),
            ("link", r"\bhttps?://\S+"),
            ("code", r"`[^`]+`"),
        ),
        pairs=("()", "[]", "``"),
        first_line_token="heading",
        first_line_limit=72,
        list_continuation=True,
    ),
    Language(name="text", extensions=(".txt", ".text", ".log"), pairs=("()", "[]", "{}"), list_continuation=True),
]

BY_NAME = {lang.name: lang for lang in LANGUAGES}
ALIASES = {
    "py": "python", "c++": "cpp", "cxx": "cpp", "objc": "c", "js": "javascript", "ts": "javascript",
    "typescript": "javascript", "rs": "rust", "golang": "go", "sh": "shell", "bash": "shell",
    "zsh": "shell", "makefile": "make", "yml": "yaml", "md": "markdown", "patch": "diff",
    "xml": "html", "plain": "text", "none": "text", "txt": "text", "commit": "gitcommit",
    "kotlin": "java", "csharp": "java", "scss": "css",
}
TEXT = BY_NAME["text"]


def get(name: str) -> Language | None:
    key = name.strip().lower()
    return BY_NAME.get(ALIASES.get(key, key))


def detect(path: str | None, first_line: str = "") -> Language:
    if path:
        base = os.path.basename(path)
        for lang in LANGUAGES:
            if base in lang.filenames:
                return lang
        ext = os.path.splitext(base)[1].lower()
        if ext:
            for lang in LANGUAGES:
                if ext in lang.extensions:
                    return lang
    if first_line.startswith("#!"):
        for lang in LANGUAGES:
            if lang.shebang and re.search(lang.shebang, first_line):
                return lang
    if first_line.startswith("diff --git") or first_line.startswith("--- "):
        return BY_NAME["diff"]
    return TEXT
