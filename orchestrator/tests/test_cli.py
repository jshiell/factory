import io
import json as _json
from dataclasses import replace
from datetime import datetime

import pytest
from starlette.testclient import TestClient

from factory import cli
from factory.audit_corpus import CASES as EVAL_CASES
from factory.cli import IO, Context, escape_control, main
from factory.client import ServerClient
from factory.ledger import Ledger
from factory.server import load_or_create_token
from factory.settings import Settings
from factory.store import TaskStore
from serving import repos_app


class FakeStdin(io.StringIO):
    def __init__(self, text="", tty=True):
        super().__init__(text)
        self._tty = tty

    def isatty(self):
        return self._tty


@pytest.fixture
def ctx(tmp_path):
    settings = Settings(repo=tmp_path, state_dir=tmp_path / "state", worktree_root=tmp_path / "wt")
    store = TaskStore(settings.tasks_dir)
    context = Context(settings, store, Ledger(settings.ledger_path), stages={}, clock=lambda: datetime(2026, 1, 2, 3, 4, 5))
    settings.tasks_dir.mkdir(parents=True)
    token = load_or_create_token(settings.state_dir)
    with TestClient(repos_app(context, token), base_url="http://127.0.0.1:8765") as http:
        context.server = ServerClient(http, token, sleep=lambda seconds: None).for_repo("proj")
        context.repo = "proj"
        yield context


def run(ctx, *argv, stdin="", tty=True):
    out, err = io.StringIO(), io.StringIO()
    code = main(list(argv), IO(FakeStdin(stdin, tty), out, err), ctx)
    return code, out.getvalue(), err.getvalue()


# --- usage --------------------------------------------------------------------

def test_usage_errors_exit_2(ctx):
    assert run(ctx)[0] == 2
    assert run(ctx, "bogus")[0] == 2


def test_the_watch_command_was_removed(ctx):
    code, _, err = run(ctx, "watch", "--once")

    assert code == 2 and "watch" in err


def test_control_characters_are_escaped_except_newline_and_tab():
    text = "ok\n\tfine \x1b[2K hidden\rline ‮"

    escaped = escape_control(text)

    assert escaped == "ok\n\tfine \\x1b[2K hidden\\x0dline \\u202e"


# --- stage fakes --------------------------------------------------------------


# --- reaping orphans under the lock -------------------------------------------

def reaper(killed=(), discarded=(), uncertain=None):
    from factory.process_registry import ReapResult, ReapUncertain
    calls = []

    def reap(state_dir):
        calls.append(state_dir)
        if uncertain:
            raise ReapUncertain(list(killed), [(r, "ps: operation not permitted") for r in uncertain])
        return ReapResult(list(killed), list(discarded))
    reap.calls = calls
    return reap


def test_a_context_has_a_defaulted_eval_runner_factory(ctx):
    assert callable(ctx.eval_runner)


# --- plan 23: factory eval-audit ----------------------------------------------------



FIRST_EVAL = "20260102T030405"  # the directory name for the fixtures' clock, 2026-01-02 03:04:05


def _verdict_json(verdict, cost=0.03):
    return _json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": cost,
                        "structured_output": {"verdict": verdict, "findings": ["because"]}})


class LabelRunner:
    """Judges each case as its label says, except the ids in `wrong`; remembers what it was built with."""

    def __init__(self, wrong=(), cost=0.03, spy=None):
        self.wrong, self.cost, self.spy, self.seen = set(wrong), cost, spy, []

    def run(self, task, config):
        from factory.step import RunResult
        case_id = task.id.split("-", 1)[1]
        label = next(c.label for c in EVAL_CASES if c.id == case_id)
        self.seen.append(case_id)
        if self.spy:
            self.spy()
        verdict = label if case_id not in self.wrong else ("fail" if label == "pass" else "pass")
        return RunResult(_verdict_json(verdict, self.cost), 0)


@pytest.fixture
def evals(ctx):
    ctx.reap = reaper()
    built = []

    def factory(runner):
        def build(settings, eval_dir):
            built.append((settings, eval_dir))
            return runner
        ctx.eval_runner = build
    factory.built = built
    return factory


def eval_dirs(ctx):
    root = ctx.settings.state_dir / "evals"
    return sorted(root.iterdir()) if root.exists() else []


def test_a_clean_eval_prints_its_directory_then_the_report_and_exits_zero(ctx, evals):
    evals(LabelRunner())

    code, out, err = run(ctx, "eval-audit", "--runs", "1")

    (eval_dir,) = eval_dirs(ctx)
    assert code == 0 and eval_dir.name == FIRST_EVAL
    assert out.splitlines()[0] == f"eval directory: {eval_dir}"
    assert "false-pass" in out and "false-fail" in out and "no earlier complete run" in out
    assert len(list(eval_dir.glob("case-*-r1.json"))) == 25


