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
    repo = tmp_path / "repo"
    (repo / "target").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    env = {key: value for key, value in os.environ.items() if key != "FACTORY_HOME"} | {"HOME": str(tmp_path)}

    result = _in_a_fresh_interpreter("-m", "factory.cli", "--help", cwd=repo, env=env)

    assert result.returncode == 0, result.stderr
    assert result.stderr.startswith("settings warning: project_subdir is unset")
    assert "target/" not in result.stdout
