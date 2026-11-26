from factory.task import TaskState
from webhelpers import add

pytest_plugins = ["webfixtures"]

TASK = "20260102-030405"


def test_the_costs_page_shows_the_total_assumed_spend_and_each_stage_and_task(client, ctx):
    add(ctx, TASK, TaskState.AUDITED)
    ctx.ledger.append(TASK, 1.25, stage="plan")
    ctx.ledger.append(TASK, 0.5, stage="audit")
    ctx.ledger.append(TASK, 2.0, assumed=True, stage="plan")

    page = client.get("/ui/costs").text

    assert "Total: $3.75" in page
    assert "Assumed: $2.00" in page
    assert "Plan: $3.25" in page
    assert "Audit: $0.50" in page
    assert f'<a href="/ui/repos/proj/tasks/{TASK}">{TASK}</a>: $3.75' in page


def test_the_costs_page_of_an_empty_ledger_shows_zero(client):
    page = client.get("/ui/costs").text

    assert "Total: $0.00" in page


def test_a_stage_name_from_the_ledger_is_escaped(client, ctx):
    ctx.ledger.append(TASK, 1.0, stage="<script>alert(1)</script>")

    assert "<script>alert(1)" not in client.get("/ui/costs").text


def test_the_costs_page_is_linked_from_the_task_list(client):
    assert '<a href="/ui/costs">' in client.get("/ui/").text


def test_the_costs_page_without_a_session_is_401(client):
    client.cookies.clear()

    assert client.get("/ui/costs").status_code == 401
