import json

import pytest

from factory.command import RunnerConfig, build_command, sandbox_prefix


def test_base_argv_has_prompt_json_output_and_model():
    argv, _env = build_command("do it", RunnerConfig(model="sonnet"))

    assert argv[:7] == ["claude", "-p", "do it", "--output-format", "json", "--model", "sonnet"]


def test_isolation_disables_setting_sources_and_claude_md_via_env():
    argv, env = build_command("p", RunnerConfig(model="sonnet"))

    index = argv.index("--setting-sources")
    assert argv[index + 1] == ""
    assert env == {"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1"}


def test_budget_is_passed_as_max_budget_usd():
    argv, _env = build_command("p", RunnerConfig(model="sonnet", budget_usd=0.5))

    index = argv.index("--max-budget-usd")
    assert argv[index + 1] == "0.5"


def test_permission_mode_and_space_joined_allowed_tools():
    config = RunnerConfig(
        model="sonnet",
        permission_mode="acceptEdits",
        allowed_tools=("Bash(git add *)", "Bash(git commit *)"),
    )

    argv, _env = build_command("p", config)

    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    assert argv[argv.index("--allowedTools") + 1] == "Bash(git add *) Bash(git commit *)"


def test_json_schema_is_serialised_with_json_dumps():
    schema = {"type": "object", "properties": {"summary": {"type": "string"}}}

    argv, _env = build_command("p", RunnerConfig(model="sonnet", json_schema=schema))

    assert json.loads(argv[argv.index("--json-schema") + 1]) == schema


def test_executable_can_be_overridden():
    argv, _env = build_command("p", RunnerConfig(model="sonnet"), executable="/tmp/fake-claude")

    assert argv[0] == "/tmp/fake-claude"


def test_append_system_prompt_is_passed_as_one_argument():
    config = RunnerConfig(model="sonnet", append_system_prompt="Be terse.")

    argv, _env = build_command("p", config)

    assert argv[argv.index("--append-system-prompt") + 1] == "Be terse."


def test_prompt_starting_with_dash_is_rejected_because_it_would_parse_as_a_flag():
    with pytest.raises(ValueError):
        build_command("--dangerous", RunnerConfig(model="sonnet"))


def test_max_turns_is_passed():
    argv, _env = build_command("p", RunnerConfig(model="sonnet", max_turns=12))

    assert argv[argv.index("--max-turns") + 1] == "12"


def test_disallowed_tools_are_space_joined():
    config = RunnerConfig(model="sonnet", disallowed_tools=("Bash(git *)", "WebFetch"))

    argv, _env = build_command("p", config)

    assert argv[argv.index("--disallowedTools") + 1] == "Bash(git *) WebFetch"


def test_sandbox_prefix_grants_the_resolved_worktree_read_only_and_ends_with_double_dash(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)

    prefix = sandbox_prefix("nono", link)

    assert prefix == [
        "nono", "run", "-s", "--profile", "nolabs-ai/claude", "--allow-cwd", "--read", str(real.resolve()), "--",
    ]


def test_confine_defaults_to_false_and_add_dirs_to_nothing():
    argv, _env = build_command("p", RunnerConfig(model="sonnet"))

    assert RunnerConfig(model="sonnet").confine is False
    assert "--add-dir" not in argv


def test_each_add_dir_is_passed_as_its_own_argument():
    argv, _env = build_command("p", RunnerConfig(model="sonnet"), add_dirs=("/a b", "/c"))

    pairs = [argv[i : i + 2] for i, arg in enumerate(argv) if arg == "--add-dir"]
    assert pairs == [["--add-dir", "/a b"], ["--add-dir", "/c"]]


def test_a_profile_less_sandbox_prefix_grants_write_to_the_given_dirs_and_read_to_the_others(tmp_path):
    prefix = sandbox_prefix("nono", tmp_path, profile=None, allow=("/p/target", "/p/cache"), read=("/py", "/uvpy"))

    assert "--profile" not in prefix
    assert prefix[:5] == ["nono", "run", "-s", "--allow-cwd", "--read"]
    assert prefix[-1] == "--"
    pairs = list(zip(prefix, prefix[1:]))
    assert [b for a, b in pairs if a == "--allow"] == ["/p/target", "/p/cache"]
    assert [b for a, b in pairs if a == "--read"] == [str(tmp_path.resolve()), "/py", "/uvpy"]


def test_each_plugin_dir_becomes_a_plugin_dir_flag_and_none_adds_nothing():
    assert "--plugin-dir" not in build_command("p", RunnerConfig(model="sonnet"))[0]

    argv, _env = build_command("p", RunnerConfig(model="sonnet", plugin_dirs=("/a b", "/c")))

    pairs = [argv[i : i + 2] for i, arg in enumerate(argv) if arg == "--plugin-dir"]
    assert pairs == [["--plugin-dir", "/a b"], ["--plugin-dir", "/c"]]
