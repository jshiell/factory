"""Plan 91 guard: every heading, label, link, button and cell of the web UI starts with a capital.

The guard reads rendered pages with the stdlib HTML parser. Data (ids, goal and plan text, codes) is seeded as
lower-case `zdata-...` values: the guard skips them and the paired test checks they are shown verbatim."""
import pytest

from factory.clean_tasks import Candidate
from factory.gate_cache import OrphanCache, OrphanScan
from factory.sqlite_db import transaction
from factory.task import Task, TaskState
from webhelpers import add
from websentence import DATA_PREFIX, sentence_case_violations

pytest_plugins = ["webfixtures"]

GOAL = f"{DATA_PREFIX}goal"
FAILURE = f"{DATA_PREFIX}failure"
BROKEN = f"{DATA_PREFIX}broken"


def seed_every_state(ctx):
    for number, state in enumerate(TaskState):
        add(ctx, f"20260102-0304{number:02d}", state, goal=GOAL, failure_reason=FAILURE)


def tasks_list(client, ctx):
    seed_every_state(ctx)
    with transaction(ctx.settings.db_path) as conn:
        conn.execute("INSERT INTO tasks (id, state, data) VALUES (?, 'queued', 'not json')", (BROKEN,))
    return client.get("/ui/").text


TASK = "20260102-030405"
FULL_TASK = {"goal": GOAL, "failure_reason": FAILURE, "retry_of": f"{DATA_PREFIX}retry", "fix_rounds_used": 2, "attempts": 3,
             "denials": ({"tool": "a"},), "worktree": f"{DATA_PREFIX}worktree", "plan_error": f"{DATA_PREFIX}plan-error",
             "review_error": f"{DATA_PREFIX}review-error", "audit_error": f"{DATA_PREFIX}audit-error", "plan": f"{DATA_PREFIX}plan",
             "plan_risks": (f"{DATA_PREFIX}risk",), "review_findings": (f"{DATA_PREFIX}review",),
             "audit_findings": (f"{DATA_PREFIX}audit",), "waived_findings": (f"{DATA_PREFIX}waived",), "grill_rounds": 2,
             "open_questions": ({"number": 1, "title": f"{DATA_PREFIX}title", "recommended": f"{DATA_PREFIX}recommended"},)}
FULL_TASK_DATA = tuple(value for value in (*FULL_TASK.values(), f"{DATA_PREFIX}title", f"{DATA_PREFIX}recommended")
                       if isinstance(value, str))
JOB_ERROR = f"{DATA_PREFIX}job-error"
STOPPED = f"{DATA_PREFIX}stopped"


def task_page_in_state(state):
    def fetch(client, ctx):
        add(ctx, TASK, state, **FULL_TASK)
        return client.get(f"/ui/tasks/{TASK}").text
    return fetch


def task_page_with_job(job_state, result=None):
    def fetch(client, ctx):
        add(ctx, TASK, TaskState.FAILED, **FULL_TASK)
        jobs = client.app.state.jobs
        job = jobs.create("advance", TASK)
        if job_state == "running":
            jobs.mark_running(job.id)
        elif job_state == "succeeded":
            jobs.mark_succeeded(job.id, result or {})
        elif job_state == "failed":
            jobs.mark_failed(job.id, JOB_ERROR)
        elif job_state == "interrupted":
            jobs.interrupt_unfinished()
        return client.get(f"/ui/tasks/{TASK}").text
    return fetch


def jobs_page(client, ctx):
    add(ctx, TASK, TaskState.QUEUED)
    jobs = client.app.state.jobs
    jobs.create("advance", TASK)
    jobs.mark_running(jobs.create("rebase", TASK).id)
    jobs.mark_succeeded(jobs.create("merge", TASK).id, {})
    jobs.mark_failed(jobs.create("clean", None).id, JOB_ERROR)
    jobs.interrupt_unfinished()
    return client.get("/ui/jobs").text


