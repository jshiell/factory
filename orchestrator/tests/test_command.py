import json

from factory.command import RunnerConfig, build_command


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
