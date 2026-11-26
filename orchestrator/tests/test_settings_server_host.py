import subprocess

import pytest

from configutil import write_config
from factory.settings import SettingsError, load_settings


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


def test_server_host_defaults_to_loopback_and_comes_from_the_repo_config(repo_dir):
    assert load_settings(None, cwd=repo_dir).server_host == "127.0.0.1"
    write_config(repo_dir, 'server_host = "0.0.0.0"\n')

    assert load_settings(None, cwd=repo_dir).server_host == "0.0.0.0"


def test_server_host_is_read_from_the_global_config(repo_dir, global_config):
    global_config.write_text('server_host = "0.0.0.0"\n')

    assert load_settings(None, cwd=repo_dir).server_host == "0.0.0.0"


def test_server_host_must_be_a_string(repo_dir):
    write_config(repo_dir, "server_host = 3\n")

    with pytest.raises(SettingsError, match="wrong type for setting: server_host"):
        load_settings(None, cwd=repo_dir)


@pytest.mark.parametrize("value", ["localhost", "192.168.1.5", "", "::"])
def test_server_host_other_than_the_two_bind_addresses_is_refused(repo_dir, value):
    write_config(repo_dir, f'server_host = "{value}"\n')

    with pytest.raises(SettingsError, match=r"server_host.*127\.0\.0\.1.*0\.0\.0\.0"):
        load_settings(None, cwd=repo_dir)