def costs_page(client, ctx):
    add(ctx, TASK, TaskState.AUDITED)
    ctx.ledger.append(TASK, 1.25, stage="plan")
    ctx.ledger.append(TASK, 2.0, assumed=True, stage="audit")
    return client.get("/ui/costs").text


def clean_page(client, ctx):
    found = [Candidate(Task(id=TASK, prompt="", state=TaskState.FAILED, worktree="/w/t1"), directory_exists=False, dirty=True,
                       commits_ahead=2, branch_exists=True)]
    orphans = OrphanScan([OrphanCache(f"{DATA_PREFIX}cache", 2048)], [f"{DATA_PREFIX}skipped"])
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("factory.server.clean_candidates", lambda *a, **k: found)
        patch.setattr("factory.server.orphan_caches", lambda *a, **k: orphans)
        return client.get("/ui/clean").text


def nothing_to_clean_page(client, ctx):
    return client.get("/ui/clean").text


STOP_WITH_ERROR ={"stopped": STOPPED, "stopped_error": {"field": "plan_error", "code": "plan_malformed"}}

# name -> (fetch(client, ctx) -> html, data values the page must show verbatim); a page group adds its pages here
PAGES: dict = {
    "tasks list": (tasks_list, (GOAL, FAILURE, BROKEN)),
    **{f"task page {state.value}": (task_page_in_state(state), FULL_TASK_DATA) for state in TaskState},
    **{f"task page, job {job_state}": (task_page_with_job(job_state), FULL_TASK_DATA)
       for job_state in ("queued", "running", "succeeded", "interrupted")},
    "task page, job failed": (task_page_with_job("failed"), (*FULL_TASK_DATA, JOB_ERROR)),
    "task page, job stopped": (task_page_with_job("succeeded", STOP_WITH_ERROR), (*FULL_TASK_DATA, STOPPED)),
    "jobs": (jobs_page, (JOB_ERROR,)),
    "costs": (costs_page, ()),
    "clean": (clean_page, (f"{DATA_PREFIX}cache", f"{DATA_PREFIX}skipped")),
    "clean, nothing to clean": (nothing_to_clean_page, ()),
}


def test_a_lower_case_heading_label_link_and_title_are_each_reported():
    html = "<title>factory</title><h2>jobs</h2><p>goal: x</p><a href='/'>back</a><button>go</button><td>Ok</td>"

    assert sentence_case_violations(html) == ["title: factory", "h2: jobs", "p: goal: x", "a: back", "button: go"]


def test_text_starting_with_a_capital_digit_or_symbol_passes():
    assert sentence_case_violations("<h2>Jobs</h2><li>1. a question</li><td>$3.25</td><p>(none)</p>") == []


def test_the_first_character_of_nested_text_decides_and_leading_space_is_ignored():
    assert sentence_case_violations("<li> <a href='/x'>zdata-id</a>: ok</li>") == []
    assert sentence_case_violations("<li><strong>needs you</strong> Reviewed</li>") == ["strong: needs you", "li: needs you Reviewed"]


def test_data_values_code_pre_textarea_script_and_the_plan_are_skipped():
    html = (f"<td>{DATA_PREFIX}goal</td><p><code>factory ui</code> Run it</p><pre>raw</pre><textarea>raw</textarea>"
            "<script>var x</script><style>p{}</style><div class=\"plan\"><p>plan text</p><li>item</li></div>")

    assert sentence_case_violations(html) == []


def test_a_lower_case_aria_label_is_reported():
    assert sentence_case_violations('<input aria-label="answer to 1">') == ["aria-label: answer to 1"]


@pytest.mark.parametrize("name", PAGES)
def test_a_page_has_no_lower_case_heading_label_link_or_button(client, ctx, name):
    fetch, _ = PAGES[name]

    assert sentence_case_violations(fetch(client, ctx)) == []


@pytest.mark.parametrize("name", PAGES)
def test_a_page_shows_its_data_values_verbatim(client, ctx, name):
    fetch, shown = PAGES[name]
    html = fetch(client, ctx)

    for value in shown:
        assert value in html
