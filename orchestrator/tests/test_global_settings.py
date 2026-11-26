import subprocess

import pytest

from factory.settings import (
    GlobalSettings,
    SettingsError,
    load_global_settings,
    load_settings,
)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("FACTORY_HOME", raising=False)
    return home


@pytest.fixture
def global_config(isolated_home):
    path = isolated_home / ".harness" / "factory.toml"
    path.parent.mkdir()
    return path


def test_global_settings_default_to_the_harness_home_and_loopback_on_8765(isolated_home):
    assert load_global_settings() == GlobalSettings(
        home=isolated_home / ".harness", server_port=8765, server_host="127.0.0.1", server_allowed_hosts=(),
    )


def test_global_settings_read_the_server_keys_from_the_global_config(global_config, isolated_home):
    global_config.write_text('server_port = 9001\nserver_host = "0.0.0.0"\nserver_allowed_hosts = ["nas.lan"]\n')

    assert load_global_settings() == GlobalSettings(
        home=isolated_home / ".harness", server_port=9001, server_host="0.0.0.0", server_allowed_hosts=("nas.lan",),
    )


def test_the_home_comes_from_factory_home_before_the_configured_home(global_config, tmp_path, monkeypatch):
    global_config.write_text(f'home = "{tmp_path / "configured"}"\n')
    assert load_global_settings().home == tmp_path / "configured"

    monkeypatch.setenv("FACTORY_HOME", str(global_config.parent))
    assert load_global_settings().home == global_config.parent


@pytest.mark.parametrize("line", ['server_host = "localhost"', 'server_allowed_hosts = ["Bad Host"]'])
def test_an_invalid_server_setting_is_a_settings_error(global_config, line):
    global_config.write_text(line + "\n")

    with pytest.raises(SettingsError):
        load_global_settings()


def test_repo_settings_carry_the_home(isolated_home, tmp_path):
    repo = (tmp_path / "repo").resolve()
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)

    assert load_settings(None, cwd=repo).home == isolated_home / ".harness"
