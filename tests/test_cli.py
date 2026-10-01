import pytest

from troll import cli


def test_split_positions():
    assert cli.split_positions(["+12", "a.py", "b.py"]) == [("a.py", 12, None), ("b.py", None, None)]
    assert cli.split_positions(["+3,7", "a.py"]) == [("a.py", 3, 7)]
    assert cli.split_positions(["src/x.py:40:2"]) == [("src/x.py", 40, 2)]


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
