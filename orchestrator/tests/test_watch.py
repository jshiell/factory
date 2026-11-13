from datetime import datetime, timezone

import pytest

from factory.watch import Issue, IssueRecord, Phase, WatchState, WatchStateError, load_state, parse_issues, run_pass, save_state

SINCE = datetime(2026, 11, 13, 0, 0, tzinfo=timezone.utc)


def raw_issue(**overrides):
    issue = {
        "number": 1,
        "title": "Normal issue",
        "body": "do the thing",
        "state": "open",
        "created_at": "2026-11-13T10:00:00Z",
        "user": {"login": "Alice", "id": 1001},
    }
    return {**issue, **overrides}


def test_an_open_issue_by_an_approver_is_considered():
    issues = parse_issues([raw_issue()], {1001}, SINCE)

    assert issues == [
        Issue(
            number=1,
            title="Normal issue",
            body="do the thing",
            author_id=1001,
            author_login="Alice",
            created_at=datetime(2026, 11, 13, 10, 0, tzinfo=timezone.utc),
        )
    ]


def test_a_pull_request_is_skipped():
    pull_request = raw_issue(pull_request={"url": "https://api.github.com/repos/o/r/pulls/1"})

    assert parse_issues([pull_request], {1001}, SINCE) == []


def test_a_closed_issue_is_skipped():
    assert parse_issues([raw_issue(state="closed")], {1001}, SINCE) == []


def test_an_author_who_is_not_an_approver_is_skipped():
    assert parse_issues([raw_issue()], {1002}, SINCE) == []


def test_an_approver_is_matched_by_id_not_login():
    impostor = raw_issue(user={"login": "Alice", "id": 2002})

    assert parse_issues([impostor], {1001}, SINCE) == []


def test_an_issue_created_before_watching_began_is_skipped():
    assert parse_issues([raw_issue(created_at="2026-11-12T23:59:59Z")], {1001}, SINCE) == []


def test_an_issue_created_exactly_when_watching_began_is_considered():
    issues = parse_issues([raw_issue(created_at="2026-11-13T00:00:00Z")], {1001}, SINCE)

    assert [i.number for i in issues] == [1]


def test_timestamps_compare_as_instants_not_text():
    later_in_another_zone = raw_issue(created_at="2026-11-13T01:00:00+02:00")  # 2026-11-12T23:00Z

    assert parse_issues([later_in_another_zone], {1001}, SINCE) == []


def test_a_missing_body_becomes_empty_text():
    issues = parse_issues([raw_issue(body=None)], {1001}, SINCE)

    assert issues[0].body == ""


def test_only_the_qualifying_issues_survive_a_mixed_list():
    mixed = [
        raw_issue(number=1),
        raw_issue(number=2, pull_request={}),
        raw_issue(number=3, state="closed"),
        raw_issue(number=4, user={"login": "mallory", "id": 2002}),
        raw_issue(number=5, created_at="2026-11-12T09:00:00Z"),
        raw_issue(number=6, body=None),
    ]

    assert [i.number for i in parse_issues(mixed, {1001}, SINCE)] == [1, 6]


def test_state_survives_a_save_and_load(tmp_path):
    state = WatchState(
        watching_since=SINCE,
        cursor=SINCE,
        issues={
            7: IssueRecord(
                task_id="t-1", plan_comment_id=42, posted_digest="d1",
                plan_body_hash="h1", findings_hash="h2", phase=Phase.AWAITING_APPROVAL,
            )
        },
    )

    save_state(tmp_path, state)

    assert load_state(tmp_path) == state
    assert [p.name for p in tmp_path.iterdir()] == ["watch.json"]


def test_first_load_records_watching_since_and_a_later_load_keeps_it(tmp_path):
    first = load_state(tmp_path, clock=lambda: SINCE)

    def clock_not_wanted():
        raise AssertionError("the clock is consulted only on the first run")

    assert first == WatchState(watching_since=SINCE)
    assert load_state(tmp_path, clock=clock_not_wanted) == first


def test_an_unknown_phase_is_refused(tmp_path):
    save_state(tmp_path, WatchState(watching_since=SINCE, issues={7: IssueRecord(task_id="t-1")}))
    path = tmp_path / "watch.json"
    path.write_text(path.read_text().replace('"new"', '"bogus"'))

    with pytest.raises(WatchStateError, match="bogus"):
        load_state(tmp_path, clock=lambda: SINCE)


class FakeGitHub:
    def __init__(self, issues):
        self.issues = issues
        self.comments_posted = []

    def open_issues(self):
        return self.issues

    def comment(self, issue_number, body):
        self.comments_posted.append((issue_number, body))
        return 1


class FakeServer:
    def __init__(self):
        self.created = []
        self.ids = iter(["t-1", "t-2", "t-3"])

    def post(self, path, body=None):
        assert path == "/tasks"
        self.created.append(body["goal"])
        return {"id": next(self.ids)}


def pass_over(tmp_path, issues, server=None, github=None):
    server = server or FakeServer()
    github = github or FakeGitHub(issues)
    save_state_if_missing(tmp_path)
    run_pass(github, server, tmp_path, {1001}, lambda: SINCE)
    return github, server


def save_state_if_missing(state_dir):
    if not (state_dir / "watch.json").exists():
        save_state(state_dir, WatchState(watching_since=SINCE))


def test_a_new_approver_issue_creates_a_task_and_records_the_mapping_with_no_comment(tmp_path):
    github, server = pass_over(tmp_path, [raw_issue(number=5, title="Add login", body="with a form")])

    assert server.created == ["Add login\n\nwith a form"]
    assert load_state(tmp_path).issues == {5: IssueRecord(task_id="t-1", phase=Phase.NEW)}
    assert github.comments_posted == []


def test_a_long_issue_becomes_a_goal_capped_at_20000_characters_ending_with_a_truncation_note(tmp_path):
    github, server = pass_over(tmp_path, [raw_issue(title="Big", body="x" * 30_000)])

    goal = server.created[0]
    assert len(goal) == 20_000
    assert goal.startswith("Big\n\nxxx")
    assert goal.endswith("[issue text truncated]")


def test_guard_a_non_approver_issue_creates_no_task_and_no_record(tmp_path):
    github, server = pass_over(tmp_path, [raw_issue(user={"login": "mallory", "id": 666})])

    assert server.created == []
    assert load_state(tmp_path).issues == {}


def test_guard_an_issue_already_in_state_creates_no_second_task(tmp_path):
    save_state(tmp_path, WatchState(watching_since=SINCE, issues={5: IssueRecord(task_id="t-9")}))

    github, server = pass_over(tmp_path, [raw_issue(number=5)])

    assert server.created == []
    assert load_state(tmp_path).issues == {5: IssueRecord(task_id="t-9")}


def test_a_truncated_state_file_is_refused_not_reset(tmp_path):
    (tmp_path / "watch.json").write_text('{"watching_since": "2026-11-13T00:0')

    with pytest.raises(WatchStateError, match="watch.json"):
        load_state(tmp_path, clock=lambda: SINCE)

    assert (tmp_path / "watch.json").read_text() == '{"watching_since": "2026-11-13T00:0'
