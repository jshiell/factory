import subprocess

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
def factory_home(tmp_path):
    return (tmp_path / "fh").resolve()


@pytest.fixture
def repo_dir(factory_home):
    repo = factory_home / "proj" / "repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    return repo


def load(repo_dir, config=None):
    return load_repo_settings(repo_dir.parent.parent, repo_dir.parent.name, config)


@pytest.mark.smoke
def test_defaults_come_from_the_repo_and_its_project_directory_in_the_home(repo_dir, factory_home):
    settings = load(repo_dir)

    assert settings.repo == repo_dir
    project = factory_home / "proj"
    assert settings.state_dir == project / "state"
    assert settings.worktree_root == project / "worktrees"
    assert settings.project_subdir == "."
    assert settings.test_argv == ("uv", "run", "pytest", "-q")
    assert settings.claude_executable == "claude"
    assert settings.ledger_path == project / "state" / "ledger.jsonl"
    assert settings.tasks_dir == project / "state" / "tasks"


def test_an_unset_project_subdir_warns_when_the_repo_has_the_old_default_target(repo_dir):
    (repo_dir / "target").mkdir()

    [warning] = load(repo_dir).warnings

    assert "target/" in warning and 'project_subdir = "target"' in warning and ".factory/factory.toml" in warning


def test_no_project_subdir_warning_without_a_target_directory(repo_dir):
    (repo_dir / "target").write_text("a file, not the old project")

    assert load(repo_dir).warnings == ()


@pytest.mark.parametrize("value", ["target", "."])
def test_no_project_subdir_warning_when_the_project_config_sets_it(repo_dir, value):
    (repo_dir / "target").mkdir()
    write_config(repo_dir, f'project_subdir = "{value}"\n')

    assert load(repo_dir).warnings == ()


def test_no_project_subdir_warning_when_the_global_config_sets_it(repo_dir, global_config):
    (repo_dir / "target").mkdir()
    global_config.write_text('project_subdir = "target"\n')

    assert load(repo_dir).warnings == ()


def test_the_dot_factory_config_is_used_when_no_path_is_given(repo_dir):
    write_config(repo_dir, 'cap_usd = 9\nproject_subdir = "app"\n')

    settings = load(repo_dir)

    assert (settings.cap_usd, settings.project_subdir) == (9.0, "app")


def test_a_root_factory_toml_fails_with_the_git_mv_fix(repo_dir):
    (repo_dir / "factory.toml").write_text("cap_usd = 7\n")

    with pytest.raises(SettingsError) as raised:
        load(repo_dir)

    message = str(raised.value)
    assert str(repo_dir / "factory.toml") in message
    assert "git mv factory.toml .factory/factory.toml" in message
    assert "gitignore" in message


def test_home_is_refused_in_the_repo_config_naming_the_file(repo_dir):
    write_config(repo_dir, 'home = "h"\n')

    with pytest.raises(SettingsError, match=rf"home is global-only.*{repo_dir}.*factory\.toml"):
        load(repo_dir)


def test_an_explicit_config_inside_the_repo_but_outside_dot_factory_is_refused(repo_dir):
    (repo_dir / "conf").mkdir()
    (repo_dir / "conf" / "f.toml").write_text("cap_usd = 7\n")

    with pytest.raises(SettingsError, match=r"conf/f\.toml.*\.factory"):
        load(repo_dir, repo_dir / "conf" / "f.toml")


def test_an_explicit_config_inside_dot_factory_is_accepted(repo_dir):
    (repo_dir / ".factory").mkdir()
    (repo_dir / ".factory" / "other.toml").write_text("cap_usd = 7\n")

    assert load(repo_dir, repo_dir / ".factory" / "other.toml").cap_usd == 7.0


@pytest.mark.parametrize("name", ["factory.db", "token"])
def test_old_in_repo_state_fails_naming_where_to_move_it(repo_dir, factory_home, name):
    (repo_dir / ".factory").mkdir()
    (repo_dir / ".factory" / name).write_text("")

    with pytest.raises(SettingsError) as raised:
        load(repo_dir)

    message = str(raised.value)
    assert str(repo_dir / ".factory" / name) in message
    assert str(factory_home / "proj" / "state") in message


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
    write_config(repo_dir, line + "\n")

    with pytest.raises(SettingsError, match=key):
        load(repo_dir)


