import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from factory.client import ServerRefused
from factory.github import GitHubError, Reaction

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
    def __init__(self, issues, reactions=None):
        self.issues = issues
        self.comments_posted = []
        self.reactions_by_comment = reactions or {}
        self.reactions_fetched = []

    def open_issues(self):
        return self.issues

    def reactions(self, comment_id):
        self.reactions_fetched.append(comment_id)
        return self.reactions_by_comment.get(comment_id, [])

    def comment(self, issue_number, body):
        self.comments_posted.append((issue_number, body))
        return 1


class FakeServer:
    def __init__(self, advance_result=None, approval=None, job_state="succeeded"):
        self.job_state = job_state
        self.created = []
        self.ids = iter(["t-1", "t-2", "t-3"])
        self.advance_result = advance_result or {"steps": [], "state": "planned", "failure_reason": None, "stopped": None}
        self.approval = approval
        self.advanced = []
        self.approved = []

    def post(self, path, body=None):
        if path.endswith("/advance"):
            self.advanced.append(path)
            return {"job": {"id": "j-1"}}
        if path.endswith("/approve"):
            self.approved.append((path, body))
            return {"id": path.split("/")[2], "state": "queued"}
        assert path == "/tasks"
        self.created.append(body["goal"])
        return {"id": next(self.ids)}

    def wait(self, job_id):
        if self.job_state != "succeeded":
            return {"state": self.job_state, "error": "boom", "result": None}
        return {"state": "succeeded", "error": None, "result": self.advance_result}

    def get(self, path):
        assert path.endswith("/approval")
        return self.approval


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


REVIEWED = {"steps": [], "state": "reviewed", "failure_reason": None, "stopped": None}
APPROVAL = {
    "goal": "g", "plan": "1. do it\n@mallory", "risks": [], "review_findings": ["[required] add a test", "nit: naming"],
    "required": ["add a test"], "plan_digest": "digest-1", "warning": "1 required note was dropped",
}


def test_guard_an_awaiting_approval_issue_is_not_advanced_or_commented_again(tmp_path):
    record = IssueRecord(task_id="t-9", phase=Phase.AWAITING_APPROVAL, plan_comment_id=7, posted_digest="d")
    save_state(tmp_path, WatchState(watching_since=SINCE, issues={5: record}))
    server = FakeServer(advance_result=REVIEWED, approval=APPROVAL)

    github, _ = pass_over(tmp_path, [raw_issue(number=5)], server=server)

    assert server.advanced == []
    assert github.comments_posted == []
    assert load_state(tmp_path).issues == {5: record}


class FlakyGitHub(FakeGitHub):
    def __init__(self, issues):
        super().__init__(issues)
        self.failures_left = 1

    def comment(self, issue_number, body):
        if self.failures_left:
            self.failures_left -= 1
            raise GitHubError("boom")
        return super().comment(issue_number, body)


def test_a_failed_plan_post_records_nothing_and_the_next_pass_posts_exactly_one_comment(tmp_path):
    server = FakeServer(advance_result=REVIEWED, approval=APPROVAL)
    github = FlakyGitHub([raw_issue(number=5)])

    with pytest.raises(GitHubError):
        pass_over(tmp_path, [], server=server, github=github)

    assert load_state(tmp_path).issues == {5: IssueRecord(task_id="t-1", phase=Phase.NEW)}

    pass_over(tmp_path, [], server=server, github=github)

    assert len(github.comments_posted) == 1
    assert load_state(tmp_path).issues[5].phase is Phase.AWAITING_APPROVAL


