# diffedit

A terminal text editor with nano's keys, plus syntax highlighting, smarter auto-formatting, a command palette, and a way to **edit the diff of an existing git commit**. It's written in pure Python (stdlib + curses) and has no dependencies.

```sh
pip install -e .              # installs `diffedit` and `git-diffedit`
diffedit file.py              # like nano
diffedit +42 file.py          # open at line 42 (also file.py:42)
git diffedit HEAD~2           # clean up the changes made by HEAD~2
git diffedit                  # pick a commit from the log
```

Without installing: `python3 -m diffedit ...`

## Editing a commit

`git diffedit REV` (or `diffedit --commit REV`, or the `commit REV` command inside the editor) opens a list of the files the commit touched, plus its message. Press Enter on a file to open it:

- The file is shown **as of that commit**, with the commit's diff drawn on top. Lines the commit added have a green `+`. Lines it removed appear as red, read-only `-` ghost lines.
- Unchanged code is **folded away** so you only see the diff with a few lines of context. `M-Z` toggles the fold.
- Edit the text normally. Every line you change gets a yellow `*` in the gutter, so you can see what you've changed compared with the commit.
- `M-↓` / `M-↑` jump between changes (and on to the next or previous file). `M->` / `M-<` switch files. `^X` goes back to the file list.
- `^S` rewrites the commit. Any later commits are replayed on top of it automatically. The `revert` command undoes your edit at the cursor.

How the rewrite works (`diffedit/gitcommit.py`):

- It uses git plumbing only: no checkout, no interactive rebase, and it never leaves you in a half-finished state. Author, dates and message are kept.
- Later commits are replayed with a line-based three-way merge (`diffedit/merge.py`). It only reports a conflict when a later commit changed the same lines you edited. Plain git would also stop at changes on neighbouring lines; this merge doesn't. That matters when you reword a comment that sits directly above a line a later commit changed.
- If anything conflicts, nothing is changed and your edits stay in the editor so you can adjust them.
- It refuses to run on merge commits, on history that contains merges, and while a rebase or merge is in progress.
- Afterwards the old HEAD is saved in `ORIG_HEAD`. Undo the rewrite with `git reset --keep ORIG_HEAD`.

## Keys

The nano bindings work as usual, for example: `^O` write out, `^S` save, `^X` exit, `^W` / `^\` search and replace, `^K` / `^U` cut and paste (consecutive `^K` presses add to the cut), `^J` justify, `^_` go to line, `M-U` / `M-E` undo and redo, `M-A` set mark, `M-6` copy, `M-3` comment, `M-]` jump to bracket, `M-W` / `M-Q` search again. Shift+arrows select text. `^G` shows every binding, and the help screen is generated from the same command table the keys use.

## Command palette (`^T`)

Start typing to filter the commands, then press Enter. You can also type a full command with arguments:

```
goto 120:4    set tabsize 2    lang rust    commit HEAD~3    justify 72
s/foo/bar/g   !sort (filters the selection)   /needle   42   format
```

## Formatting helpers

- Enter keeps the current indentation. It indents after `:`, `{`, `(` (but not when the colon is inside a comment), splits `{|}` into a block, and dedents after `return`.
- Enter continues `#`, `//`, `///`, and ` * ` comments, as well as Markdown lists. Pressing Enter on an empty continued line ends the comment or list.
- `}` and `)` line up with their opening bracket. In Python, `else:`, `elif`, and `except` line up with the matching `if` or `try`.
- Brackets and quotes are auto-paired, but not inside comments or strings, so typing "don't" in a comment works.
- `^J` reflows the paragraph, comment block (keeping the markers), or list item under the cursor to `fill_width` (80 by default). If you select lines first, only those are reflowed.
- On save, trailing whitespace is trimmed **only on lines you edited**, so saving never adds whitespace noise to a diff. A final newline is kept only if the file already had one.
- `format` runs the language's external formatter: ruff or black, clang-format, gofmt, rustfmt, prettier, and so on.

Syntax highlighting covers Python, C/C++/ObjC, JS/TS, Rust, Go, Java/Kotlin, Swift, shell, Make, JSON, YAML, TOML, INI, Markdown, diff, HTML/XML, CSS, Lua, SQL and git commit messages.

## Layout

The editor logic and the rendering are kept strictly apart:

| module | role |
|---|---|
| `buffer.py` | lines + grouped undo/redo |
| `document.py` | cursor, selection, all editing operations |
| `autoformat.py` | pure functions for indentation, comments and justify |
| `highlight.py`, `languages.py` | incremental regex highlighter |
| `search.py`, `merge.py`, `diffmodel.py` | find/replace, 3-way merge, diff overlay and folding |
| `gitcommit.py`, `commit_session.py` | git plumbing and the commit-editing session |
| `commands.py`, `prompt.py`, `editor.py` | command table, modal prompts and pickers, key dispatch |
| `view.py` | turns editor state into a `Frame` of styled text (no curses) |
| `tui/` | the only code that touches the terminal: key decoding and painting |

Tests: `python3 -m pytest` (225 tests). They drive the editor with key names, check the `Frame` data, and create real temporary git repositories for the rewrite tests.