def test_sandbox_executable_defaults_to_unset_and_can_be_set(repo_dir):
    assert load(repo_dir).sandbox_executable is None
    write_config(repo_dir, 'sandbox_executable = "/opt/fake-nono"\n')

    assert load(repo_dir).sandbox_executable == "/opt/fake-nono"


def test_a_non_string_sandbox_executable_is_rejected_naming_the_key(repo_dir):
    write_config(repo_dir, "sandbox_executable = 3\n")

    with pytest.raises(SettingsError, match="sandbox_executable"):
        load(repo_dir)


def test_fix_rounds_defaults_to_two_and_accepts_zero_and_more(repo_dir):
    assert load(repo_dir).fix_rounds == 2
    for value in (0, 3):
        write_config(repo_dir, f"fix_rounds = {value}\n")
        assert load(repo_dir).fix_rounds == value


@pytest.mark.parametrize("value", ["-1", "1.5", '"2"', "true"])
def test_a_bad_fix_rounds_is_rejected_naming_the_key(repo_dir, value):
    write_config(repo_dir, f"fix_rounds = {value}\n")

    with pytest.raises(SettingsError, match="fix_rounds"):
        load(repo_dir)


def test_grilling_is_on_by_default_with_three_rounds_and_both_come_from_the_config(repo_dir):
    defaults = load(repo_dir)
    assert (defaults.grill, defaults.grill_max_rounds) == (True, 3)

    write_config(repo_dir, "grill = false\ngrill_max_rounds = 0\n")
    configured = load(repo_dir)

    assert (configured.grill, configured.grill_max_rounds) == (False, 0)


@pytest.mark.parametrize("line, key", [
    ('grill = "no"', "grill"), ("grill = 1", "grill"),
    ("grill_max_rounds = -1", "grill_max_rounds"), ("grill_max_rounds = true", "grill_max_rounds"),
    ('grill_max_rounds = "3"', "grill_max_rounds"),
])
def test_a_bad_grill_setting_is_rejected_naming_the_key(repo_dir, line, key):
    write_config(repo_dir, line + "\n")

    with pytest.raises(SettingsError, match=key):
        load(repo_dir)


def test_gate_cache_dir_is_under_the_state_dir(repo_dir):
    settings = load(repo_dir)

    assert settings.gate_cache_dir == settings.state_dir / "gate-cache"


def test_server_port_defaults_to_8765_and_comes_from_the_global_config(repo_dir, global_config):
    assert load(repo_dir).server_port == 8765

    global_config.write_text("server_port = 9001\n")
    assert load(repo_dir).server_port == 9001


def test_a_non_string_home_in_the_global_config_is_rejected_naming_the_key(repo_dir, global_config):
    global_config.write_text("home = 3\n")

    with pytest.raises(SettingsError, match="home"):
        load(repo_dir)


def test_append_system_prompt_defaults_to_none_and_can_be_set(repo_dir):
    assert load(repo_dir).append_system_prompt is None
    write_config(repo_dir, 'append_system_prompt = "Be terse."\n')

    assert load(repo_dir).append_system_prompt == "Be terse."


@pytest.mark.parametrize("value", ['""', "3"])
def test_a_bad_append_system_prompt_is_rejected_naming_the_key(repo_dir, value):
    write_config(repo_dir, f"append_system_prompt = {value}\n")

    with pytest.raises(SettingsError, match="append_system_prompt"):
        load(repo_dir)


def test_stage_prompts_default_to_empty_and_can_be_set_per_stage(repo_dir):
    assert load(repo_dir).stage_prompts == {}
    write_config(repo_dir, '[stage_prompts]\nplan = "Plan well."\naudit = "Be strict."\n')

    assert load(repo_dir).stage_prompts == {"plan": "Plan well.", "audit": "Be strict."}


@pytest.mark.parametrize(
    "toml",
    [
        'stage_prompts = "x"',
        '[stage_prompts]\nbogus = "x"',
        '[stage_prompts]\nplan = ""',
        "[stage_prompts]\nplan = 3",
    ],
)
def test_bad_stage_prompts_are_rejected_naming_the_key(repo_dir, toml):
    write_config(repo_dir, toml + "\n")

    with pytest.raises(SettingsError, match="stage_prompts"):
        load(repo_dir)


@pytest.fixture
def global_config(factory_home):
    return factory_home / "factory.toml"


