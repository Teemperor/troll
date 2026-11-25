"""Pull requests fetched with the GitHub CLI (`gh`).

`parse_pr_url` splits a PR URL into host/owner/repo/number; `fetch(url)` asks
`gh api` for the PR, its conversation (issue comments, reviews and their inline
comments), commits, changed files and diff, and returns a `PullRequest`.
`timeline(pr)` orders the conversation the way GitHub shows it.

`run_gh(args)` is the only place that runs a process (tests replace it). It
raises `GhError` with gh's message when gh is missing, not logged in, or the
PR doesn't exist.
"""

from __future__ import annotations

import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

URL_RE = re.compile(r"^https?://([^/\s]+)/([^/\s]+)/([^/\s]+)/pulls?/(\d+)")


class GhError(Exception):
    pass


@dataclass(frozen=True)
class PrRef:
    host: str
    owner: str
    repo: str
    number: int

    @property
    def url(self) -> str:
        return f"https://{self.host}/{self.owner}/{self.repo}/pull/{self.number}"

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}#{self.number}"


def parse_pr_url(url: str) -> PrRef | None:
    m = URL_RE.match(url.strip())
    return PrRef(m.group(1), m.group(2), m.group(3), int(m.group(4))) if m else None


@dataclass
class Comment:
    author: str
    body: str
    created: str  # ISO 8601
    association: str = ""  # MEMBER, CONTRIBUTOR, ...
    id: int = 0
    # inline review comments
    path: str = ""
    line: int | None = None
    hunk: str = ""
    reply_to: int | None = None
    review_id: int | None = None
    replies: list["Comment"] = field(default_factory=list)


@dataclass
class Review:
    author: str
    state: str  # APPROVED, CHANGES_REQUESTED, COMMENTED, DISMISSED
    body: str
    submitted: str
    association: str = ""
    id: int = 0
    threads: list[Comment] = field(default_factory=list)  # inline comments started in this review


@dataclass
class Commit:
    sha: str
    message: str
    author: str
    date: str

    @property
    def subject(self) -> str:
        return self.message.split("\n", 1)[0]


@dataclass
class FileChange:
    path: str
    additions: int
    deletions: int
    status: str = "modified"


@dataclass
class PullRequest:
    ref: PrRef
    title: str
    body: str
    author: str
    state: str  # open, closed, merged, draft
    created: str
    base: str
    head: str
    association: str = ""
    merged_by: str = ""
    merged_at: str = ""
    closed_at: str = ""
    merge_sha: str = ""
    additions: int = 0
    deletions: int = 0
    labels: list[str] = field(default_factory=list)
    requested: list[str] = field(default_factory=list)  # reviewers asked who haven't reviewed
    assignees: list[str] = field(default_factory=list)
    comments: list[Comment] = field(default_factory=list)
    reviews: list[Review] = field(default_factory=list)
    commits: list[Commit] = field(default_factory=list)
    files: list[FileChange] = field(default_factory=list)
    diff: str = ""

    def reviewers(self) -> list[tuple[str, str]]:
        """(login, latest verdict) per reviewer, like GitHub's sidebar; requested ones are "PENDING"."""
        latest: dict[str, str] = {}
        for r in sorted(self.reviews, key=lambda r: r.submitted):
            if r.author == self.author:
                continue
            if r.state in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED") or r.author not in latest:
                latest[r.author] = r.state
        for login in self.requested:
            latest.setdefault(login, "PENDING")
        return list(latest.items())

    @property
    def comment_count(self) -> int:
        inline = sum(1 + len(t.replies) for r in self.reviews for t in r.threads)
        return len(self.comments) + sum(1 for r in self.reviews if r.body.strip()) + inline


# ------------------------------------------------------------------ fetching


