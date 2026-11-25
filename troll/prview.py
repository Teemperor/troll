"""The pull request screen (`open-pr`), laid out like GitHub's PR page.

`PullRequestView` is the overlay's state (tab, scroll, back = the overlay to
return to); `layout(pr, tab, width)` builds every row of a tab once per width
as `(text, fg, bg)` segments, plus anchors (rows where timeline items start,
for n/p). `view.build_frame` only slices the visible part.

Tabs: Conversation (description card, comments, reviews with their inline
threads, commits and the merge on a timeline, with a sidebar of reviewers,
assignees and labels when there's room), Commits, and Files changed.
"""

from __future__ import annotations

import re
import zlib
from collections.abc import Callable

from . import github, markdown
from .github import Comment, Event, PullRequest, ago
from .highlight import Tokenizer
from .languages import detect
from .textutil import char_width, text_width

Seg = tuple[str, str, "str | None"]
BG = "pr"
TABS = ("Conversation", "Commits", "Files changed")
SIDEBAR_MIN = 100  # terminal width from which the sidebar is shown
SIDEBAR_W = 26
TAG_STYLES = [f"tag.{i}" for i in range(32)]  # the same colors as [tags] in the commit list
AVATARS = 8  # avatar.N backgrounds in tui/theme.py


def _fit(text: str, width: int) -> str:
    out, used = [], 0
    for ch in text:
        cw = char_width(ch)
        if used + cw > width:
            break
        out.append(ch)
        used += cw
    return "".join(out)


class Row:
    """Segments of one screen row; text past `width` is cut, `pad` fills the rest."""

    def __init__(self, width: int):
        self.width = width
        self.used = 0
        self.segs: list[Seg] = []

    def add(self, text: str, fg: str = "md.text", bg: str | None = BG) -> "Row":
        if not text or self.used >= self.width:
            return self
        w = text_width(text)
        if w > self.width - self.used:
            text = _fit(text, self.width - self.used)
            w = text_width(text)
        self.used += w
        bg = bg or BG
        if self.segs and self.segs[-1][1:] == (fg, bg):
            self.segs[-1] = (self.segs[-1][0] + text, fg, bg)
        else:
            self.segs.append((text, fg, bg))
        return self

    def runs(self, runs, bg: str | None = None) -> "Row":
        for text, fg, rbg in runs:
            self.add(text, fg, rbg or bg)
        return self

    def pad(self, upto: int | None = None, bg: str | None = BG) -> "Row":
        target = self.width if upto is None else min(upto, self.width)
        if self.used < target:
            self.add(" " * (target - self.used), "md.text", bg)
        return self

    def right(self, runs, bg: str | None = BG, margin: int = 0) -> "Row":
        """Right-align `runs` (if they fit after what's there)."""
        w = markdown.runs_width(runs)
        if self.used + w + margin + 1 <= self.width:
            self.pad(self.width - w - margin, bg)
            self.runs(runs, bg)
        return self

    def done(self) -> list[Seg]:
        return self.pad().segs


def tag_style(name: str) -> str:
    return TAG_STYLES[zlib.crc32(name.lower().encode()) % len(TAG_STYLES)]


def avatar(login: str) -> list[Seg]:
    """GitHub shows a picture; we show the initial on a color picked from the login."""
    bg = f"avatar.{zlib.crc32(login.lower().encode()) % AVATARS}"
    return [((login[:1] or "?").upper() + " ", "pr.avatar", bg)]


ASSOCIATIONS = {"OWNER": "Owner", "MEMBER": "Member", "COLLABORATOR": "Collaborator",
                "CONTRIBUTOR": "Contributor", "FIRST_TIME_CONTRIBUTOR": "First-time contributor",
                "FIRST_TIMER": "First-time contributor"}
STATE_BADGES = {"open": ("⑂ Open", "pr.open"), "draft": ("⑂ Draft", "pr.draft"),
                "merged": ("⑂ Merged", "pr.merged"), "closed": ("⑂ Closed", "pr.closed")}
