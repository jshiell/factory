import os
import subprocess
import sys


def _in_a_fresh_interpreter(*args: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", *args], capture_output=True, text=True, **kwargs)


def test_the_server_imports_on_its_own_before_the_cli_is_loaded():
    # Every other test imports factory.cli first, so an import cycle between the server and the cli would hide.
    result = _in_a_fresh_interpreter("-c", "import factory.server")

    assert result.returncode == 0, result.stderr


def test_the_cli_runs_as_a_module():
    result = _in_a_fresh_interpreter("-m", "factory.cli", "--help")

    assert result.returncode == 0, result.stderr
    assert "usage: factory" in result.stdout


def test_the_cli_prints_settings_warnings_to_stderr(tmp_path):
    home = tmp_path / "home"
    repo = home / "proj" / "repo"
    (repo / "target").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    env = {key: value for key, value in os.environ.items() if key != "FACTORY_HOME"} | {"FACTORY_HOME": str(home)}

    result = _in_a_fresh_interpreter("-m", "factory.cli", "eval-audit", "--runs", "0", cwd=repo, env=env)  # no server runs, so it stops there

    assert result.stderr.startswith("settings warning: project_subdir is unset")
    assert "target/" not in result.stdout


def _factory_home(tmp_path):
    home = tmp_path / "home"
    repo = home / "proj" / "repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    env = {key: value for key, value in os.environ.items() if key != "FACTORY_HOME"} | {"FACTORY_HOME": str(home)}
    return home, repo, env


def test_an_unknown_command_is_a_usage_error_with_the_web_ui_hint_even_outside_any_repo(tmp_path):
    _, _, env = _factory_home(tmp_path)
    outside = tmp_path / "elsewhere"
    outside.mkdir()

    result = _in_a_fresh_interpreter("-m", "factory.cli", "watch", cwd=outside, env=env)

    assert result.returncode == 2
    assert "usage error" in result.stderr and "factory ui" in result.stderr
    assert "no repo" not in result.stderr


def test_an_unknown_command_inside_a_repo_creates_no_state(tmp_path):
    home, repo, env = _factory_home(tmp_path)
    before = sorted(home.rglob("*"))

    result = _in_a_fresh_interpreter("-m", "factory.cli", "watch", cwd=repo, env=env)

    assert result.returncode == 2 and "factory ui" in result.stderr
    assert sorted(home.rglob("*")) == before
