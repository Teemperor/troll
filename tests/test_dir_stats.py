import json
import os

from troll import config
from troll.editor import Editor
from troll.settings import Settings


def new_editor(cwd) -> Editor:
    return Editor(Settings(), cwd=str(cwd), raise_errors=True)


def test_stats_live_in_the_cache_per_directory(tmp_path, user_files):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    path = config.dir_stats_path(str(a))
    assert path.startswith(str(user_files["cache"] / "troll"))
    assert path != config.dir_stats_path(str(b))
    assert path == config.dir_stats_path(str(a) + "/")


def test_opened_files_are_recorded_with_line_and_col(tmp_path):
    (tmp_path / "x.py").write_text("one\ntwo three\n")
    (tmp_path / "y.py").write_text("y\n")
    ed = new_editor(tmp_path)
    ed.open_file("x.py", 2, 5)
    ed.open_file("y.py")
    ed.open_file("missing.py")  # a new file isn't recorded
    files = config.load_dir_stats(str(tmp_path))["files"]
    assert [(os.path.basename(f["path"]), f["line"], f["col"]) for f in files] == [("y.py", 1, 1), ("x.py", 2, 5)]
    assert os.path.isabs(files[0]["path"]) and files[0]["time"] > 0
    new_editor(tmp_path).open_file("x.py")  # opening again moves it to the front
    assert [os.path.basename(f["path"]) for f in config.load_dir_stats(str(tmp_path))["files"]] == ["x.py", "y.py"]


def test_searches_are_recorded_and_become_the_next_sessions_history(tmp_path):
    (tmp_path / "x.py").write_text("alpha beta gamma\n")
    ed = new_editor(tmp_path)
    ed.open_file("x.py")
    for q in ["beta", "gamma", "beta"]:
        ed.keys("C-w")
        ed.type(q)
        ed.keys("Enter")
    ed.keys("C-t")
    ed.type("/alpha")
    ed.keys("Enter")
    assert config.load_dir_stats(str(tmp_path))["searches"] == ["alpha", "beta", "gamma"]

    ed2 = new_editor(tmp_path)
    ed2.open_file("x.py")
    ed2.keys("C-w", "Up")
    assert ed2.prompt.text == "alpha"
    ed2.keys("Up")
    assert ed2.prompt.text == "beta"


def test_other_directories_have_their_own_stats(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    config.record_search(str(a), "only in a")
    assert new_editor(a).search_history == ["only in a"]
    assert new_editor(b).search_history == []


def test_lists_are_capped(tmp_path):
    for i in range(config.MAX_RECENT_SEARCHES + 5):
        config.record_search(str(tmp_path), f"q{i}")
    searches = config.load_dir_stats(str(tmp_path))["searches"]
    assert len(searches) == config.MAX_RECENT_SEARCHES and searches[0] == f"q{config.MAX_RECENT_SEARCHES + 4}"


def test_broken_or_foreign_stats_files_are_ignored(tmp_path):
    path = config.dir_stats_path(str(tmp_path))
    os.makedirs(os.path.dirname(path))
    with open(path, "w") as f:
        f.write("{not json")
    assert config.load_dir_stats(str(tmp_path))["searches"] == []
    config.record_search(str(tmp_path), "x")  # rewrites it
    assert config.load_dir_stats(str(tmp_path))["searches"] == ["x"]
    with open(path, "w") as f:
        json.dump({"cwd": "/somewhere/else", "searches": ["y"]}, f)
    assert config.load_dir_stats(str(tmp_path))["searches"] == []


def test_undecodable_file_names_round_trip(tmp_path):
    name = os.path.join(str(tmp_path), b"caf\xe9.txt".decode(errors="surrogateescape"))
    config.record_open_file(str(tmp_path), name, 1, 1)
    assert config.load_dir_stats(str(tmp_path))["files"][0]["path"] == name