REVIEW_ICONS = {"APPROVED": ("✓", "pr.icon.approved", "approved these changes"),
                "CHANGES_REQUESTED": ("±", "pr.icon.changes", "requested changes"),
                "DISMISSED": ("✕", "pr.icon.neutral", "left a review (dismissed)"),
                "COMMENTED": ("◉", "pr.icon.neutral", "reviewed")}


def pills(pr: PullRequest, c_author: str, association: str) -> list[Seg]:
    out: list[Seg] = []
    if c_author == pr.author:
        out += [(" Author ", "pr.pill", "pr.pill"), (" ", "md.text", None)]
    label = ASSOCIATIONS.get(association)
    if label:
        out += [(f" {label} ", "pr.pill", "pr.pill")]
    return out


# ------------------------------------------------------------------ building blocks


def card(pr: PullRequest, author: str, verb: str, when: str, association: str,
         body: list[list[Seg] | markdown.Line], width: int, left: list[Seg] | None = None) -> list[list[Seg]]:
    """A comment box: header bar (author, verb, time, pills), then the body lines."""
    mine = author == pr.author
    border = "pr.border.author" if mine else "pr.border"
    head_bg = "pr.cardhead.author" if mine else "pr.cardhead"
    inner = width - 4
    rows: list[list[Seg]] = []
    r = Row(width).add("╭" + "─" * (width - 2) + "╮", border)
    rows.append(r.done())
    r = Row(width).add("│", border).add(" ", "md.text", head_bg)
    r.add(author, "pr.author", head_bg).add(f" {verb} ", "pr.muted", head_bg).add(ago(when), "pr.muted", head_bg)
    r.right(pills(pr, author, association), head_bg, margin=2)
    r.pad(width - 1, head_bg).add("│", border)
    rows.append(r.done())
    rows.append(Row(width).add("├" + "─" * (width - 2) + "┤", border).done())
    for line in body:
        if isinstance(line, tuple):  # a markdown line (runs, fill)
            runs, fill = line
            r = Row(width).add("│", border).add(" ")
            r.runs(runs).pad(width - 2, fill or BG).add(" ").add("│", border)
        else:  # already a row of inner width
            r = Row(width).add("│", border).add(" ").runs(line).pad(width - 2).add(" ").add("│", border)
        rows.append(r.done())
    rows.append(Row(width).add("╰" + "─" * (width - 2) + "╯", border).done())
    if left is not None:  # the avatar column
        rows = [left_col(row, (avatar(author) if i == 1 else []), width) for i, row in enumerate(rows)]
    return rows


def left_col(row: list[Seg], lead: list[Seg], width: int) -> list[Seg]:
    r = Row(width + 4).add(" ").runs(lead).pad(4)
    return r.runs(row).done()


def md_body(text: str, width: int, empty: str = "") -> list[markdown.Line]:
    lines = markdown.render(text, width) if text.strip() else []
    if not lines and empty:
        lines = [([(empty, "pr.empty", None)], None)]
    return [([], None)] + lines + [([], None)]


def hunk_rows(c: Comment, width: int, tail: int = 4) -> list[list[Seg]]:
    """The last lines of an inline comment's diff hunk (the commented line is the last), highlighted."""
    lines = [ln for ln in c.hunk.split("\n") if ln and not ln.startswith("@@")][-tail:]
    hdr = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)", c.hunk)
    old, new = (int(hdr.group(1)), int(hdr.group(2))) if hdr else (0, 0)
    for ln in [ln for ln in c.hunk.split("\n") if ln and not ln.startswith("@@")][:-len(lines) or None]:
        if ln[0] != "+":
            old += 1
        if ln[0] != "-":
            new += 1
    tok = Tokenizer(detect(c.path))
    rows = []
    for ln in lines:
        kind, code = ln[0], ln[1:].replace("\t", "    ")
        bg = "add" if kind == "+" else "del" if kind == "-" else None
        o = "" if kind == "+" else str(old)
        n = "" if kind == "-" else str(new)
        old += kind != "+"
        new += kind != "-"
        spans, _ = tok.tokenize(code, None)
        fg = ["md.codetext"] * len(code)
        for s, e, t in spans:
            fg[s:e] = [t] * (min(e, len(code)) - s)
        r = Row(width).add(f"{o:>5}{n:>5} ", "pr.lineno", bg)
        r.add(kind if kind in "+-" else " ", "diff_add" if kind == "+" else "diff_del" if kind == "-" else "md.text", bg)
        for ch, f in zip(code, fg):
            r.add(ch, f, bg)
        rows.append(r.pad(bg=bg or BG).segs)
    return rows


