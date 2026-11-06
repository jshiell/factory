from factory.command import RunnerConfig, build_command


def test_base_argv_has_prompt_json_output_and_model():
    argv, _env = build_command("do it", RunnerConfig(model="sonnet"))

    assert argv[:7] == ["claude", "-p", "do it", "--output-format", "json", "--model", "sonnet"]


def test_isolation_disables_setting_sources_and_claude_md_via_env():
    argv, env = build_command("p", RunnerConfig(model="sonnet"))

    index = argv.index("--setting-sources")
    assert argv[index + 1] == ""
    assert env == {"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1"}