def test_the_global_config_applies_when_there_is_no_local_file(repo_dir, global_config):
    global_config.write_text('cap_usd = 7\nmodel = "opus"\n')

    settings = load(repo_dir)

    assert (settings.cap_usd, settings.model) == (7.0, "opus")


def test_a_local_key_overrides_the_global_one_and_other_global_keys_survive(repo_dir, global_config):
    global_config.write_text('cap_usd = 7\nmodel = "opus"\n')
    write_config(repo_dir, "cap_usd = 2\n")

    settings = load(repo_dir)

    assert (settings.cap_usd, settings.model) == (2.0, "opus")


def test_stage_prompts_merge_per_stage_with_the_local_one_winning(repo_dir, global_config):
    global_config.write_text('[stage_prompts]\nplan = "G plan."\naudit = "G audit."\n')
    write_config(repo_dir, '[stage_prompts]\nplan = "L plan."\n')

    settings = load(repo_dir)

    assert settings.stage_prompts == {"plan": "L plan.", "audit": "G audit."}


def test_the_global_config_in_a_home_kept_under_git_is_not_a_root_factory_toml(repo_dir, global_config):
    home = global_config.parent
    subprocess.run(["git", "init", "-q"], cwd=home, check=True)
    global_config.write_text('model = "opus"\n')
    write_config(repo_dir, "cap_usd = 3\n")

    settings = load(repo_dir)

    assert (settings.model, settings.cap_usd, settings.state_dir) == ("opus", 3.0, home / "proj" / "state")


@pytest.mark.parametrize("key", ["repo", "state_dir", "worktree_root"])
def test_per_repo_paths_are_rejected_in_the_global_config(repo_dir, global_config, key):
    global_config.write_text(f'{key} = "x"\n')

    with pytest.raises(SettingsError, match=f"{key}.*global|global.*{key}"):
        load(repo_dir)


def test_watch_approvers_are_rejected_in_the_global_config(repo_dir, global_config):
    key = "watch_approvers"
    global_config.write_text(f'{key} = ["alice"]\n')

    with pytest.raises(SettingsError, match=f"{key}.*global|global.*{key}"):
        load(repo_dir)


def test_watch_merge_is_rejected_in_the_global_config(repo_dir, global_config):
    key = "watch_merge"
    global_config.write_text(f"{key} = true\n")

    with pytest.raises(SettingsError, match=f"{key}.*global|global.*{key}"):
        load(repo_dir)


def test_watch_retry_is_rejected_in_the_global_config(repo_dir, global_config):
    key = "watch_retry"
    global_config.write_text(f"{key} = true\n")

    with pytest.raises(SettingsError, match=f"{key}.*global|global.*{key}"):
        load(repo_dir)


def test_main_branch_is_rejected_in_the_global_config(repo_dir, global_config):
    key = "main_branch"
    global_config.write_text(f'{key} = "main"\n')

    with pytest.raises(SettingsError, match=f"{key}.*global|global.*{key}"):
        load(repo_dir)


@pytest.mark.parametrize("content", ["bogus = 1\n", "cap_usd = 'x'\n"])
def test_a_bad_global_config_names_the_file_in_its_error(repo_dir, global_config, content):
    global_config.write_text(content)

    with pytest.raises(SettingsError, match=str(global_config)):
        load(repo_dir)


def test_plugins_default_to_none_and_come_from_the_config_with_local_replacing_global(repo_dir, global_config):
    assert load(repo_dir).plugins == ()
    for name in "abc":
        (global_config.parent / "_shared" / "plugins" / name).mkdir(parents=True)
    global_config.write_text('plugins = ["a", "b"]\n')
    assert load(repo_dir).plugins == ("a", "b")
    write_config(repo_dir, 'plugins = ["c"]\n')

    assert load(repo_dir).plugins == ("c",)


@pytest.mark.parametrize("value", ["[]", '"a"', "[1]", '["../x"]', '["a/b"]', '[""]', '["."]', '[".."]', '["a", "a"]'])
def test_bad_plugin_names_are_rejected_naming_the_key(repo_dir, value):
    write_config(repo_dir, f"plugins = {value}\n")

    with pytest.raises(SettingsError, match="wrong type for setting: plugins"):
        load(repo_dir)