def thread_rows(pr: PullRequest, c: Comment, width: int) -> list[list[Seg]]:
    """An inline review thread inside a review card: file and lines, then the comments."""
    rows = []
    loc = f" {c.path}" + (f":{c.line}" if c.line else "")
    r = Row(width).add("┌", "pr.border").add(loc + " ", "pr.path", "pr.cardhead").pad(width - 1, "pr.cardhead")
    rows.append(r.add("┐", "pr.border").done())
    for h in hunk_rows(c, width - 2):
        rows.append(Row(width).add("│", "pr.border").runs(h).pad(width - 1).add("│", "pr.border").done())
    for k, comment in enumerate([c] + c.replies):
        rows.append(Row(width).add("├" + "─" * (width - 2) + "┤", "pr.border").done())
        r = Row(width).add("│ ", "pr.border").runs(avatar(comment.author)).add(" ")
        r.add(comment.author, "pr.author").add("  " + ago(comment.created), "pr.muted")
        r.right(pills(pr, comment.author, comment.association), margin=2).pad(width - 1).add("│", "pr.border")
        rows.append(r.done())
        for runs, fill in markdown.render(comment.body, width - 7):
            r = Row(width).add("│", "pr.border").add("    ").runs(runs).pad(width - 3, fill or BG).pad(width - 1)
            rows.append(r.add("│", "pr.border").done())
    rows.append(Row(width).add("└" + "─" * (width - 2) + "┘", "pr.border").done())
    return rows


def event_row(icon: str, icon_style: str, runs: list[Seg], width: int) -> list[Seg]:
    """A timeline line with a round icon on the timeline (column 6)."""
    r = Row(width).add("     ").add(f" {icon} ", "pr.icon.fg", icon_style + ".bg").add(" ")
    return r.runs(runs).done()


def spine(width: int) -> list[Seg]:
    return Row(width).add("      ").add("│", "pr.border").done()


# ------------------------------------------------------------------ tabs


def header_rows(pr: PullRequest, tab: int, width: int) -> list[list[Seg]]:
    rows = [Row(width).done()]
    title = markdown.wrap([(pr.title + " ", "pr.title", None), (f"#{pr.ref.number}", "pr.number", None)], width - 2)
    rows += [Row(width).add(" ").runs(t).done() for t in title]
    rows.append(Row(width).done())
    text, style = STATE_BADGES.get(pr.state, STATE_BADGES["open"])
    badge = [(f" {text} ", "pr.badge", style)]
    n = len(pr.commits)
    commits = f"{n} commit{'s' if n != 1 else ''}"
    if pr.state == "merged":
        desc = [(pr.merged_by or pr.author, "pr.author", None), (f" merged {commits} into ", "pr.muted", None)]
    else:
        desc = [(pr.author, "pr.author", None), (f" wants to merge {commits} into ", "pr.muted", None)]
    nb = "\u00a0"  # keeps the pill's padding when wrapping
    desc += [(f"{nb}{pr.base}{nb}", "pr.branch", "pr.branch"), (" from ", "pr.muted", None),
             (f"{nb}{pr.head}{nb}", "pr.branch", "pr.branch")]
    when = pr.merged_at if pr.state == "merged" else pr.closed_at if pr.state == "closed" else pr.created
    desc += [(" · " + ago(when), "pr.muted", None)]
    bw = markdown.runs_width(badge) + 2
    for k, runs in enumerate(markdown.wrap(desc, width - bw)):
        r = Row(width).add(" ").runs(badge) if k == 0 else Row(width).add(" " * (bw - 1))
        rows.append(r.add(" ").runs(runs).done())
    rows.append(Row(width).done())
    counts = (pr.comment_count, len(pr.commits), len(pr.files))
    r = Row(width).add(" ")
    spans = []
    for i, (name, count) in enumerate(zip(TABS, counts)):
        start = r.used
        r.add(" ").add(name, "pr.tab.sel" if i == tab else "pr.tab").add(" ")
        r.add(f" {count} ", "pr.count", "pr.count").add("  ")
        spans.append((start, r.used - 2))
    total = pr.additions + pr.deletions
    squares = 5 if total else 0
    green = round(5 * pr.additions / total) if total else 0
    stat = [(f"+{pr.additions:,} ", "pr.add", None), (f"−{pr.deletions:,} ", "pr.del", None),
            ("■" * green, "pr.add", None), ("■" * (squares - green), "pr.del", None)]
    rows.append(r.right(stat, margin=1).done())
    start, end = spans[tab]
    under = Row(width).add("─" * start, "pr.border").add("━" * (end - start), "pr.tab.line")
    rows.append(under.add("─" * (width - end), "pr.border").done())
    rows.append(Row(width).done())
    return rows


