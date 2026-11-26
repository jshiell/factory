import pytest

from factory.settings import SettingsError, load_repo_settings


@pytest.fixture(autouse=True)
def no_factory_home(monkeypatch):
    monkeypatch.delenv("FACTORY_HOME", raising=False)


@pytest.fixture
def home(tmp_path):
    (tmp_path / "home" / "proj" / "repo" / ".factory").mkdir(parents=True)
    return tmp_path / "home"


def repo_config(home, text):
    (home / "proj" / "repo" / ".factory" / "factory.toml").write_text(text)


def test_the_layout_is_fixed_under_the_home(home):
    settings = load_repo_settings(home, "proj")

    assert settings.repo == home / "proj" / "repo"
    assert settings.state_dir == home / "proj" / "state"
    assert settings.worktree_root == home / "proj" / "worktrees"
    assert settings.home == home


def test_the_global_config_is_the_base_and_the_repo_config_overrides_it(home):
    (home / "factory.toml").write_text('model = "opus"\nmax_turns = 7\n')
    repo_config(home, "max_turns = 9\n")

    settings = load_repo_settings(home, "proj")

    assert (settings.model, settings.max_turns) == ("opus", 9)


@pytest.mark.parametrize("key", ["repo", "state_dir", "worktree_root"])
def test_a_repo_config_may_not_move_the_layout(home, key):
    repo_config(home, f'{key} = "/elsewhere"\n')

    with pytest.raises(SettingsError, match=rf"{key} is not allowed in the repo config .*factory\.toml"):
        load_repo_settings(home, "proj")


def test_a_root_factory_toml_in_the_repo_is_refused(home):
    (home / "proj" / "repo" / "factory.toml").write_text("")

    with pytest.raises(SettingsError, match=r"no longer read"):
        load_repo_settings(home, "proj")


def test_old_in_repo_state_is_refused_with_where_to_move_it(home):
    (home / "proj" / "repo" / ".factory" / "factory.db").write_text("")

    with pytest.raises(SettingsError) as refused:
        load_repo_settings(home, "proj")

    assert str(home / "proj" / "state") in str(refused.value)
    assert "factory migrate" in str(refused.value)


def test_a_bad_project_subdir_is_refused(home):
    repo_config(home, 'project_subdir = "../up"\n')

    with pytest.raises(SettingsError, match="project_subdir"):
        load_repo_settings(home, "proj")


def test_the_old_target_default_still_warns(home):
    (home / "proj" / "repo" / "target").mkdir()

    assert "target/" in load_repo_settings(home, "proj").warnings[0]
