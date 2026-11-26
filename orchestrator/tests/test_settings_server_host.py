import pytest

from configutil import write_config
from factory.settings import SettingsError, load_repo_settings


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("FACTORY_HOME", raising=False)
    return home


@pytest.fixture
def repo_dir(tmp_path):
    repo = (tmp_path / "fh" / "proj" / "repo").resolve()
    repo.mkdir(parents=True)
    return repo


def load(repo_dir):
    return load_repo_settings(repo_dir.parent.parent, repo_dir.parent.name)


@pytest.fixture
def global_config(repo_dir):
    return repo_dir.parent.parent / "factory.toml"


def test_server_host_defaults_to_loopback_and_comes_from_the_global_config(repo_dir, global_config):
    assert load(repo_dir).server_host == "127.0.0.1"
    global_config.write_text('server_host = "0.0.0.0"\n')

    assert load(repo_dir).server_host == "0.0.0.0"


@pytest.mark.parametrize("line", ['server_host = "0.0.0.0"', 'server_allowed_hosts = ["nas.lan"]', "server_port = 9000"])
def test_server_settings_are_refused_in_the_repo_config_naming_the_file(repo_dir, line):
    write_config(repo_dir, line + "\n")

    with pytest.raises(SettingsError, match=rf"{line.split()[0]} is global-only.*{repo_dir}.*factory\.toml"):
        load(repo_dir)


def test_server_host_must_be_a_string(repo_dir, global_config):
    global_config.write_text("server_host = 3\n")

    with pytest.raises(SettingsError, match="wrong type for setting: server_host"):
        load(repo_dir)


@pytest.mark.parametrize("value", ["localhost", "192.168.1.5", "", "::"])
def test_server_host_other_than_the_two_bind_addresses_is_refused(repo_dir, global_config, value):
    global_config.write_text(f'server_host = "{value}"\n')

    with pytest.raises(SettingsError, match=r"server_host.*127\.0\.0\.1.*0\.0\.0\.0"):
        load(repo_dir)