def test_plugin_names_resolve_to_directories_under_the_shared_plugins_dir(repo_dir, global_config):
    shared = global_config.parent / "_shared" / "plugins"
    (shared / "a").mkdir(parents=True)
    (shared / "b").mkdir()
    write_config(repo_dir, 'plugins = ["a", "b"]\n')

    assert load(repo_dir).plugin_dirs == (shared / "a", shared / "b")


def test_a_missing_plugin_directory_is_an_error_naming_it(repo_dir, global_config):
    write_config(repo_dir, 'plugins = ["nope"]\n')

    with pytest.raises(SettingsError, match="nope"):
        load(repo_dir)


def make_plugins(home, *names):
    for name in names:
        (home / "_shared" / "plugins" / name).mkdir(parents=True, exist_ok=True)


def test_stage_plugins_default_to_empty_and_can_be_set_per_stage(repo_dir, factory_home):
    assert load(repo_dir).stage_plugins == {}
    make_plugins(factory_home, "a", "b", "c")
    write_config(repo_dir, '[stage_plugins]\nreview = ["a", "b"]\naudit = ["c"]\n')

    assert load(repo_dir).stage_plugins == {"review": ("a", "b"), "audit": ("c",)}


@pytest.mark.parametrize(
    "toml",
    [
        'stage_plugins = "x"',
        '[stage_plugins]\nbogus = ["a"]',
        "[stage_plugins]\nreview = []",
        '[stage_plugins]\nreview = "a"',
        '[stage_plugins]\nreview = ["../x"]',
        '[stage_plugins]\nreview = ["a", "a"]',
    ],
)
def test_bad_stage_plugins_are_rejected_naming_the_key(repo_dir, toml):
    write_config(repo_dir, toml + "\n")

    with pytest.raises(SettingsError, match="wrong type for setting: stage_plugins"):
        load(repo_dir)


def test_stage_plugins_merge_per_stage_with_the_local_list_replacing_the_global_one(repo_dir, global_config):
    make_plugins(global_config.parent, "a", "b", "c")
    global_config.write_text('[stage_plugins]\nreview = ["a"]\naudit = ["b"]\n')
    write_config(repo_dir, '[stage_plugins]\nreview = ["c"]\n')

    assert load(repo_dir).stage_plugins == {"review": ("c",), "audit": ("b",)}


def test_stage_plugin_names_resolve_to_directories_per_stage(repo_dir, factory_home):
    make_plugins(factory_home, "a", "b")
    shared = factory_home / "_shared" / "plugins"
    write_config(repo_dir, '[stage_plugins]\nreview = ["a", "b"]\naudit = ["b"]\n')

    settings = load(repo_dir)

    assert settings.stage_plugin_dirs == {"review": (shared / "a", shared / "b"), "audit": (shared / "b",)}
    assert load(repo_dir).plugin_dirs == ()


def test_a_missing_stage_plugin_directory_is_an_error_naming_it(repo_dir, factory_home):
    write_config(repo_dir, '[stage_plugins]\nreview = ["nope"]\n')

    with pytest.raises(SettingsError, match="nope"):
        load(repo_dir)


@pytest.mark.parametrize(
    "setup_plugin,reason",
    [
        (lambda d: (d / "hooks").mkdir(), "hooks"),
        (lambda d: (d / ".mcp.json").write_text("{}"), ".mcp.json"),
        (lambda d: (d / ".claude-plugin").mkdir() or (d / ".claude-plugin" / "plugin.json").write_text('{"hooks": {}}'), "hooks"),
        (lambda d: (d / ".claude-plugin").mkdir() or (d / ".claude-plugin" / "plugin.json").write_text('{"mcpServers": {}}'), "mcpServers"),
    ],
)
@pytest.mark.parametrize("toml", ['plugins = ["bad"]', '[stage_plugins]\nreview = ["bad"]'])
def test_a_plugin_with_hooks_or_mcp_servers_is_refused_naming_it(repo_dir, factory_home, toml, setup_plugin, reason):
    make_plugins(factory_home, "bad")
    setup_plugin(factory_home / "_shared" / "plugins" / "bad")
    write_config(repo_dir, toml + "\n")

    with pytest.raises(SettingsError, match=f"bad.*{reason}"):
        load(repo_dir)