def conversation(pr: PullRequest, width: int) -> tuple[list[list[Seg]], list[int]]:
    cw = width - 5  # card width after the avatar column
    rows: list[list[Seg]] = []
    anchors: list[int] = []

    def add_card(author, verb, when, assoc, body):
        anchors.append(len(rows))
        rows.extend(card(pr, author, verb, when, assoc, body, cw, left=[]))

    add_card(pr.author, "commented", pr.created, pr.association,
             md_body(pr.body, cw - 4, "No description provided."))
    for e in github.timeline(pr):
        rows.append(spine(width))
        if e.kind == "comment":
            c = e.comment
            add_card(c.author, "commented", c.created, c.association, md_body(c.body, cw - 4))
        elif e.kind == "review":
            rv = e.review
            icon, style, verb = REVIEW_ICONS.get(rv.state, REVIEW_ICONS["COMMENTED"])
            content = bool(rv.body.strip() or rv.threads)
            if rv.state != "COMMENTED" or not content:  # a plain comment review is just its card
                anchors.append(len(rows))
                rows.append(event_row(icon, style, [(rv.author, "pr.author", None), (f" {verb} ", "md.text", None),
                                                     (ago(rv.submitted), "pr.muted", None)], width))
                if content:
                    rows.append(spine(width))
            if content:
                body: list = md_body(rv.body, cw - 4) if rv.body.strip() else [([], None)]
                for t in rv.threads:
                    body += thread_rows(pr, t, cw - 4) + [[]]
                if rv.state == "COMMENTED":
                    anchors.append(len(rows))
                rows.extend(card(pr, rv.author, "reviewed" if rv.state == "COMMENTED" else "left a comment",
                                 rv.submitted, rv.association, body, cw, left=[]))
        elif e.kind == "commits":
            anchors.append(len(rows))
            who = sorted({c.author for c in e.commits})
            n = len(e.commits)
            rows.append(event_row("⊙", "pr.icon.neutral",
                                  [(", ".join(who), "pr.author", None),
                                   (f" added {n} commit{'s' if n != 1 else ''} ", "md.text", None),
                                   (ago(e.commits[-1].date), "pr.muted", None)], width))
            for c in e.commits:
                r = Row(width).add("      ").add("│", "pr.border").add("   ")
                r.add("○ ", "pr.muted").add(c.subject, "md.text")
                rows.append(r.right([(c.sha[:7], "pr.sha", None)], margin=1).done())
        elif e.kind in ("merged", "closed"):
            anchors.append(len(rows))
            if e.kind == "merged":
                runs = [(pr.merged_by or "someone", "pr.author", None), (" merged commit ", "md.text", None),
                        (pr.merge_sha[:7], "pr.sha", None), (" into ", "md.text", None),
                        (pr.base, "pr.branch", "pr.branch"), (" " + ago(pr.merged_at), "pr.muted", None)]
                rows.append(event_row("⑂", "pr.icon.merged", runs, width))
            else:
                rows.append(event_row("✕", "pr.icon.closed", [("closed this ", "md.text", None),
                                                               (ago(pr.closed_at), "pr.muted", None)], width))
    rows.append(Row(width).done())
    return rows, anchors


