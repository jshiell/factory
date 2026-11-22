"""A numeric `server_allowed_hosts` entry must be a real dotted-quad IPv4 address."""
import subprocess

import pytest

from factory.settings import SettingsError, load_settings


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("FACTORY_HOME", raising=False)


@pytest.fixture
def repo_dir(tmp_path):
    repo = (tmp_path / "repo").resolve()
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    return repo


def load_with(repo_dir, host):
    (repo_dir / "factory.toml").write_text(f'server_allowed_hosts = ["{host}"]\n')
    return load_settings(None, cwd=repo_dir)


@pytest.mark.parametrize("host", ["1.2.3", "1.2.3.4.5", "256.1.1.1", "1.2.3.999", "10.01.1.1", "123", "1..2.3"])
def test_a_numeric_entry_that_is_not_an_ipv4_address_is_a_settings_error(repo_dir, host):
    with pytest.raises(SettingsError, match="server_allowed_hosts"):
        load_with(repo_dir, host)


@pytest.mark.parametrize("host", ["1.2.3.4", "192.168.1.5", "nas-1.lan", "nas.lan", "1nas.lan", "10.lan"])
def test_an_ipv4_address_or_a_hostname_is_accepted(repo_dir, host):
    assert load_with(repo_dir, host).server_allowed_hosts == (host,)
