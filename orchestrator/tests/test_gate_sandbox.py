from pathlib import Path

from factory.gate_sandbox import build_gate_sandbox, discover_uv_tooling

BASE_ENV = {"HOME": "/h", "PATH": "/h/.local/share/mise/shims:/usr/bin:/bin", "ANTHROPIC_API_KEY": "secret",
            "VIRTUAL_ENV": "/v", "GIT_DIR": "/g", "TOKEN": "t"}


def build(tmp_path, **overrides):
    args = dict(sandbox_executable="nono", worktree=tmp_path / "wt", project_dir=tmp_path / "wt" / "target",
                cache_dir=tmp_path / "cache", python_prefix="/py", uv_python_dir="/uvpy", real_uv="/real/uv",
                base_env=BASE_ENV, test_argv=("uv", "run", "pytest", "-q"))
    return build_gate_sandbox(**{**args, **overrides})


def test_the_prefix_has_no_profile_and_grants_write_to_the_project_and_private_cache_and_read_to_python(tmp_path):
    sandbox = build(tmp_path)

    prefix = list(sandbox.prefix)
    assert prefix[:2] == ["nono", "run"] and "--profile" not in prefix and prefix[-1] == "--"
    pairs = list(zip(prefix, prefix[1:]))
    assert [b for a, b in pairs if a == "--allow"] == [str(tmp_path / "wt" / "target"), str(tmp_path / "cache")]
    assert {"/py", "/uvpy"} <= {b for a, b in pairs if a == "--read"}


def test_the_first_uv_in_the_test_command_becomes_the_real_binary(tmp_path):
    assert build(tmp_path).argv == ("/real/uv", "run", "pytest", "-q")
    assert build(tmp_path, test_argv=("pytest",)).argv == ("pytest",)


def test_the_environment_is_filtered_and_points_uv_at_the_private_cache_without_shims(tmp_path):
    env = build(tmp_path).env

    for stripped in ("ANTHROPIC_API_KEY", "VIRTUAL_ENV", "GIT_DIR"):
        assert stripped not in env
    assert env["UV_CACHE_DIR"] == str(tmp_path / "cache") and env["UV_LINK_MODE"] == "copy"
    assert env["PATH"] == "/usr/bin:/bin" and env["TOKEN"] == "t"


def test_discovery_finds_existing_python_and_uv_directories_and_a_real_uv(tmp_path):
    tooling = discover_uv_tooling(Path(__file__).parent.parent)

    assert Path(tooling.python_prefix).is_dir() and Path(tooling.real_uv).is_file()
    assert "shims" not in tooling.real_uv


def test_discovery_ignores_the_projects_own_uv_config(tmp_path):
    import stat
    log = tmp_path / "uv.log"
    fake = tmp_path / "bin" / "uv"
    fake.parent.mkdir()
    fake.write_text(f'#!/bin/sh\necho "$@" >> {log}\ncase "$*" in *find*) echo {tmp_path}/py/bin/python;; *) echo {tmp_path}/pydir;; esac\n')
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

    discover_uv_tooling(tmp_path, path=str(fake.parent))

    calls = log.read_text().splitlines()
    assert len(calls) == 2 and all("--no-config" in call.split() for call in calls)
