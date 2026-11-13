import pytest

from factory.comments import MAX_COMMENT_CHARS, render_comment, strip_home
from factory.ids import InvalidTaskId


def test_first_line_is_the_kind_marker():
    body = render_comment("t-1", "plan", [("Plan", "do it", True)])

    assert body.splitlines()[0] == "<!-- factory:task=t-1:kind=plan -->"


def test_fence_is_longer_than_any_backtick_run_in_the_text():
    text = "before\n```\nbreak out\n````\nafter"

    lines = render_comment("t-1", "plan", [("Plan", text, True)]).splitlines()

    opening = lines.index("`````")
    assert lines[opening + 1 : opening + 1 + len(text.splitlines())] == text.splitlines()
    assert lines[opening + 1 + len(text.splitlines())] == "`````"
    assert lines.count("`````") == 2


def test_mentions_refs_images_and_fake_markers_stay_inside_the_fence():
    hostile = [
        "ping @user",
        "fixes #12",
        "![x](http://evil.example/p.png)",
        "<!-- factory:task=other:kind=plan -->",
    ]
    body = render_comment("t-1", "plan", [("Plan", "\n".join(hostile), True)])

    lines = body.splitlines()
    start = lines.index("```")
    end = lines.index("```", start + 1)
    outside = lines[:start] + lines[end + 1 :]
    assert [line for line in outside if "factory:task=" in line] == ["<!-- factory:task=t-1:kind=plan -->"]
    assert lines[start + 1 : end] == hostile
    assert not any(h in line for h in hostile for line in outside)


def test_oversized_text_is_cut_with_a_visible_note_and_the_comment_stays_under_budget():
    body = render_comment("t-1", "plan", [("Plan", "x" * 100_000, True)])

    assert len(body) <= MAX_COMMENT_CHARS
    assert body.splitlines()[0] == "<!-- factory:task=t-1:kind=plan -->"
    assert "**Plan**" in body
    assert "[truncated" in body
    assert body.index("[truncated") < body.rindex("```")


def test_the_budget_is_shared_across_sections_and_every_heading_is_kept():
    sections = [(f"Part {n}", "y" * 40_000, True) for n in range(4)]

    body = render_comment("t-1", "result", sections)

    assert len(body) <= MAX_COMMENT_CHARS
    assert all(f"**Part {n}**" in body for n in range(4))


def test_a_huge_backtick_run_cannot_blow_the_budget_through_the_fence():
    body = render_comment("t-1", "plan", [("Plan", "`" * 70_000, True)])

    assert len(body) <= MAX_COMMENT_CHARS


def test_strip_home_replaces_the_home_path_with_a_tilde():
    text = "failed in /home/jsh/Projects/x and /home/jsh/.cache; /opt/other untouched"

    assert strip_home(text, "/home/jsh") == "failed in ~/Projects/x and ~/.cache; /opt/other untouched"


@pytest.mark.parametrize("kind", ["other", "plan -->", ""])
def test_unknown_kind_is_refused(kind):
    with pytest.raises(ValueError):
        render_comment("t-1", kind, [])


@pytest.mark.parametrize("task_id", ["a--b", "x>y", "a b", "", "-->"])
def test_unsafe_task_id_is_refused(task_id):
    with pytest.raises(InvalidTaskId):
        render_comment(task_id, "plan", [])
