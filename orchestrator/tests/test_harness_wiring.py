import pytest

from factory.audit_corpus import PROJECT_SUBDIR
from factory.cli.base import _real_eval_runner, agent_runner
from factory.command import RunnerConfig
from factory.harness import HARNESSES, Capability
from factory.sandbox import SANDBOXES
from factory.settings import Settings, SettingsError
from factory.task import Task
from test_runner import EchoHarness, fake_claude


@pytest.mark.parametrize("missing", list(Capability), ids=lambda capability: capability.value)
def test_a_harness_lacking_a_capability_every_stage_needs_is_refused_at_startup(tmp_path, monkeypatch, missing):
    harness = EchoHarness()
    harness.capabilities = frozenset(Capability) - {missing}
    monkeypatch.setitem(HARNESSES, "echo", harness)
    settings = Settings(tmp_path / "repo", tmp_path / "state", tmp_path / "wts", harness="echo")

    with pytest.raises(SettingsError, match=rf"harness 'echo'.*{missing.value}"):
        agent_runner(settings, "target", records=None)


def test_the_eval_runner_starts_the_agent_the_way_the_configured_harness_does(tmp_path, monkeypatch):
    monkeypatch.setitem(HARNESSES, "echo", EchoHarness())
    worktree = tmp_path / "wt"
    (worktree / PROJECT_SUBDIR).mkdir(parents=True)
    script = fake_claude(tmp_path, 'echo "$1 $HARNESS_MARK"')
    settings = Settings(tmp_path / "repo", tmp_path / "state", tmp_path / "wts", harness="echo", claude_executable=script)

    result = _real_eval_runner(settings, tmp_path / "eval").run(
        Task(id="t1", prompt="do it", worktree=str(worktree)), RunnerConfig(model="sonnet", budget_usd=0.5)
    )

    assert result.stdout.strip() == "do it from-fake"


def test_the_agent_runner_wraps_a_confined_run_in_the_configured_sandbox(tmp_path, monkeypatch):
    policies = []
    sandbox = type("Fake", (), {"wrap": lambda self, policy: policies.append(policy) or []})()
    monkeypatch.setitem(SANDBOXES, "fake", lambda executable: sandbox)
    worktree = tmp_path / "wt"
    (worktree / PROJECT_SUBDIR).mkdir(parents=True)
    settings = Settings(
        tmp_path / "repo", tmp_path / "state", tmp_path / "wts", sandbox="fake",
        claude_executable=fake_claude(tmp_path, "echo ok"),
    )

    agent_runner(settings, PROJECT_SUBDIR, records=None).run(
        Task(id="t1", prompt="do it", worktree=str(worktree)), RunnerConfig(model="sonnet", confine=True)
    )

    assert [policy.worktree for policy in policies] == [str(worktree.resolve())]


@pytest.mark.parametrize("project_subdir", [PROJECT_SUBDIR, "."])
def test_a_confined_agent_may_write_the_whole_worktree_and_nothing_else(tmp_path, monkeypatch, project_subdir):
    policies = []
    sandbox = type("Fake", (), {"wrap": lambda self, policy: policies.append(policy) or []})()
    monkeypatch.setitem(SANDBOXES, "fake", lambda executable: sandbox)
    worktree = tmp_path / "wt"
    (worktree / PROJECT_SUBDIR).mkdir(parents=True)
    settings = Settings(
        tmp_path / "repo", tmp_path / "state", tmp_path / "wts", sandbox="fake", project_subdir=project_subdir,
        claude_executable=fake_claude(tmp_path, "echo ok"),
    )

    agent_runner(settings, project_subdir, records=None).run(
        Task(id="t1", prompt="do it", worktree=str(worktree)), RunnerConfig(model="sonnet", confine=True)
    )

    (policy,) = policies
    assert policy.write == (str(worktree.resolve()),)


def test_the_gate_of_a_root_project_may_write_the_whole_worktree_and_its_cache(tmp_path, monkeypatch):
    import factory.stages as stages
    from factory.gate_sandbox import UvTooling
    policies = []
    sandbox = type("Fake", (), {"wrap": lambda self, policy: policies.append(policy) or []})()
    monkeypatch.setitem(SANDBOXES, "fake", lambda executable: sandbox)
    monkeypatch.setattr(stages, "discover_uv_tooling", lambda project_dir: UvTooling("/real/uv", "/py", "/uvpy"))
    worktree = tmp_path / "wt"
    worktree.mkdir()
    settings = Settings(tmp_path / "repo", tmp_path / "state", tmp_path / "wts", sandbox="fake", project_subdir=".")

    stages.gate_sandbox_for(settings)(Task(id="t1", prompt="do it", worktree=str(worktree)))

    (policy,) = policies
    assert policy.write == (str(worktree.resolve()), str(settings.gate_cache_dir / "t1"))


def test_the_agent_runner_hands_an_unset_sandbox_executable_to_the_sandbox_as_none(tmp_path, monkeypatch):
    asked = []
    monkeypatch.setitem(SANDBOXES, "fake", lambda executable: asked.append(executable) or type("Fake", (), {"wrap": lambda self, policy: []})())
    settings = Settings(tmp_path / "repo", tmp_path / "state", tmp_path / "wts", sandbox="fake")

    agent_runner(settings, PROJECT_SUBDIR, records=None)

    assert asked == [None]


def test_the_context_builds_its_agent_runner_with_the_configured_harness(tmp_path, monkeypatch):
    import factory.cli as cli
    harness = EchoHarness()
    monkeypatch.setitem(HARNESSES, "echo", harness)
    deps = []
    monkeypatch.setattr(cli, "build_stages", lambda settings, given: deps.append(given) or {})
    settings = Settings(tmp_path / "repo", tmp_path / "state", tmp_path / "wts", harness="echo", home=tmp_path / "home")

    cli.build_context(settings)

    assert deps[0].runner._harness is harness