def test_a_task_that_advances_to_reviewed_gets_one_plan_comment_and_the_record_is_saved(tmp_path):
    server = FakeServer(advance_result=REVIEWED, approval=APPROVAL)

    github, _ = pass_over(tmp_path, [raw_issue(number=5)], server=server)

    assert server.advanced == ["/tasks/t-1/advance"]
    [(issue_number, body)] = github.comments_posted
    assert issue_number == 5
    assert body.startswith("<!-- factory:task=t-1:kind=plan -->")
    assert "👍" in body and "👎" in body
    assert "```\n1. do it\n@mallory\n```" in body
    findings = "- [required] add a test\n- nit: naming"
    assert f"```\n{findings}\n```" in body
    assert "```\nadd a test\n\n1 required note was dropped\n```" in body
    assert load_state(tmp_path).issues == {
        5: IssueRecord(
            task_id="t-1", phase=Phase.AWAITING_APPROVAL, plan_comment_id=1, posted_digest="digest-1",
            plan_body_hash=hashlib.sha256(body.encode()).hexdigest(),
            findings_hash=hashlib.sha256(findings.encode()).hexdigest(),
        )
    }


FAILED = {"steps": [], "state": "failed", "failure_reason": "plan rejected: too vague", "stopped": None}


def test_a_task_that_fails_gets_one_result_comment_with_the_reason_fenced_and_goes_failed(tmp_path):
    server = FakeServer(advance_result=FAILED)

    github, _ = pass_over(tmp_path, [raw_issue(number=5)], server=server)

    [(issue_number, body)] = github.comments_posted
    assert issue_number == 5
    assert body.startswith("<!-- factory:task=t-1:kind=result -->")
    assert "```\nplan rejected: too vague\n```" in body
    assert load_state(tmp_path).issues == {5: IssueRecord(task_id="t-1", phase=Phase.FAILED, result_comment_id=1)}


def test_a_stopped_advance_gets_one_result_comment_saying_why_and_goes_stopped(tmp_path):
    stopped = {"steps": [], "state": "planned", "failure_reason": None, "stopped": "step cap (10) reached\n@mallory"}
    server = FakeServer(advance_result=stopped)

    github, _ = pass_over(tmp_path, [raw_issue(number=5)], server=server)

    [(issue_number, body)] = github.comments_posted
    assert issue_number == 5
    assert body.startswith("<!-- factory:task=t-1:kind=result -->")
    assert "```\nstep cap (10) reached\n@mallory\n```" in body
    assert load_state(tmp_path).issues == {5: IssueRecord(task_id="t-1", phase=Phase.STOPPED, result_comment_id=1)}


@pytest.mark.parametrize("job_state", ["failed", "interrupted"])
def test_a_job_that_did_not_succeed_changes_nothing_so_the_next_pass_retries(tmp_path, job_state):
    server = FakeServer(job_state=job_state)

    github, _ = pass_over(tmp_path, [raw_issue(number=5)], server=server)

    assert github.comments_posted == []
    assert load_state(tmp_path).issues == {5: IssueRecord(task_id="t-1", phase=Phase.NEW)}


@pytest.mark.parametrize("phase", [Phase.FAILED, Phase.STOPPED])
def test_guard_a_failed_or_stopped_issue_is_not_advanced_or_commented_again(tmp_path, phase):
    record = IssueRecord(task_id="t-9", phase=phase, result_comment_id=7)
    save_state(tmp_path, WatchState(watching_since=SINCE, issues={5: record}))
    server = FakeServer(advance_result=FAILED)

    github, _ = pass_over(tmp_path, [raw_issue(number=5)], server=server)

    assert server.advanced == []
    assert github.comments_posted == []
    assert load_state(tmp_path).issues == {5: record}


def test_guard_a_failed_result_post_records_nothing_and_the_next_pass_retries(tmp_path):
    server = FakeServer(advance_result=FAILED)
    github = FlakyGitHub([raw_issue(number=5)])

    with pytest.raises(GitHubError):
        pass_over(tmp_path, [], server=server, github=github)

    assert load_state(tmp_path).issues == {5: IssueRecord(task_id="t-1", phase=Phase.NEW)}

    pass_over(tmp_path, [], server=server, github=github)

    assert len(github.comments_posted) == 1
    assert load_state(tmp_path).issues[5].phase is Phase.FAILED