def run_gh(args: list[str]) -> bytes:
    try:
        proc = subprocess.run(["gh", *args], stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
    except FileNotFoundError:
        raise GhError("the GitHub CLI 'gh' is not installed (https://cli.github.com)") from None
    except subprocess.TimeoutExpired:
        raise GhError("gh timed out") from None
    if proc.returncode != 0:
        msg = proc.stderr.decode(errors="replace").strip().splitlines()
        raise GhError("gh: " + (msg[-1] if msg else f"exit status {proc.returncode}"))
    return proc.stdout


def _decode_pages(data: bytes) -> list:
    """`gh api --paginate` prints one JSON array per page, back to back."""
    text = data.decode(errors="replace")
    dec = json.JSONDecoder()
    out: list = []
    pos = 0
    while True:
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos >= len(text):
            return out
        value, pos = dec.raw_decode(text, pos)
        out.extend(value if isinstance(value, list) else [value])


def _login(user) -> str:
    return (user or {}).get("login") or "ghost"


def fetch(url: str) -> PullRequest:
    ref = parse_pr_url(url)
    if ref is None:
        raise GhError(f"not a GitHub pull request URL: {url}")
    base = f"repos/{ref.owner}/{ref.repo}"
    host = [] if ref.host == "github.com" else ["--hostname", ref.host]

    def api(path: str, paginate: bool = False, accept: str = ""):
        args = ["api", *host]
        if accept:
            args += ["-H", f"Accept: {accept}"]
        if paginate:
            args += ["--paginate", f"{path}?per_page=100"]
            return _decode_pages(run_gh(args))
        return run_gh(args + [path])

    jobs = {
        "pr": lambda: json.loads(api(f"{base}/pulls/{ref.number}")),
        "comments": lambda: api(f"{base}/issues/{ref.number}/comments", True),
        "reviews": lambda: api(f"{base}/pulls/{ref.number}/reviews", True),
        "inline": lambda: api(f"{base}/pulls/{ref.number}/comments", True),
        "commits": lambda: api(f"{base}/pulls/{ref.number}/commits", True),
        "files": lambda: api(f"{base}/pulls/{ref.number}/files", True),
        "diff": lambda: api(f"{base}/pulls/{ref.number}", accept="application/vnd.github.diff"),
    }
    with ThreadPoolExecutor(len(jobs)) as pool:
        futures = {k: pool.submit(f) for k, f in jobs.items()}
        data = {}
        for k, fut in futures.items():
            try:
                data[k] = fut.result()
            except GhError:
                if k in ("pr", "comments", "reviews"):
                    raise
                data[k] = b"" if k == "diff" else []  # too large etc.: show the rest
    return build(ref, data)


def build(ref: PrRef, data: dict) -> PullRequest:
    """A `PullRequest` from the REST API's JSON (`data` keyed like the jobs in `fetch`)."""
    p = data["pr"]
    if p.get("merged_at"):
        state = "merged"
    elif p.get("state") == "closed":
        state = "closed"
    else:
        state = "draft" if p.get("draft") else "open"
    pr = PullRequest(
        ref=ref, title=p.get("title") or "", body=p.get("body") or "", author=_login(p.get("user")),
        state=state, created=p.get("created_at") or "",
        base=(p.get("base") or {}).get("label") or (p.get("base") or {}).get("ref") or "",
        head=(p.get("head") or {}).get("label") or (p.get("head") or {}).get("ref") or "",
        association=p.get("author_association") or "",
        merged_by=_login(p.get("merged_by")) if p.get("merged_by") else "",
        merged_at=p.get("merged_at") or "", closed_at=p.get("closed_at") or "",
        merge_sha=(p.get("merge_commit_sha") or "") if p.get("merged_at") else "",
        additions=p.get("additions") or 0, deletions=p.get("deletions") or 0,
        labels=[lab.get("name", "") for lab in p.get("labels") or []],
        requested=[_login(u) for u in p.get("requested_reviewers") or []],
        assignees=[_login(u) for u in p.get("assignees") or []],
    )
    pr.comments = [Comment(_login(c.get("user")), c.get("body") or "", c.get("created_at") or "",
                           c.get("author_association") or "", c.get("id") or 0)
                   for c in data.get("comments") or []]
    reviews = {}
    for r in data.get("reviews") or []:
        if r.get("state") == "PENDING":
            continue
        review = Review(_login(r.get("user")), r.get("state") or "COMMENTED", r.get("body") or "",
                        r.get("submitted_at") or "", r.get("author_association") or "", r.get("id") or 0)
        reviews[review.id] = review
        pr.reviews.append(review)
    inline = [Comment(_login(c.get("user")), c.get("body") or "", c.get("created_at") or "",
                      c.get("author_association") or "", c.get("id") or 0, c.get("path") or "",
                      c.get("line") or c.get("original_line"), c.get("diff_hunk") or "",
                      c.get("in_reply_to_id"), c.get("pull_request_review_id"))
              for c in data.get("inline") or []]
    by_id = {c.id: c for c in inline}
    for c in sorted(inline, key=lambda c: c.created):
        root = by_id.get(c.reply_to) if c.reply_to else None
        while root is not None and root.reply_to in by_id:
            root = by_id[root.reply_to]
        if root is not None:
            root.replies.append(c)
        elif c.review_id in reviews:
            reviews[c.review_id].threads.append(c)
        else:  # a review we didn't get: show the thread on its own
            review = Review(c.author, "COMMENTED", "", c.created, c.association, c.review_id or -c.id)
            reviews[review.id] = review
            review.threads.append(c)
            pr.reviews.append(review)
    for c in data.get("commits") or []:
        info = c.get("commit") or {}
        who = _login(c.get("author")) if c.get("author") else (info.get("author") or {}).get("name", "")
        pr.commits.append(Commit(c.get("sha") or "", info.get("message") or "", who,
                                 (info.get("committer") or info.get("author") or {}).get("date") or ""))
    pr.files = [FileChange(f.get("filename") or "", f.get("additions") or 0, f.get("deletions") or 0,
                           f.get("status") or "modified") for f in data.get("files") or []]
    diff = data.get("diff") or b""
    pr.diff = diff.decode(errors="replace") if isinstance(diff, bytes) else diff
    return pr


# ------------------------------------------------------------------ timeline


@dataclass
class Event:
    kind: str  # "comment", "review", "commits", "merged", "closed"
    when: str
    comment: Comment | None = None
    review: Review | None = None
    commits: list[Commit] = field(default_factory=list)


def timeline(pr: PullRequest) -> list[Event]:
    """The conversation after the description, oldest first. Consecutive commits are grouped;
    reviews without a body or threads of their own (just replies elsewhere) are left out."""
    events = [Event("comment", c.created, comment=c) for c in pr.comments]
    events += [Event("review", r.submitted, review=r) for r in pr.reviews
               if r.body.strip() or r.threads or r.state in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED")]
    events += [Event("commits", c.date, commits=[c]) for c in pr.commits]
    events.sort(key=lambda e: e.when)
    grouped: list[Event] = []
    for e in events:
        if e.kind == "commits" and grouped and grouped[-1].kind == "commits":
            grouped[-1].commits += e.commits
        else:
            grouped.append(e)
    if pr.state == "merged":
        grouped.append(Event("merged", pr.merged_at))
    elif pr.state == "closed":
        grouped.append(Event("closed", pr.closed_at))
    return grouped


def ago(iso: str, now: datetime | None = None) -> str:
    """"3 days ago", like GitHub (older than a month: "on Mar 4, 2025")."""
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    now = now or datetime.now(timezone.utc)
    s = int((now - t).total_seconds())
    if s < 60:
        return "just now"
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if s >= size:
            n = s // size
            if unit == "day" and n >= 30:
                return f"on {t.strftime('%b')} {t.day}, {t.year}"
            if unit == "day" and n == 1:
                return "yesterday"
            return f"{n} {unit}{'s' if n != 1 else ''} ago"
    return "just now"
