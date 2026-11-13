from datetime import datetime, timezone

from factory.watch import Issue, parse_issues

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
