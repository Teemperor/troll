import pytest

from troll import cli


def test_split_positions():
    assert cli.split_positions(["+12", "a.py", "b.py"]) == [("a.py", 12, None), ("b.py", None, None)]
    assert cli.split_positions(["+3,7", "a.py"]) == [("a.py", 3, 7)]
    assert cli.split_positions(["src/x.py:40:2"]) == [("src/x.py", 40, 2)]
    assert cli.split_positions(["x.cpp:104"]) == [("x.cpp", 104, None)]
    assert cli.split_positions(["x.cpp:"]) == [("x.cpp", None, None)]
    assert cli.split_positions(["x.cpp:104:"]) == [("x.cpp", 104, None)]
    assert cli.split_positions(["x.cpp:104:2:"]) == [("x.cpp", 104, 2)]
    assert cli.split_positions(["+5", "x.cpp:104"]) == [("x.cpp", 5, None)]


def test_split_positions_prefers_existing_files(tmp_path):
    for name in ("a.cpp:104", "b.cpp:", "c.cpp:1:2"):
        (tmp_path / name).write_text("")
    names = ["a.cpp:104", "b.cpp:", "c.cpp:1:2", "d.cpp:3"]
    assert cli.split_positions(names, str(tmp_path)) == [
        ("a.cpp:104", None, None), ("b.cpp:", None, None), ("c.cpp:1:2", None, None), ("d.cpp", 3, None)]


def test_setup_editor_opens_file_at_line_and_column(tmp_path):
    (tmp_path / "f.cpp").write_text("one\ntwo\nthree\n")
    args = cli.build_parser().parse_args(["-I", "-C", str(tmp_path), "f.cpp:3:2"])
    ed = cli.setup_editor(args)
    assert ed.doc.path.endswith("f.cpp") and ed.doc.cursor == (2, 1)


def test_setup_editor_opens_files_with_options(tmp_path):
    (tmp_path / "a.py").write_text("x\ny\n")
    args = cli.build_parser().parse_args(["-C", str(tmp_path), "-T", "2", "-L", "+2", "a.py"])
    ed = cli.setup_editor(args)
    assert ed.doc.cursor == (1, 0)
    assert ed.doc.settings.tab_size == 2 and not ed.doc.settings.line_numbers


def test_setup_editor_commit_mode(repo):
    repo.commit("base", {"a": "a\n"})
    repo.commit("second", {"a": "b\n"})
    ed = cli.setup_editor(cli.build_parser().parse_args(["-C", repo.path, "--commit", "HEAD"]))
    assert ed.commit is not None and ed.commit.commit.subject == "second"
    ed = cli.setup_editor(cli.build_parser().parse_args(["-C", repo.path, "--commit"]))
    assert ed.overlay is not None  # the commit picker


def test_git_main_maps_to_commit_mode(monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "main", lambda argv: seen.append(argv) or 0)
    cli.git_main(["HEAD~2", "-T", "4"])
    cli.git_main([])
    assert seen == [["--commit", "HEAD~2", "-T", "4"], ["--commit"]]


def test_main_reports_git_errors(tmp_path, capsys, git_env):
    assert cli.main(["-C", str(tmp_path), "--commit", "HEAD"]) == 1
    assert "not inside a git repository" in capsys.readouterr().err


def test_version(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert "troll" in capsys.readouterr().out