def test_the_eval_is_built_with_the_settings_and_its_own_directory_and_reaps_only_that_directory(ctx, evals):
    evals(LabelRunner())

    run(ctx, "eval-audit", "--runs", "1", "--only", "case-01")

    (settings, eval_dir), = evals.built
    assert settings is ctx.settings and eval_dir.parent == ctx.settings.state_dir / "evals"
    assert ctx.reap.calls == [eval_dir]


def _eval_body(ctx, evals, monkeypatch, *args):
    evals(LabelRunner())
    posted = []
    real_post = ctx.server.post
    monkeypatch.setattr(ctx.server, "post", lambda path, body=None: posted.append((path, body)) or real_post(path, body))
    run(ctx, "eval-audit", "--runs", "1", "--only", "case-01", *args)
    return next(body for path, body in posted if path == "/eval-audit")


def test_the_stage_config_flag_asks_the_eval_to_run_the_audit_stage_config(ctx, evals, monkeypatch):
    assert _eval_body(ctx, evals, monkeypatch, "--stage-config")["stage_config"] is True


def test_the_eval_request_does_not_mention_the_stage_config_without_the_flag(ctx, evals, monkeypatch):
    assert "stage_config" not in _eval_body(ctx, evals, monkeypatch)


def test_the_real_state_dir_stays_free_and_untouched_while_an_eval_runs(ctx, evals):
    from factory.lock import state_lock
    free = []

    def spy():
        with state_lock(ctx.settings.state_dir):
            free.append(True)
    evals(LabelRunner(spy=spy))

    run(ctx, "eval-audit", "--runs", "1", "--only", "case-01")

    assert free == [True]
    assert not ctx.settings.tasks_dir.exists() or list(ctx.settings.tasks_dir.iterdir()) == []
    assert not ctx.settings.ledger_path.exists()


@pytest.mark.parametrize("args, message", [
    (("--only", "case-99"), "unknown case"),
    (("--runs", "0"), "--runs"),
    (("--max-usd", "0"), "--max-usd"),
    (("--resume", "/nowhere/at/all"), "--resume"),
    (("--compare", "/nowhere/at/all"), "--compare"),
])
def test_bad_options_are_usage_errors_and_nothing_runs(ctx, evals, args, message):
    runner = LabelRunner()
    evals(runner)

    code, out, err = run(ctx, "eval-audit", *args)

    assert code == 2 and message in err and runner.seen == [] and eval_dirs(ctx) == []


def test_resuming_continues_the_earlier_directory_through_the_server(ctx, evals):
    evals(LabelRunner())
    run(ctx, "eval-audit", "--runs", "1", "--only", "case-01")
    (eval_dir,) = eval_dirs(ctx)
    runner = LabelRunner()
    evals(runner)

    code, out, _ = run(ctx, "eval-audit", "--runs", "1", "--only", "case-01", "--resume", str(eval_dir))

    assert code == 0 and str(eval_dir) in out and runner.seen == [] and eval_dirs(ctx) == [eval_dir]


def test_a_bad_case_judged_pass_exits_one(ctx, evals):
    evals(LabelRunner(wrong={"case-09"}))

    code, out, _ = run(ctx, "eval-audit", "--runs", "1")

    assert code == 1 and "case-09" in out


def test_a_run_stopped_by_the_budget_cap_exits_three(ctx, evals):
    evals(LabelRunner(cost=0.5))

    code, out, _ = run(ctx, "eval-audit", "--runs", "1", "--max-usd", "0.7")

    assert code == 3


def test_the_next_eval_is_compared_with_the_last_complete_full_one(ctx, evals):
    evals(LabelRunner())
    run(ctx, "eval-audit", "--runs", "1")
    ctx.clock = lambda: datetime(2026, 1, 3, 3, 4, 5)
    evals(LabelRunner(wrong={"case-01"}))

    code, out, _ = run(ctx, "eval-audit", "--runs", "1")

    assert f"compared with {FIRST_EVAL}" in out and "case-01: pass -> fail" in out and code == 1


def test_compare_accepts_an_exported_bundle_and_sees_no_config_difference(ctx, evals, tmp_path):
    from factory.audit_corpus import CASES
    from factory.audit_share import build_bundle, write_bundle
    evals(LabelRunner())
    run(ctx, "eval-audit", "--runs", "1")
    (first,) = eval_dirs(ctx)
    (tmp_path / "share").mkdir()
    bundle_path, _ = write_bundle(build_bundle(first, CASES), tmp_path / "share")
    evals(LabelRunner(wrong={"case-01"}))

    code, out, _ = run(ctx, "eval-audit", "--runs", "1", "--compare", str(bundle_path))

    assert f"compared with {first.name}" in out and "case-01: pass -> fail" in out
    assert "differs" not in out and code == 1