def sidebar(pr: PullRequest, width: int) -> list[list[Seg]]:
    rows: list[list[Seg]] = []

    def section(title: str, items: list[list[Seg]], empty: str):
        if rows:
            rows.append(Row(width).add("─" * width, "pr.border").done())
        rows.append(Row(width).add(title, "pr.side.head").done())
        rows.extend(items or [Row(width).add(empty, "pr.muted").done()])

    reviewers = []
    for login, state in pr.reviewers():
        icon, style = {"APPROVED": ("✓", "pr.icon.approved"), "CHANGES_REQUESTED": ("±", "pr.icon.changes"),
                       "PENDING": ("●", "pr.icon.pending")}.get(state, ("◉", "pr.icon.neutral"))
        r = Row(width).runs(avatar(login)).add(" ").add(login, "md.text")
        reviewers.append(r.right([(icon, style, None)]).done())
    section("Reviewers", reviewers, "No reviews")
    section("Assignees", [Row(width).runs(avatar(a)).add(" ").add(a).done() for a in pr.assignees],
            "No one assigned")
    labels: list[list[Seg]] = []
    r = Row(width)
    for name in pr.labels:
        w = text_width(name) + 3
        if r.used and r.used + w > width:
            labels.append(r.done())
            r = Row(width)
        r.add(f" {name} ", tag_style(name), "pr.pill").add(" ")
    if r.used:
        labels.append(r.done())
    section("Labels", labels, "None yet")
    if pr.files:
        files = []
        for f in pr.files[:12]:
            stat = f" +{f.additions} −{f.deletions}"
            name = f.path.rsplit("/", 1)[-1]
            r = Row(width).add(_fit(name, width - text_width(stat) - 1), "md.text")
            files.append(r.right([(f"+{f.additions}", "pr.add", None), (f" −{f.deletions}", "pr.del", None)]).done())
        if len(pr.files) > 12:
            files.append(Row(width).add(f"and {len(pr.files) - 12} more (tab 3)", "pr.muted").done())
        section(f"Files ({len(pr.files)})", files, "")
    return rows


def commits_tab(pr: PullRequest, width: int) -> tuple[list[list[Seg]], list[int]]:
    rows: list[list[Seg]] = []
    anchors: list[int] = []
    if not pr.commits:
        return [Row(width).add("  No commits", "pr.muted").done()], []
    w = width - 4
    day = None
    for i, c in enumerate(pr.commits):
        d = c.date[:10]
        if d != day:
            if day is not None:
                rows.append(Row(width).add("  ").add("╰" + "─" * (w - 2) + "╯", "pr.border").done())
                rows.append(Row(width).done())
            day = d
            rows.append(Row(width).add("  ").add("⊙ ", "pr.muted").add(f"Commits on {_date(c.date)}", "pr.muted").done())
            rows.append(Row(width).add("  ").add("╭" + "─" * (w - 2) + "╮", "pr.border").done())
        elif i:
            rows.append(Row(width).add("  ").add("├" + "─" * (w - 2) + "┤", "pr.border").done())
        anchors.append(len(rows))
        r = Row(width).add("  ").add("│ ", "pr.border").add(c.subject, "pr.commit")
        r.right([(c.sha[:7], "pr.sha", "pr.pill")], margin=4).pad(width - 3).add("│", "pr.border")
        rows.append(r.done())
        r = Row(width).add("  ").add("│ ", "pr.border").runs(avatar(c.author)).add(" ")
        r.add(c.author, "pr.author").add(" committed " + ago(c.date), "pr.muted").pad(width - 3).add("│", "pr.border")
        rows.append(r.done())
    rows.append(Row(width).add("  ").add("╰" + "─" * (w - 2) + "╯", "pr.border").done())
    return rows, anchors


def _date(iso: str) -> str:
    try:
        y, m, d = iso[:10].split("-")
        return f"{'Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split()[int(m) - 1]} {int(d)}, {y}"
    except (ValueError, IndexError):
        return iso[:10]


