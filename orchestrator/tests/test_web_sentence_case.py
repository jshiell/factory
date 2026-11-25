"""Plan 91 guard: every heading, label, link, button and cell of the web UI starts with a capital.

The guard reads rendered pages with the stdlib HTML parser. Data (ids, goal and plan text, codes) is seeded as
lower-case `zdata-...` values: the guard skips them and the paired test checks they are shown verbatim."""
import pytest

from factory.sqlite_db import transaction
from factory.task import TaskState
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


# name -> (fetch(client, ctx) -> html, data values the page must show verbatim); a page group adds its pages here
PAGES: dict = {"tasks list": (tasks_list, (GOAL, FAILURE, BROKEN))}


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
