from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from gitutil import git

from factory.audit_task import AUDIT_CONFIG
from factory.digest import plan_digest
from factory.gate_sandbox import UvTooling
from factory.ledger import Ledger
from factory.plan_task import PLAN_CONFIG
from factory.process_registry import Record
from factory.review_plan import REVIEW_CONFIG
from factory.settings import Settings
from factory.sqlite_process_records import SqliteProcessRecords
from factory.stages import Deps, build_stages, implementer_config, stage_config
from factory.step import RunResult
from factory.store import TaskStore
from factory.task import Task, TaskState


class RecordingRunner:
    def __init__(self):
        self.configs = []

    def run(self, task, config):
        self.configs.append(config)
        return RunResult("not json", 1)


class RepoWorktrees:
    def __init__(self, repo):
        self.repo = repo

    def create(self, task_id):
        return self.repo, git(self.repo, "rev-parse", "HEAD")


@pytest.fixture
def setup(tmp_path, repo):
    (tmp_path / "tasks").mkdir()
    store = TaskStore(tmp_path / "tasks")
    runner = RecordingRunner()
    settings = Settings(repo=repo, state_dir=tmp_path, worktree_root=tmp_path / "wt", project_subdir="proj",
                        test_argv=("pytest", "-x"), gate_timeout_s=7.0, cap_usd=3.0, budget_usd=0.4)
    deps = Deps(store=store, ledger=Ledger(tmp_path / "ledger.jsonl"), runner=runner, worktrees=RepoWorktrees(repo))
    (repo / "proj").mkdir()
    (repo / "proj" / "code.py").write_text("x")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "p")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "proj" / "code.py").write_text("y")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "c")
    return store, runner, settings, deps, repo, base


def save(store, repo, base, state, **fields):
    store.save(Task(id="t1", prompt="do it", state=state, worktree=str(repo), base_sha=base, **fields))


def test_the_implementer_config_is_the_only_one_with_write_tools_and_no_git(setup):
    settings = setup[2]
    config = implementer_config(settings)

    assert "Bash(git *)" in config.disallowed_tools
    assert (config.budget_usd, config.max_turns, config.model) == (0.4, 30, "sonnet")
    for readonly in (PLAN_CONFIG, REVIEW_CONFIG, AUDIT_CONFIG):
        assert not {"Write", "Edit"} & set(readonly.allowed_tools)


def test_all_four_agent_configs_are_confined(setup):
    assert all(config.confine for config in (implementer_config(setup[2]), PLAN_CONFIG, REVIEW_CONFIG, AUDIT_CONFIG))


def test_the_implementer_cannot_run_commands_whatever_the_settings_allow(setup):
    settings = replace(setup[2], allowed_tools=("Bash(uv run pytest*)", "Write"))

    config = implementer_config(settings)

    assert {"Bash", "Bash(git *)", "Glob", "NotebookEdit"} <= set(config.disallowed_tools)


def test_the_default_implementer_allow_list_is_empty_so_default_rules_confine_reads(setup):
    assert implementer_config(setup[2]).allowed_tools == ()


@pytest.mark.parametrize(
    "stage,state,expected",
    [
        ("plan", TaskState.PROPOSED, PLAN_CONFIG),
        ("review", TaskState.PLANNED, REVIEW_CONFIG),
        ("audit", TaskState.VERIFIED, AUDIT_CONFIG),
    ],
)
def test_read_only_stages_receive_their_own_read_only_config(setup, stage, state, expected):
    store, runner, settings, deps, repo, base = setup
    save(store, repo, base, state, goal="g", plan="p", plan_digest=plan_digest("p"))

    build_stages(settings, deps)[stage]("t1")

    assert runner.configs == [expected]


def test_implement_receives_the_implementer_config(setup):
    store, runner, settings, deps, repo, base = setup
    store.save(Task(id="t1", prompt="do it"))

    build_stages(settings, deps)["implement"]("t1")

    assert runner.configs == [implementer_config(settings)]


def test_verify_receives_the_test_command_subdir_and_timeout(setup, monkeypatch):
    store, runner, settings, deps, repo, base = setup
    calls = []
    monkeypatch.setattr("factory.stages.verify_task", lambda *a, **k: calls.append((a, k)))

    build_stages(settings, deps)["verify"]("t1")

    (args, kwargs), = calls
    sandbox = kwargs.pop("sandbox")
    assert callable(sandbox)
    for fix_kwarg in ("runner", "ledger", "fix_config", "cap_usd", "fix_rounds", "factory_paths", "records"):
        kwargs.pop(fix_kwarg)
    assert (args, kwargs) == (("t1", deps.store), {"project_subdir": "proj", "test_argv": ["pytest", "-x"], "gate_timeout_s": 7.0})