def files_tab(pr: PullRequest, width: int) -> tuple[list[list[Seg]], list[int]]:
    rows: list[list[Seg]] = []
    anchors: list[int] = []
    n = len(pr.files)
    rows.append(Row(width).add("  Showing ", "pr.muted").add(f"{n} changed file{'s' if n != 1 else ''}", "pr.author")
                .add(" with ", "pr.muted").add(f"{pr.additions} additions", "pr.add").add(" and ", "pr.muted")
                .add(f"{pr.deletions} deletions", "pr.del").done())
    rows.append(Row(width).done())
    stats = {f.path: f for f in pr.files}
    w = width - 2
    if not pr.diff.strip():
        for f in pr.files:
            anchors.append(len(rows))
            rows.append(Row(width).add("  ").add(f.path, "pr.path").right(
                [(f"+{f.additions}", "pr.add", None), (f" −{f.deletions}", "pr.del", None)], margin=1).done())
        rows.append(Row(width).done())
        rows.append(Row(width).add("  The diff is not available (too large?)", "pr.empty").done())
        return rows, anchors
    tok = None
    old = new = 0
    in_hunk = False

    def close_box():
        if rows and anchors:
            rows.append(Row(width).add(" ").add("╰" + "─" * (w - 2) + "╯", "pr.border").done())
            rows.append(Row(width).done())

    def boxed(r: Row, bg: str | None = None) -> list[Seg]:
        return r.pad(width - 2, bg).add("│", "pr.border").done()

    for line in pr.diff.split("\n"):
        if line.startswith("diff --git "):
            close_box()
            path = line.rsplit(" b/", 1)[-1]
            f = stats.get(path)
            tok = Tokenizer(detect(path))
            in_hunk = False
            anchors.append(len(rows))
            rows.append(Row(width).add(" ").add("╭" + "─" * (w - 2) + "╮", "pr.border").done())
            r = Row(width).add(" ").add("│", "pr.border").add(" ▾ ", "pr.muted", "pr.cardhead")
            r.add(path, "pr.path", "pr.cardhead")
            if f is not None:
                r.right([(f" +{f.additions}", "pr.add", None), (f" −{f.deletions} ", "pr.del", None)],
                        "pr.cardhead", margin=2)
            rows.append(boxed(r, "pr.cardhead"))
            rows.append(Row(width).add(" ").add("├" + "─" * (w - 2) + "┤", "pr.border").done())
        elif line.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)", line)
            old, new = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
            in_hunk = True
            r = Row(width).add(" ").add("│", "pr.border").add(" " * 11, "pr.lineno", "pr.hunk")
            rows.append(boxed(r.add(line, "pr.hunktext", "pr.hunk"), "pr.hunk"))
        elif in_hunk and line[:1] in ("+", "-", " ") and tok is not None:
            kind, code = line[0], line[1:].replace("\t", "    ")
            bg = "add" if kind == "+" else "del" if kind == "-" else None
            o = "" if kind == "+" else str(old)
            n_ = "" if kind == "-" else str(new)
            old += kind != "+"
            new += kind != "-"
            spans, _ = tok.tokenize(code, None)
            fg = ["md.codetext"] * len(code)
            for s, e, t in spans:
                fg[s:e] = [t] * (min(e, len(code)) - s)
            runs = [(ch, f, bg) for ch, f in zip(code, fg)]
            room = w - 2 - 12
            chunks = markdown._char_wrap(runs, room) if runs else [[]]
            for k, chunk in enumerate(chunks):
                r = Row(width).add(" ").add("│", "pr.border")
                r.add(f"{o:>5}{n_:>5} " if k == 0 else " " * 11, "pr.lineno", bg)
                mark = kind if kind in "+-" and k == 0 else " "
                r.add(mark, "diff_add" if kind == "+" else "diff_del" if kind == "-" else "md.text", bg)
                rows.append(boxed(r.runs(chunk, bg), bg))
        elif in_hunk and line.startswith("\\"):
            r = Row(width).add(" ").add("│", "pr.border").add(" " * 12, "pr.lineno")
            rows.append(boxed(r.add(line, "pr.muted")))
        elif line.startswith("Binary files"):
            rows.append(boxed(Row(width).add(" ").add("│", "pr.border").add("  Binary file not shown", "pr.empty")))
    close_box()
    return rows, anchors


