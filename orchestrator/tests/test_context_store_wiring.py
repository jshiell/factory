from factory.cli import build_context
from factory.digest import plan_digest
from factory.settings import Settings
from factory.task import Task, TaskState


def test_a_listener_on_the_context_store_hears_a_stage_save(tmp_path):
    # guard (green on arrival): the stages and the context share one task store
    ctx = build_context(Settings(tmp_path / "repo", tmp_path / "state", tmp_path / "wts"))
    # a plan that no longer matches its digest fails the review stage before any agent runs
    ctx.store.save(Task(id="t1", prompt="p", state=TaskState.PLANNED, goal="g", plan="changed", plan_digest=plan_digest("original")))
    calls = []
    ctx.store.subscribe(lambda: calls.append("saved"))

    ctx.stages["review"]("t1")

    assert calls == ["saved"]