@pytest.mark.parametrize("relative", ["skills/s/SKILL.md", "agents/a.md"])
def test_a_skill_or_agent_with_frontmatter_hooks_is_refused_naming_it(repo_dir, factory_home, relative):
    make_plugins(factory_home, "bad")
    plugin = factory_home / "_shared" / "plugins" / "bad"
    (plugin / relative).parent.mkdir(parents=True)
    (plugin / relative).write_text("---\nname: x\nhooks:\n  PreToolUse: []\n---\nBody\n")
    write_config(repo_dir, 'plugins = ["bad"]\n')

    with pytest.raises(SettingsError, match=f"bad.*frontmatter hooks.*{relative}"):
        load(repo_dir)


def test_a_skill_that_only_mentions_hooks_outside_its_frontmatter_is_accepted(repo_dir, factory_home):
    make_plugins(factory_home, "ok")
    plugin = factory_home / "_shared" / "plugins" / "ok"
    (plugin / "skills" / "s").mkdir(parents=True)
    (plugin / "skills" / "s" / "SKILL.md").write_text("---\nname: s\n---\nhooks:\n  are discussed here\n")
    write_config(repo_dir, 'plugins = ["ok"]\n')

    assert load(repo_dir).plugin_dirs == (plugin,)


def test_a_plugin_with_an_unreadable_manifest_is_refused(repo_dir, factory_home):
    make_plugins(factory_home, "bad")
    plugin = factory_home / "_shared" / "plugins" / "bad"
    (plugin / ".claude-plugin").mkdir()
    (plugin / ".claude-plugin" / "plugin.json").write_text("{not json")
    write_config(repo_dir, 'plugins = ["bad"]\n')

    with pytest.raises(SettingsError, match=r"bad.*plugin\.json"):
        load(repo_dir)


def test_a_plugin_manifest_that_cannot_be_read_is_refused_not_a_crash(repo_dir, factory_home):
    make_plugins(factory_home, "bad")
    plugin = factory_home / "_shared" / "plugins" / "bad"
    (plugin / ".claude-plugin").mkdir()
    manifest = plugin / ".claude-plugin" / "plugin.json"
    manifest.write_text('{"name": "bad"}')
    manifest.chmod(0)
    write_config(repo_dir, 'plugins = ["bad"]\n')
    try:
        with pytest.raises(SettingsError, match=r"bad.*plugin\.json"):
            load(repo_dir)
    finally:
        manifest.chmod(0o644)


def test_a_skill_file_that_cannot_be_read_is_refused_not_a_crash(repo_dir, factory_home):
    make_plugins(factory_home, "bad")
    plugin = factory_home / "_shared" / "plugins" / "bad"
    (plugin / "skills" / "s").mkdir(parents=True)
    skill = plugin / "skills" / "s" / "SKILL.md"
    skill.write_text("---\nname: s\n---\n")
    skill.chmod(0)
    write_config(repo_dir, 'plugins = ["bad"]\n')
    try:
        with pytest.raises(SettingsError, match=r"bad.*skills/s/SKILL.md"):
            load(repo_dir)
    finally:
        skill.chmod(0o644)


def test_a_plugin_with_only_skills_and_agents_is_accepted(repo_dir, factory_home):
    make_plugins(factory_home, "ok")
    plugin = factory_home / "_shared" / "plugins" / "ok"
    (plugin / "skills" / "s").mkdir(parents=True)
    (plugin / "agents").mkdir()
    (plugin / ".claude-plugin").mkdir()
    (plugin / ".claude-plugin" / "plugin.json").write_text('{"name": "ok"}')
    write_config(repo_dir, 'plugins = ["ok"]\n')

    assert load(repo_dir).plugin_dirs == (plugin,)


def test_watch_approvers_default_to_none_and_load_as_a_casefolded_tuple(repo_dir):
    assert load(repo_dir).watch_approvers == ()
    write_config(repo_dir, 'watch_approvers = ["Alice", "bob-2"]\n')

    assert load(repo_dir).watch_approvers == ("alice", "bob-2")


@pytest.mark.parametrize("login", ["", "-a", "a-", "a--b", "a_b", "a b", "a/b", "@a", "a" * 40])
def test_a_bad_watch_approver_login_is_rejected_naming_the_key(repo_dir, login):
    write_config(repo_dir, f'watch_approvers = ["{login}"]\n')

    with pytest.raises(SettingsError, match="wrong type for setting: watch_approvers"):
        load(repo_dir)