def test_an_earlier_eval_that_cannot_be_compared_is_reported_without_losing_the_result(ctx, evals):
    import json
    evals(LabelRunner())
    run(ctx, "eval-audit", "--runs", "1")
    (first,) = eval_dirs(ctx)
    meta = json.loads((first / "meta.json").read_text())
    meta["audit_config"]["plugin_dirs"] = ["/a/same", "/b/same"]
    (first / "meta.json").write_text(json.dumps(meta))
    ctx.clock = lambda: datetime(2026, 1, 3, 3, 4, 5)
    evals(LabelRunner(wrong={"case-01"}))

    code, out, _ = run(ctx, "eval-audit", "--runs", "1")

    assert code == 1 and "cannot compare" in out and "same" in out


@pytest.mark.parametrize("content", ["not json", '{"schema": 2}', '{"schema": 1}', "[]"])
def test_a_compare_file_that_is_not_a_bundle_is_a_usage_error_and_nothing_runs(ctx, evals, tmp_path, content):
    runner = LabelRunner()
    evals(runner)
    bad = tmp_path / "run.json"
    bad.write_text(content)

    code, _, err = run(ctx, "eval-audit", "--compare", str(bad))

    assert code == 2 and "--compare" in err and runner.seen == [] and eval_dirs(ctx) == []



def test_check_corpus_runs_the_gate_on_every_case_runs_no_agents_and_exits_zero_when_all_pass(ctx, evals, monkeypatch):
    from factory.gate import GateResult
    from factory.gate_sandbox import UvTooling
    runner = LabelRunner()
    evals(runner)
    calls = []
    monkeypatch.setattr("factory.stages.discover_uv_tooling", lambda project_dir: UvTooling("/real/uv", "/py", "/uvpy"))
    monkeypatch.setattr(cli, "check_gate", lambda worktree, base_sha, subdir, argv, timeout, **kw: (calls.append((subdir, argv, timeout)), GateResult(True))[1])

    code, out, _ = run(ctx, "eval-audit", "--check-corpus")

    assert code == 0 and len(calls) == 25 and runner.seen == [] and eval_dirs(ctx) == []
    assert calls[0] == ("target", ["/real/uv", *ctx.settings.test_argv[1:]], ctx.settings.gate_timeout_s)
    assert "case-01 pass minimal" in out and "25 cases, none blocked" in out


def test_check_corpus_runs_each_case_through_the_configured_gate_sandbox(ctx, evals, monkeypatch):
    from factory.audit_corpus import PROJECT_SUBDIR
    from factory.gate import GateResult
    from factory.gate_sandbox import UvTooling
    from factory.sandbox import SANDBOXES
    evals(LabelRunner())
    policies = []
    monkeypatch.setitem(SANDBOXES, "fake", lambda executable: type("Fake", (), {"wrap": lambda self, policy: policies.append(policy) or ["fake", "--"]})())
    ctx.settings = replace(ctx.settings, sandbox="fake", project_subdir="elsewhere")
    monkeypatch.setattr("factory.stages.discover_uv_tooling", lambda project_dir: UvTooling("/real/uv", "/py", "/uvpy"))
    calls = []
    monkeypatch.setattr(cli, "check_gate", lambda worktree, base_sha, subdir, argv, timeout, **kw: (calls.append((argv, kw)), GateResult(True))[1])

    code, _, _ = run(ctx, "eval-audit", "--check-corpus")

    assert code == 0 and len(calls) == 25
    argv, kw = calls[0]
    assert kw["test_prefix"] == ("fake", "--") and argv[0] == "/real/uv" and kw["env"]["UV_LINK_MODE"] == "copy"
    assert policies[0].cwd.name == PROJECT_SUBDIR


def test_check_corpus_names_a_blocked_case_with_its_reason_and_escaped_output_and_exits_one(ctx, evals, monkeypatch):
    from factory.gate import GateResult
    from factory.gate_sandbox import UvTooling
    evals(LabelRunner())
    monkeypatch.setattr("factory.stages.discover_uv_tooling", lambda project_dir: UvTooling("/real/uv", "/py", "/uvpy"))

    def gate(worktree, base_sha, subdir, argv, timeout, **kw):
        if worktree.name.endswith("case-02"):
            return GateResult(False, "tests_failed", 1, "\x1b[31m1 failed\x1b[0m")
        return GateResult(True)
    monkeypatch.setattr(cli, "check_gate", gate)

    code, out, _ = run(ctx, "eval-audit", "--check-corpus")

    assert code == 1 and "case-02" in out and "BLOCKED tests_failed" in out and "1 failed" in out and "\x1b" not in out
    assert "blocked: case-02" in out
