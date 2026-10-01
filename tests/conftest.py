import os
import subprocess

import pytest

from troll.document import Document
from troll.editor import Editor
from troll.settings import Settings


@pytest.fixture(autouse=True)
def user_files(monkeypatch, tmp_path):
    """Keep tests away from the real settings file and position log."""
    paths = {"config": tmp_path / "user" / "config", "state": tmp_path / "user" / "state"}
    monkeypatch.setenv("TROLL_CONFIG", str(paths["config"]))
    monkeypatch.setenv("XDG_STATE_HOME", str(paths["state"]))
    return paths


@pytest.fixture
def editor(tmp_path):
    return Editor(Settings(), cwd=str(tmp_path), raise_errors=True)


def make_doc(text: str, path: str | None = None, cursor=(0, 0), **settings) -> Document:
    s = Settings()
    for k, v in settings.items():
        setattr(s, k, v)
    doc = Document(text, path=path, settings=s)
    for k, v in settings.items():  # beat language defaults / detection
        setattr(doc.settings, k, v)
    doc.set_cursor(cursor)
    return doc


def editor_with(editor: Editor, text: str, path: str = "t.py", cursor=(0, 0), **settings) -> Document:
    doc = make_doc(text, path=os.path.join(editor.cwd, path), cursor=cursor, **settings)
    editor.add_doc(doc)
    return doc


# ----------------------------------------------------------------- git repos


@pytest.fixture
def git_env(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for k, v in {
        "GIT_AUTHOR_NAME": "Ada Author",
        "GIT_AUTHOR_EMAIL": "ada@example.com",
        "GIT_COMMITTER_NAME": "Carl Committer",
        "GIT_COMMITTER_EMAIL": "carl@example.com",
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("GIT_DIR", raising=False)
    monkeypatch.delenv("GIT_WORK_TREE", raising=False)
    monkeypatch.delenv("GIT_INDEX_FILE", raising=False)


class Repo:
    def __init__(self, path):
        self.path = str(path)
        self.git("init", "-q", "-b", "main")

    def git(self, *args, check=True, input=None) -> str:
        proc = subprocess.run(["git", *args], cwd=self.path, capture_output=True, text=True, input=input)
        if check and proc.returncode != 0:
            raise AssertionError(f"git {args} failed: {proc.stderr}")
        return proc.stdout.strip()

    def write(self, name: str, text: str) -> None:
        full = os.path.join(self.path, name)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", newline="") as f:
            f.write(text)

    def read(self, name: str) -> str:
        with open(os.path.join(self.path, name), newline="") as f:
            return f.read()

    def commit(self, message: str, files: dict[str, str] | None = None, date: str = "1700000000 +0100") -> str:
        for name, text in (files or {}).items():
            self.write(name, text)
        self.git("add", "-A")
        env = dict(os.environ, GIT_AUTHOR_DATE=f"@{date}", GIT_COMMITTER_DATE=f"@{date}")
        proc = subprocess.run(["git", "commit", "-q", "-m", message], cwd=self.path, capture_output=True, text=True, env=env)
        assert proc.returncode == 0, proc.stderr
        return self.git("rev-parse", "HEAD")

    def show(self, rev: str, path: str) -> str:
        return self.git("show", f"{rev}:{path}")

    def log(self) -> list[str]:
        return self.git("log", "--format=%s").splitlines()


@pytest.fixture
def repo(git_env, tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    return Repo(path)
