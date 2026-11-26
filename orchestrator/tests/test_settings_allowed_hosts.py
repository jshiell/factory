import json
import re

import pytest

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


def test_server_allowed_hosts_defaults_to_empty_and_comes_from_the_global_config(repo_dir, global_config):
    assert load(repo_dir).server_allowed_hosts == ()
    global_config.write_text('server_allowed_hosts = ["nas.lan", "192.168.1.5"]\n')

    assert load(repo_dir).server_allowed_hosts == ("nas.lan", "192.168.1.5")


def test_server_allowed_hosts_is_read_from_the_global_config(repo_dir, global_config):
    global_config.write_text('server_allowed_hosts = ["nas.lan"]\n')

    assert load(repo_dir).server_allowed_hosts == ("nas.lan",)


@pytest.mark.parametrize("value", ['"nas.lan"', "3", "[]"])
def test_server_allowed_hosts_must_be_a_non_empty_list_of_strings(repo_dir, global_config, value):
    global_config.write_text(f"server_allowed_hosts = {value}\n")

    with pytest.raises(SettingsError, match="wrong type for setting: server_allowed_hosts"):
        load(repo_dir)


@pytest.mark.parametrize(
    "entries",
    [
        [""], ["*"], ["a b"], ["h:80"], ["http://h"], ["Example.com"], ["::1"], ["[::1]"], ["nas.lan."],
        ["0.0.0.0"], ["127.0.0.1"], ["localhost"], ["nas.lan", "nas.lan"],
    ],
)
def test_invalid_server_allowed_hosts_entries_are_refused(repo_dir, global_config, entries):
    global_config.write_text(f"server_allowed_hosts = {json.dumps(entries)}\n")

    with pytest.raises(SettingsError, match=f"server_allowed_hosts {re.escape(repr(entries[-1]))} is invalid: ."):
        load(repo_dir)
