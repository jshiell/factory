from factory.audit_corpus import PROJECT_SUBDIR
from factory.cli.base import _real_eval_runner
from factory.command import RunnerConfig
from factory.harness import HARNESSES
from factory.settings import Settings
from factory.task import Task
from test_runner import EchoHarness, fake_claude


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