def test_the_verify_stage_gets_the_fix_loop_wiring(setup, monkeypatch):
    store, runner, settings, deps, repo, base = setup
    settings = replace(settings, fix_rounds=3)
    seen = {}
    monkeypatch.setattr("factory.stages.verify_task", lambda *a, **k: seen.update(k))

    build_stages(settings, deps)["verify"]("t1")

    assert seen["runner"] is runner and seen["ledger"] is deps.ledger
    assert seen["fix_config"] == implementer_config(settings)
    assert (seen["cap_usd"], seen["fix_rounds"]) == (3.0, 3)


def test_the_verify_stage_builds_the_gate_sandbox_from_the_settings_and_the_task(setup, monkeypatch):
    store, runner, settings, deps, repo, base = setup
    settings = replace(settings, sandbox_executable="fake-nono")
    saved = Task(id="t1", prompt="p", worktree=str(repo), base_sha=base)
    seen = {}
    monkeypatch.setattr("factory.stages.verify_task", lambda *a, **k: seen.update(k))
    monkeypatch.setattr("factory.stages.discover_uv_tooling", lambda project_dir: UvTooling("/real/uv", "/py", "/uvpy"))

    build_stages(settings, deps)["verify"]("t1")
    sandbox = seen["sandbox"](saved)

    assert sandbox.prefix[0] == "fake-nono" and sandbox.argv[0] == "pytest"
    assert str(settings.state_dir / "gate-cache" / "t1") in sandbox.prefix
    assert (settings.state_dir / "gate-cache" / "t1").is_dir()


def test_stage_names_are_exactly_the_pipeline(setup):
    assert set(build_stages(setup[2], setup[3])) == {"plan", "review", "implement", "verify", "audit"}


def test_the_verify_stage_passes_the_state_and_worktree_dirs_to_the_gate(setup, monkeypatch):
    store, runner, settings, deps, repo, base = setup
    seen = {}
    monkeypatch.setattr("factory.stages.verify_task", lambda *a, **k: seen.update(k))

    build_stages(settings, deps)["verify"]("t1")

    assert seen["factory_paths"] == (settings.state_dir, settings.worktree_root)


def test_the_verify_stage_gets_the_records_so_gate_runs_are_recorded(setup, monkeypatch):
    store, runner, settings, deps, repo, base = setup
    seen = {}
    monkeypatch.setattr("factory.stages.verify_task", lambda *a, **k: seen.update(k))

    build_stages(settings, deps)["verify"]("t1")

    record = Record(4242, 4242, "t1", "gate", 100.0, 100.5)
    SqliteProcessRecords(settings.db_path).write(record)
    assert seen["records"].load() == [record]


def test_the_implementer_is_told_to_stay_inside_the_project(setup):
    _, _, settings, _, _, _ = setup

    config = implementer_config(settings)

    assert "Only change files inside the project directory" in config.append_system_prompt


def test_the_implementer_is_told_not_to_touch_uv_config(setup):
    _, _, settings, _, _, _ = setup

    config = implementer_config(settings)

    for name in ("uv.toml", ".python-version", "[tool.uv]"):
        assert name in config.append_system_prompt


def extended(settings, **fields):
    return stage_config(PLAN_CONFIG, replace(settings, **fields), "plan")


def test_a_config_is_unchanged_when_no_extra_prompt_is_set(setup):
    assert extended(setup[2]) == PLAN_CONFIG


def test_the_global_prompt_follows_the_built_in_one(setup):
    config = extended(setup[2], append_system_prompt="Global.")

    assert config.append_system_prompt == PLAN_CONFIG.append_system_prompt + "\n\nGlobal."


def test_the_stage_prompt_applies_only_to_its_own_stage(setup):
    settings = replace(setup[2], stage_prompts={"audit": "Strict."})

    assert stage_config(PLAN_CONFIG, settings, "plan") == PLAN_CONFIG
    assert stage_config(AUDIT_CONFIG, settings, "audit").append_system_prompt.endswith("\n\nStrict.")


def test_global_then_stage_prompts_follow_the_built_in_one(setup):
    config = extended(setup[2], append_system_prompt="Global.", stage_prompts={"plan": "Stage."})

    assert config.append_system_prompt == PLAN_CONFIG.append_system_prompt + "\n\nGlobal.\n\nStage."


