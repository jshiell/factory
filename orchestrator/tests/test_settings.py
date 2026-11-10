import subprocess
from pathlib import Path

import pytest

from factory.settings import Settings, SettingsError, load_settings


@pytest.fixture
def repo_dir(tmp_path):
    repo = (tmp_path / "repo").resolve()
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    return repo


def test_defaults_come_from_the_git_toplevel_of_the_cwd(repo_dir):
    (repo_dir / "sub").mkdir()

    settings = load_settings(None, cwd=repo_dir / "sub")

    assert settings.repo == repo_dir
    assert settings.state_dir == repo_dir / ".factory"
    assert settings.worktree_root == repo_dir / ".worktrees"
    assert settings.project_subdir == "target"
    assert settings.test_argv == ("uv", "run", "pytest", "-q")
    assert settings.claude_executable == "claude"
    assert settings.ledger_path == repo_dir / ".factory" / "ledger.jsonl"
    assert settings.tasks_dir == repo_dir / ".factory" / "tasks"


def test_outside_a_git_repository_without_a_configured_repo_is_an_error(tmp_path):
    with pytest.raises(SettingsError, match="repo"):
        load_settings(None, cwd=tmp_path)


def test_factory_toml_in_the_cwd_is_used_when_no_path_is_given(repo_dir):
    (repo_dir / "factory.toml").write_text('cap_usd = 9\nproject_subdir = "app"\n')

    settings = load_settings(None, cwd=repo_dir)

    assert (settings.cap_usd, settings.project_subdir) == (9.0, "app")


def test_only_repo_state_and_worktree_paths_resolve_against_the_config_directory(tmp_path, repo_dir):
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    (config_dir / "f.toml").write_text(
        f'repo = "{repo_dir}"\nstate_dir = "state"\nworktree_root = "../wt"\nproject_subdir = "proj"\n'
    )

    settings = load_settings(config_dir / "f.toml", cwd=tmp_path)

    assert settings.state_dir == config_dir / "state"
    assert settings.worktree_root == (config_dir / ".." / "wt")
    assert settings.project_subdir == "proj"


def test_an_explicit_missing_config_file_raises(repo_dir):
    with pytest.raises(SettingsError, match="missing.toml"):
        load_settings(repo_dir / "missing.toml", cwd=repo_dir)


@pytest.mark.parametrize(
    "line,key",
    [
        ("bogus = 1", "bogus"),
        ('cap_usd = "five"', "cap_usd"),
        ("max_turns = 1.5", "max_turns"),
        ("allowed_tools = [1]", "allowed_tools"),
        ("test_argv = []", "test_argv"),
        ('project_subdir = "/abs"', "project_subdir"),
        ('project_subdir = "a/../b"', "project_subdir"),
        ('project_subdir = ""', "project_subdir"),
        ("cap_usd = true", "cap_usd"),
    ],
)
def test_bad_settings_are_rejected_with_a_message_naming_the_key(repo_dir, line, key):
    (repo_dir / "factory.toml").write_text(line + "\n")

    with pytest.raises(SettingsError, match=key):
        load_settings(None, cwd=repo_dir)


def test_sandbox_executable_defaults_to_nono_and_can_be_set(repo_dir):
    assert load_settings(None, cwd=repo_dir).sandbox_executable == "nono"
    (repo_dir / "factory.toml").write_text('sandbox_executable = "/opt/fake-nono"\n')

    assert load_settings(None, cwd=repo_dir).sandbox_executable == "/opt/fake-nono"


def test_a_non_string_sandbox_executable_is_rejected_naming_the_key(repo_dir):
    (repo_dir / "factory.toml").write_text("sandbox_executable = 3\n")

    with pytest.raises(SettingsError, match="sandbox_executable"):
        load_settings(None, cwd=repo_dir)


def test_fix_rounds_defaults_to_two_and_accepts_zero_and_more(repo_dir):
    assert load_settings(None, cwd=repo_dir).fix_rounds == 2
    for value in (0, 3):
        (repo_dir / "factory.toml").write_text(f"fix_rounds = {value}\n")
        assert load_settings(None, cwd=repo_dir).fix_rounds == value


@pytest.mark.parametrize("value", ["-1", "1.5", '"2"', "true"])
def test_a_bad_fix_rounds_is_rejected_naming_the_key(repo_dir, value):
    (repo_dir / "factory.toml").write_text(f"fix_rounds = {value}\n")

    with pytest.raises(SettingsError, match="fix_rounds"):
        load_settings(None, cwd=repo_dir)


def test_gate_cache_dir_is_under_the_state_dir(repo_dir):
    settings = load_settings(None, cwd=repo_dir)

    assert settings.gate_cache_dir == settings.state_dir / "gate-cache"


def test_server_port_defaults_to_8765_and_comes_from_the_config(tmp_path):
    from factory.settings import load_settings
    config = tmp_path / "factory.toml"
    config.write_text(f'repo = "{tmp_path}"\n')
    assert load_settings(config, tmp_path).server_port == 8765

    config.write_text(f'repo = "{tmp_path}"\nserver_port = 9001\n')
    assert load_settings(config, tmp_path).server_port == 9001