def test_main_branch_is_unset_by_default_and_comes_from_the_config(repo_dir):
    assert load(repo_dir).main_branch is None
    write_config(repo_dir, 'main_branch = "master"\n')

    assert load(repo_dir).main_branch == "master"


def test_an_empty_main_branch_is_rejected_naming_the_key(repo_dir):
    write_config(repo_dir, 'main_branch = ""\n')

    with pytest.raises(SettingsError, match="wrong type for setting: main_branch"):
        load(repo_dir)


def test_watch_merge_is_off_by_default_and_comes_from_the_config(repo_dir):
    assert load(repo_dir).watch_merge is False
    write_config(repo_dir, "watch_merge = true\n")

    assert load(repo_dir).watch_merge is True


@pytest.mark.parametrize("where", ["project", "global"])
def test_gate_allow_outside_is_refused_naming_the_file_and_why(repo_dir, global_config, where):
    path = write_config(repo_dir, 'gate_allow_outside = ["README.md"]\n') if where == "project" else global_config
    global_config.write_text('gate_allow_outside = ["README.md"]\n' if where == "global" else "")

    with pytest.raises(SettingsError) as raised:
        load(repo_dir)

    message = str(raised.value)
    assert "gate_allow_outside" in message and str(path) in message
    assert "whole repo" in message and ".factory" in message


@pytest.mark.parametrize("subdir", [".", "./"])
def test_project_subdir_dot_names_the_repo_root(repo_dir, subdir):
    write_config(repo_dir, f'project_subdir = "{subdir}"\n')

    assert load(repo_dir).project_subdir == subdir


@pytest.mark.parametrize("subdir", [".factory", ".FACTORY", "./.Factory/sub", ".factory/"])
def test_project_subdir_in_the_factory_dir_is_rejected(repo_dir, subdir):
    write_config(repo_dir, f'project_subdir = "{subdir}"\n')

    with pytest.raises(SettingsError, match=r"project_subdir.*\.factory"):
        load(repo_dir)


def test_gate_allow_dependency_changes_is_off_by_default_and_comes_from_the_repo_config(repo_dir):
    assert load(repo_dir).gate_allow_dependency_changes is False
    write_config(repo_dir, "gate_allow_dependency_changes = true\n")

    assert load(repo_dir).gate_allow_dependency_changes is True


def test_gate_allow_dependency_changes_is_rejected_in_the_global_config(repo_dir, global_config):
    key = "gate_allow_dependency_changes"
    global_config.write_text(f"{key} = true\n")

    with pytest.raises(SettingsError, match=f"{key}.*global|global.*{key}"):
        load(repo_dir)


def test_gate_allow_dependency_changes_must_be_a_boolean(repo_dir):
    write_config(repo_dir, 'gate_allow_dependency_changes = "yes"\n')

    with pytest.raises(SettingsError, match="gate_allow_dependency_changes"):
        load(repo_dir)


def test_watch_retry_is_off_by_default_and_comes_from_the_config(repo_dir):
    assert load(repo_dir).watch_retry is False
    write_config(repo_dir, "watch_retry = true\n")

    assert load(repo_dir).watch_retry is True


def test_the_harness_defaults_to_claude_and_can_be_named_locally_or_globally(repo_dir, global_config):
    assert load(repo_dir).harness == "claude"
    global_config.write_text('harness = "claude"\n')
    write_config(repo_dir, 'harness = "claude"\n')

    assert load(repo_dir).harness == "claude"


def test_an_unknown_harness_names_the_key_and_the_known_ones(repo_dir):
    write_config(repo_dir, 'harness = "gemini"\n')

    with pytest.raises(SettingsError, match=r"harness.*gemini.*claude"):
        load(repo_dir)


def test_the_sandbox_defaults_to_nono_and_can_be_named_locally_or_globally(repo_dir, global_config):
    assert load(repo_dir).sandbox == "nono"
    global_config.write_text('sandbox = "nono"\n')
    write_config(repo_dir, 'sandbox = "nono"\n')

    assert load(repo_dir).sandbox == "nono"


def test_an_unknown_sandbox_names_the_key_and_the_known_ones(repo_dir):
    write_config(repo_dir, 'sandbox = "bubblewrap"\n')

    with pytest.raises(SettingsError, match=r"^sandbox 'bubblewrap' is unknown; known: nono$"):
        load(repo_dir)