@pytest.mark.parametrize(
    "stage,state,base_config",
    [
        ("plan", TaskState.PROPOSED, PLAN_CONFIG),
        ("review", TaskState.PLANNED, REVIEW_CONFIG),
        ("audit", TaskState.VERIFIED, AUDIT_CONFIG),
    ],
)
def test_read_only_stages_append_the_global_and_their_own_prompt(setup, stage, state, base_config):
    store, runner, settings, deps, repo, base = setup
    settings = replace(settings, append_system_prompt="G.", stage_prompts={stage: "S."})
    save(store, repo, base, state, goal="g", plan="p", plan_digest=plan_digest("p"))

    build_stages(settings, deps)[stage]("t1")

    assert runner.configs == [stage_config(base_config, settings, stage)]
    assert runner.configs[0].append_system_prompt.endswith("\n\nG.\n\nS.")


def test_implement_appends_the_global_and_implement_prompts(setup):
    store, runner, settings, deps, repo, base = setup
    settings = replace(settings, append_system_prompt="G.", stage_prompts={"implement": "S.", "plan": "P."})
    store.save(Task(id="t1", prompt="do it"))

    build_stages(settings, deps)["implement"]("t1")

    assert runner.configs[0].append_system_prompt.endswith("\n\nG.\n\nS.")


def test_the_fixer_gets_the_implement_prompt(setup, monkeypatch):
    store, runner, settings, deps, repo, base = setup
    settings = replace(settings, stage_prompts={"implement": "S."})
    seen = {}
    monkeypatch.setattr("factory.stages.verify_task", lambda *a, **k: seen.update(k))

    build_stages(settings, deps)["verify"]("t1")

    assert seen["fix_config"].append_system_prompt.endswith("\n\nS.")


def test_the_plugin_dirs_are_added_to_a_stage_config_as_strings(setup):
    settings = replace(setup[2], plugin_dirs=(Path("/h/_shared/plugins/a"),))

    config = stage_config(PLAN_CONFIG, settings, "plan")

    assert config.plugin_dirs == ("/h/_shared/plugins/a",)
    assert replace(config, plugin_dirs=()) == PLAN_CONFIG


def test_every_agent_stage_and_the_fixer_get_the_plugin_dirs(setup, monkeypatch):
    store, runner, settings, deps, repo, base = setup
    settings = replace(settings, plugin_dirs=(Path("/p/a"),))
    save(store, repo, base, TaskState.PROPOSED, goal="g", plan="p", plan_digest=plan_digest("p"))
    stages = build_stages(settings, deps)
    seen = {}
    monkeypatch.setattr("factory.stages.verify_task", lambda *a, **k: seen.update(k))

    stages["plan"]("t1")
    store.save(Task(id="t1", prompt="do it"))
    stages["implement"]("t1")
    stages["verify"]("t1")

    assert [config.plugin_dirs for config in runner.configs] == [("/p/a",), ("/p/a",)]
    assert seen["fix_config"].plugin_dirs == ("/p/a",)


def test_a_stage_gets_the_global_plugin_dirs_then_its_own_without_duplicates(setup):
    a, b, c = Path("/p/a"), Path("/p/b"), Path("/p/c")
    settings = replace(setup[2], plugin_dirs=(a, b), stage_plugin_dirs={"plan": (b, c), "audit": (c,)})

    assert stage_config(PLAN_CONFIG, settings, "plan").plugin_dirs == ("/p/a", "/p/b", "/p/c")
    assert stage_config(AUDIT_CONFIG, settings, "audit").plugin_dirs == ("/p/a", "/p/b", "/p/c")
    assert stage_config(REVIEW_CONFIG, settings, "review").plugin_dirs == ("/p/a", "/p/b")


def test_stage_plugins_apply_only_to_their_own_stage(setup):
    settings = replace(setup[2], stage_plugin_dirs={"audit": (Path("/p/c"),)})

    assert stage_config(PLAN_CONFIG, settings, "plan") == PLAN_CONFIG
    assert stage_config(AUDIT_CONFIG, settings, "audit").plugin_dirs == ("/p/c",)


def test_the_implementer_and_the_fixer_get_the_implement_stage_plugins(setup, monkeypatch):
    store, runner, settings, deps, repo, base = setup
    settings = replace(settings, stage_plugin_dirs={"implement": (Path("/p/i"),), "plan": (Path("/p/x"),)})
    seen = {}
    monkeypatch.setattr("factory.stages.verify_task", lambda *a, **k: seen.update(k))

    build_stages(settings, deps)["verify"]("t1")

    assert implementer_config(settings).plugin_dirs == ("/p/i",)
    assert seen["fix_config"].plugin_dirs == ("/p/i",)
