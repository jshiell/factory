import subprocess

import pytest

from factory.settings import load_settings


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("FACTORY_HOME", raising=False)
    return home


@pytest.fixture
def repo_dir(tmp_path):
    repo = (tmp_path / "repo").resolve()
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    return repo


@pytest.fixture
def global_config(isolated_home):
    path = isolated_home / ".harness" / "factory.toml"
    path.parent.mkdir()
    return path


def test_server_allowed_hosts_defaults_to_empty_and_comes_from_the_repo_config(repo_dir):
    assert load_settings(None, cwd=repo_dir).server_allowed_hosts == ()
    (repo_dir / "factory.toml").write_text('server_allowed_hosts = ["nas.lan", "192.168.1.5"]\n')

    assert load_settings(None, cwd=repo_dir).server_allowed_hosts == ("nas.lan", "192.168.1.5")