def test_guard_a_state_file_from_before_result_comment_ids_still_loads(tmp_path):
    save_state(tmp_path, WatchState(watching_since=SINCE, issues={7: IssueRecord(task_id="t-1")}))
    path = tmp_path / "watch.json"
    path.write_text(path.read_text().replace(', "result_comment_id": null', ""))
    assert "result_comment_id" not in path.read_text()

    assert load_state(tmp_path).issues == {7: IssueRecord(task_id="t-1")}


def test_the_home_directory_is_stripped_from_the_failure_reason(tmp_path):
    reason = "worktree /home/jsh/.factory/t-1 is dirty"
    server = FakeServer(advance_result={**FAILED, "failure_reason": reason})
    save_state_if_missing(tmp_path)

    github = FakeGitHub([raw_issue(number=5)])
    run_pass(github, server, tmp_path, {1001}, lambda: SINCE, home=Path("/home/jsh"))

    [(_, body)] = github.comments_posted
    assert "```\nworktree ~/.factory/t-1 is dirty\n```" in body
    assert "/home/jsh" not in body


AWAITING = IssueRecord(
    task_id="t-9", phase=Phase.AWAITING_APPROVAL, plan_comment_id=7, posted_digest="digest-1",
    plan_body_hash="h1", findings_hash="h2",
)


def awaiting_pass(tmp_path, reactions):
    save_state(tmp_path, WatchState(watching_since=SINCE, issues={5: AWAITING}))
    server = FakeServer(advance_result=REVIEWED, approval=APPROVAL)
    github = FakeGitHub([raw_issue(number=5)], reactions={7: reactions})
    pass_over(tmp_path, [], server=server, github=github)
    return github, server


def test_an_approver_thumbs_up_approves_with_the_posted_digest_keeping_required_notes_and_goes_approved(tmp_path):
    github, server = awaiting_pass(tmp_path, [Reaction("+1", 1001, "alice")])

    assert server.approved == [("/tasks/t-9/approve", {"plan_digest": "digest-1", "waive": []})]
    assert server.advanced == []
    assert github.comments_posted == []
    assert load_state(tmp_path).issues == {5: IssueRecord(**{**vars(AWAITING), "phase": Phase.APPROVED})}


def test_guard_a_thumbs_up_from_a_non_approver_with_an_approvers_login_does_nothing(tmp_path):
    github, server = awaiting_pass(tmp_path, [Reaction("+1", 2002, "alice"), Reaction("+1", 666, "mallory")])

    assert server.approved == []
    assert load_state(tmp_path).issues == {5: AWAITING}


def test_guard_no_reactions_on_the_plan_comment_does_nothing(tmp_path):
    github, server = awaiting_pass(tmp_path, [])

    assert github.reactions_fetched == [7]
    assert server.approved == []
    assert server.advanced == []
    assert github.comments_posted == []
    assert load_state(tmp_path).issues == {5: AWAITING}


@pytest.mark.parametrize("phase", [p for p in Phase if p is not Phase.AWAITING_APPROVAL])
def test_guard_reactions_are_read_only_for_an_issue_awaiting_approval(tmp_path, phase):
    record = IssueRecord(**{**vars(AWAITING), "phase": phase})
    save_state(tmp_path, WatchState(watching_since=SINCE, issues={5: record}))
    github = FakeGitHub([raw_issue(number=5)], reactions={7: [Reaction("+1", 1001, "alice")]})

    pass_over(tmp_path, [], server=FakeServer(), github=github)

    assert github.reactions_fetched == []


class RefusingApproveServer(FakeServer):
    def post(self, path, body=None):
        if path.endswith("/approve"):
            raise ServerRefused(409, "wrong state", "task is queued")
        return super().post(path, body)


def test_guard_a_refused_approve_does_not_write_approved(tmp_path):
    save_state(tmp_path, WatchState(watching_since=SINCE, issues={5: AWAITING}))
    github = FakeGitHub([raw_issue(number=5)], reactions={7: [Reaction("+1", 1001, "alice")]})

    with pytest.raises(ServerRefused):
        pass_over(tmp_path, [], server=RefusingApproveServer(), github=github)

    assert load_state(tmp_path).issues == {5: AWAITING}