def layout(pr: PullRequest, tab: int, width: int) -> tuple[list[list[Seg]], list[int], int]:
    """All rows of `tab`, the anchor rows (timeline items, commits, files) and the header height."""
    width = max(20, width)
    head = header_rows(pr, tab, width)
    if tab == 0:
        if width >= SIDEBAR_MIN:
            main_w = width - SIDEBAR_W - 3
            body, anchors = conversation(pr, main_w)
            side = sidebar(pr, SIDEBAR_W)
            joined = []
            for i in range(max(len(body), len(side))):
                r = Row(width)
                r.runs(body[i] if i < len(body) else []).pad(main_w).add("   ")
                joined.append(r.runs(side[i] if i < len(side) else []).done())
            body = joined
        else:
            body, anchors = conversation(pr, width)
    elif tab == 1:
        body, anchors = commits_tab(pr, width)
    else:
        body, anchors = files_tab(pr, width)
    return head + body, [len(head) + a for a in anchors], len(head)


# ------------------------------------------------------------------ state


class PullRequestView:
    """Overlay showing a PR; Esc goes back to `back` (e.g. the commit list)."""

    kind = "pr"

    def __init__(self, pr: PullRequest, back=None, open_url: Callable[[str], None] | None = None):
        self.pr = pr
        self.back = back
        self.open_url = open_url
        self.tab = 0
        self.scrolls = [0, 0, 0]
        self.height = 20
        self.done = False
        self._cache: dict[tuple[int, int], tuple[list[list[Seg]], list[int], int]] = {}
        self.width = 80

    @property
    def scroll(self) -> int:
        return self.scrolls[self.tab]

    @scroll.setter
    def scroll(self, value: int) -> None:
        self.scrolls[self.tab] = value

    def rows(self, width: int) -> tuple[list[list[Seg]], list[int], int]:
        self.width = width
        key = (self.tab, width)
        if key not in self._cache:
            self._cache[key] = layout(self.pr, self.tab, width)
        return self._cache[key]

    def visible(self, height: int, width: int) -> list[list[Seg]]:
        self.height = height
        rows, _, _ = self.rows(width)
        self.clamp(len(rows))
        out = rows[self.scroll:self.scroll + height]
        blank = Row(width).done()
        return out + [blank] * (height - len(out))

    def clamp(self, total: int) -> None:
        self.scroll = max(0, min(self.scroll, max(0, total - self.height)))

    def handle(self, key: str) -> None:
        page = max(1, self.height - 2)
        rows, anchors, head = self.rows(self.width)
        if key in ("Up", "C-p", "k"):
            self.scroll -= 1
        elif key in ("Down", "C-n", "j", "Enter"):
            self.scroll += 1
        elif key == "WheelUp":
            self.scroll -= 3
        elif key == "WheelDown":
            self.scroll += 3
        elif key in ("PageUp", "C-y", "M-Up", "b"):
            self.scroll -= page
        elif key in ("PageDown", "C-v", " ", "M-Down"):
            self.scroll += page
        elif key in ("Home", "M-\\", "C-Home", "g"):
            self.scroll = 0
        elif key in ("End", "M-/", "C-End", "G"):
            self.scroll = len(rows)
        elif key in ("Tab", "Right", "l"):
            self.tab = (self.tab + 1) % len(TABS)
        elif key in ("S-Tab", "BTab", "Left", "h"):
            self.tab = (self.tab - 1) % len(TABS)
        elif key in ("1", "2", "3"):
            self.tab = int(key) - 1
        elif key in ("n", "N", "p", "P"):
            # the anchor's row goes just below the header (or the top if the header scrolled away)
            offset = 1
            top = self.scroll + offset
            if key in ("n", "N"):
                later = [a for a in anchors if a > top]
                target = later[0] if later else None
            else:
                earlier = [a for a in anchors if a < top]
                target = earlier[-1] if earlier else 0
            if target is not None:
                self.scroll = max(0, target - offset)
        elif key in ("o", "C-o") and self.open_url is not None:
            self.open_url(self.pr.ref.url)
        elif key in ("Esc", "q", "C-x", "C-c", "C-g"):
            self.done = True
        self.clamp(len(self.rows(self.width)[0]))
