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


def read_grants(sandbox):
    prefix = list(sandbox.prefix)
    return [b for a, b in zip(prefix, prefix[1:]) if a == "--read"]


def test_an_absolute_uv_adds_a_read_on_its_directory(tmp_path):
    tmp_path = tmp_path.resolve()
    uv = tmp_path / "uvbin" / "uv"
    uv.parent.mkdir()
    uv.write_text("")

    assert str(uv.parent) in read_grants(build(tmp_path, real_uv=str(uv)))


def test_a_symlinked_uv_adds_a_read_on_the_resolved_directory_only(tmp_path):
    tmp_path = tmp_path.resolve()
    target = tmp_path / "release" / "uv"
    target.parent.mkdir()
    target.write_text("")
    link = tmp_path / "bins" / "uv"
    link.parent.mkdir()
    link.symlink_to(target)

    grants = read_grants(build(tmp_path, real_uv=str(link)))

    assert str(target.parent) in grants and str(link.parent) not in grants


def test_a_plain_uv_adds_exactly_one_read_for_its_own_directory(tmp_path):
    tmp_path = tmp_path.resolve()
    uv = tmp_path / "uvbin" / "uv"
    uv.parent.mkdir()
    uv.write_text("")

    assert read_grants(build(tmp_path, real_uv=str(uv))) == [str(tmp_path / "wt"), "/py", "/uvpy", str(uv.parent)]


def test_a_uv_directory_that_is_root_or_holds_home_adds_no_read(tmp_path):
    tmp_path = tmp_path.resolve()
    home = tmp_path / "home"
    uv = home / "uv"
    uv.parent.mkdir()
    uv.write_text("")
    env = {**BASE_ENV, "HOME": str(home)}

    assert read_grants(build(tmp_path, real_uv=str(uv), base_env=env))[-1] == "/uvpy"
    assert read_grants(build(tmp_path, real_uv="/uv"))[-1] == "/uvpy"


def test_a_bare_or_relative_uv_adds_no_read(tmp_path):
    for real_uv in ("uv", "bin/uv"):
        assert read_grants(build(tmp_path, real_uv=real_uv)) == [str(tmp_path / "wt"), "/py", "/uvpy"]
