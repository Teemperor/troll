# CLAUDE.md

troll: nano-style curses editor in pure Python (stdlib only, Python ≥3.10) with syntax highlighting, smart formatting, a command palette and an interactive "edit a commit's diff" mode. Entry points: `troll` / `git-troll` (`troll/cli.py`), or `python3 -m troll`.

## Commands

- Test: `python3 -m pytest -q` (~270 tests, ~10s; the git tests create real temp repos).
- Run: `python3 -m troll FILE`, `python3 -m troll --commit HEAD~1`.
- The TUI needs a real terminal. To check it end to end, drive it through a pty (`pty.fork`) and **always kill the child after a deadline**, because a modal prompt waiting for input will otherwise hang forever.

## Architecture (the core rule: logic and rendering stay separate)

Nothing outside `troll/tui/` may import curses. The flow is:
`tui/keys.py` (raw input → key names) → `Editor.handle_key(name)` → state → `view.build_frame(ed, h, w)` → `Frame` (rows of `(text, fg_style, bg_style)` segments + cursor) → `tui/app.py` paints it.

| module | role |
|---|---|
| `buffer.py` | lines list; **all** mutations go through `insert`/`delete`; grouped undo (`begin_group`/`end_group`, `merge_key` for typing runs, `seal()` on cursor moves); listeners get `(row, line_delta)` |
| `document.py` | cursor/selection/scroll + every editing op (`type_char`, `newline`, `backspace`, `justify`, …). Wrap edits in `with doc.edit(merge_key=...)`. `eol` = on-disk line ending; `original_lines` = text at load/save (used for "edited" trimming and markers) |
| `autoformat.py` | **pure** functions: `plan_newline`, `electric_indent`, `plan_pair`, `toggle_comment`, `line_prefix`, `find_paragraph`/`justify` |
| `languages.py` / `highlight.py` | Language = regex `rules` + multi-line `regions`. `Highlighter` keeps a per-line state chain that is invalidated incrementally. `context_at(row, col)` says whether you're in a comment or string (used so auto-pairing is skipped there) |
| `definitions.py` | **pure** jump-to/show-definition: `symbol_at`, `find_definitions` (exact for LLVM IR; indentation/brace-block heuristics for Python and C/C++) over `code_lines` (comments/strings blanked). Candidates carry a `tier`; `WEAK` ones (declarations, imports) make `Editor.locate_definitions` search other buffers, `#include`d / sibling files and imported modules. `build_panel` → `ed.definition`, drawn on the right by `view.definition_rows` |
| `search.py` | find/replace over lines (matches never span lines) |
| `diffmodel.py` | `compute(base, original, current)` → `LineDiff` (added/ghosts/hunks/edited/opcodes/removed). `build_rows(..., fold, side_by_side)` → display `Row`s (`line`/`ghost`/`fold`, `right` = base row in side-by-side mode) |
| `merge.py` | line-based 3-way merge; adjacent (non-overlapping) changes merge cleanly, unlike git |
| `gitcommit.py` | plumbing only: `load_commit`, `rewrite_commit` (temp index → `commit-tree`; descendants replayed via `merge3` on the edited files only, following renames; then `read-tree -m -u` + `update-ref`, `ORIG_HEAD` = old HEAD). Raises `GitError`; on failure the repo is untouched |
| `commit_session.py` | one `Document` per changed file (with `doc.diff = DiffState`) + the message doc; `current is None` = overview screen |
| `commands.py` | **single source of truth** for actions: `@command(name, help, keys=..., aliases=..., prompt=...)`. The keymap, the palette and the help screen are all generated from it. Key names look like `"C-k"`, `"M-u"`, `"S-Up"`, `"M-Down"`, `"Enter"`, `"F7"` |
| `editor.py` | dispatch order: prompt → overlay → commit overview → keymap → typed char. Modal flows (save/exit/search/replace/commit apply). `option_targets(key)` decides which `Settings` objects a change applies to. `raise_errors=True` for tests (otherwise errors become status messages) |
| `prompt.py` | pure state machines: `Prompt`, `Choice`, `Picker` (fuzzy palette), `HelpScreen`; each sets `.done` |
| `config.py` | the user's settings file (`load_config`/`save_config`, nanorc-style `set`/`unset` lines; `$TROLL_CONFIG` overrides the path) and the cursor position log for `remember_position` (`$XDG_STATE_HOME`). The CLI applies it before command line flags |
| `settings.py` / `settings_screen.py` | `Settings` dataclass + `OPTIONS` metadata (label, section, range, `editor_wide`, `all_files`); the settings panel overlay |
| `view.py` | all layout: gutters, scrolling (`adjust_scroll`), unified and side-by-side diff rows, overlays, title/status/help bars |
| `tui/theme.py` | style name → 256/8-color fg/bg |

## Conventions and gotchas

- Positions are `(row, col)` with col = string index; display columns come from `textutil.display_col` (tabs and wide characters).
- Lines are `text.split("\n")`, so a trailing newline shows up as a final `""` line. Keep this: it makes byte-exact round-trips work. Decode with `surrogateescape`.
- Commit files must never gain unrelated diff noise: trimming only touches edited lines, and a final newline is only added if the original had one.
- Adding a setting: add a field to `Settings`, an `OptionInfo` in `OPTIONS` (a test checks every field is listed), and aliases in `ALIASES` if you want them. Per-file values live in `doc.settings`. Set `editor_wide` for editor-global options (read them from `ed.settings`) and `all_files` for options that apply to every open file.
- Adding a command or key: one `@command` in `commands.py`; check that the key isn't already bound (`build_keymap`). Commands with `prompt=` ask for their argument when run without one.
- The layout cache key in `Document.layout()` must include every input to `build_rows` (fold, context, cursor row, side_by_side).
- Tests: use `conftest.editor_with(editor, text, path=..., cursor=..., **settings)` and `make_doc`; drive with `ed.keys(...)`/`ed.type(...)`; check `build_frame(ed, h, w).text()`/`.cursor`. Git tests use the `repo` fixture (isolated `GIT_CONFIG_GLOBAL`, fixed identities). Run command lines "like a user" through the palette (`C-t`, type, `Enter`) so they hit the same error handling as real input.
- Per-frame highlight inputs (matching brackets, word under cursor, all search matches) go in `view.Marks`; `emit_cells` draws a line's cells, plus the guide stripe and indent guides.
- Soft wrap: `textutil.wrap_starts` splits a line into screen rows. `doc.scroll_row` still counts display rows (whole lines), so anything that converts between screen rows and lines must go through `_item_height`/`last_visible_index`.
- Mouse events reach the editor as keys `Click:Y:X` / `WheelUp:Y:X` / `WheelDown:Y:X` (screen cells). `build_frame` fills `ed.click_map` (screen body row → buffer row, left display col, text x) so clicks resolve without the editor knowing the layout.
- Tests never touch the real home directory: the autouse `user_files` fixture points the settings file and state dir into `tmp_path`.
- Language detection can override `tab_size`/`expand_tabs` (detect_indent); tests that depend on these should set them explicitly.
