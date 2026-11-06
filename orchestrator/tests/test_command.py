from factory.command import RunnerConfig, build_command


def test_base_argv_has_prompt_json_output_and_model():
    argv, _env = build_command("do it", RunnerConfig(model="sonnet"))

    assert argv == ["claude", "-p", "do it", "--output-format", "json", "--model", "sonnet"]
