import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from factory.client import ServerRefused, ServerUnreachable
from factory.github import Comment, GitHubError, Reaction, User

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
    def __init__(self, issues, reactions=None, comments=None):
        self.issues = issues
        self.comments_posted = []
        self.reactions_by_comment = reactions or {}
        self.reactions_fetched = []
        self.comments_by_issue = comments or {}
        self.comments_fetched = []

    def self_user(self):
        return User("factory-bot", 4242)

    def open_issues(self):
        return self.issues

    def comments(self, issue_number):
        self.comments_fetched.append(issue_number)
        return self.comments_by_issue.get(issue_number, [])

    def reactions(self, comment_id):
        self.reactions_fetched.append(comment_id)
        return self.reactions_by_comment.get(comment_id, [])

    def comment(self, issue_number, body):
        self.comments_posted.append((issue_number, body))
        return 1


class FakeServer:
    def __init__(self, advance_result=None, approval=None, job_state="succeeded", task_state="planned", failure_reason=None):
        self.job_state = job_state
        self.task_state = task_state
        self.failure_reason = failure_reason
        self.created = []
        self.ids = iter(["t-1", "t-2", "t-3"])
        self.advance_result = advance_result or {"steps": [], "state": "planned", "failure_reason": None, "stopped": None}
        self.approval = approval
        self.advanced = []
        self.approved = []
        self.rejected = []
        self.fetched = []

    def post(self, path, body=None):
        if path.endswith("/reject"):
            self.rejected.append(path)
            return {"id": path.split("/")[2], "state": "rejected"}
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
        if not path.endswith("/approval"):
            return {"state": self.task_state, "failure_reason": self.failure_reason}
        self.fetched.append(path)
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


def sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()


PLAN_BODY = "<!-- factory:task=t-9:kind=plan -->\n\nthe plan as posted"
APPROVAL_FINDINGS = "- [required] add a test\n- nit: naming"
PLAN_COMMENT = Comment(7, PLAN_BODY, 4242, "factory-bot")
AWAITING = IssueRecord(
    task_id="t-9", phase=Phase.AWAITING_APPROVAL, plan_comment_id=7, posted_digest="digest-1",
    plan_body_hash=sha256(PLAN_BODY), findings_hash=sha256(APPROVAL_FINDINGS),
)


def awaiting_pass(tmp_path, reactions, comments=(PLAN_COMMENT,), approval=APPROVAL, server=None, github=None):
    save_state_if_missing(tmp_path)
    if load_state(tmp_path).issues == {}:
        save_state(tmp_path, WatchState(watching_since=SINCE, issues={5: AWAITING}))
    server = server or FakeServer(advance_result=REVIEWED, approval=approval)
    github = github or FakeGitHub([raw_issue(number=5)], reactions={7: reactions}, comments={5: list(comments)})
    pass_over(tmp_path, [], server=server, github=github)
    return github, server


def test_an_approver_thumbs_up_approves_with_the_posted_digest_keeping_required_notes_and_goes_approved(tmp_path):
    github, server = awaiting_pass(tmp_path, [Reaction("+1", 1001, "alice")])

    assert server.approved == [("/tasks/t-9/approve", {
        "plan_digest": "digest-1", "review_findings": ["[required] add a test", "nit: naming"], "waive": []})]
    assert server.advanced == []
    assert github.comments_posted == []
    assert load_state(tmp_path).issues == {5: IssueRecord(**{**vars(AWAITING), "phase": Phase.APPROVED})}


def test_the_approve_post_carries_the_findings_lines_from_the_approval_it_just_checked(tmp_path):
    _, server = awaiting_pass(tmp_path, [Reaction("+1", 1001, "alice")])

    [(_, body)] = server.approved
    assert body["review_findings"] == APPROVAL["review_findings"]


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


APPROVED = IssueRecord(task_id="t-9", phase=Phase.APPROVED, plan_comment_id=7, posted_digest="digest-1")
AUDITED = {
    "steps": [{"stage": "implement", "state": "agent_done"}, {"stage": "audit", "state": "audited"}],
    "state": "audited", "failure_reason": None, "stopped": None,
}


def approved_pass(tmp_path, advance_result, github=None):
    save_state(tmp_path, WatchState(watching_since=SINCE, issues={5: APPROVED}))
    server = FakeServer(advance_result=advance_result)
    github = github or FakeGitHub([raw_issue(number=5)])
    pass_over(tmp_path, [], server=server, github=github)
    return github, server


def test_an_approved_task_that_audits_gets_one_result_comment_with_steps_state_and_merge_hint(tmp_path):
    github, server = approved_pass(tmp_path, AUDITED)

    assert server.advanced == ["/tasks/t-9/advance"]
    [(issue_number, body)] = github.comments_posted
    assert issue_number == 5
    assert body.startswith("<!-- factory:task=t-9:kind=result -->")
    assert "```\nimplement -> agent_done\naudit -> audited\n```" in body
    assert "```\naudited\n```" in body
    assert "factory merge t-9" in body
    assert load_state(tmp_path).issues == {5: IssueRecord(**{**vars(APPROVED), "phase": Phase.AUDITED, "result_comment_id": 1})}


def test_guard_an_approved_task_that_fails_or_stops_reuses_the_failure_and_stopped_comments(tmp_path):
    github, _ = approved_pass(tmp_path, FAILED)

    [(_, body)] = github.comments_posted
    assert "```\nplan rejected: too vague\n```" in body
    assert load_state(tmp_path).issues[5].phase is Phase.FAILED

    stopped = {**AUDITED, "stopped": "step cap (10) reached"}
    github, _ = approved_pass(tmp_path, stopped)

    [(_, body)] = github.comments_posted
    assert "```\nstep cap (10) reached\n```" in body
    assert load_state(tmp_path).issues[5].phase is Phase.STOPPED


def test_guard_an_approved_task_still_running_changes_nothing_and_is_retried(tmp_path):
    github, _ = approved_pass(tmp_path, {**AUDITED, "state": "agent_running"})

    assert github.comments_posted == []
    assert load_state(tmp_path).issues == {5: APPROVED}


def test_guard_an_audited_issue_is_not_advanced_or_commented_again(tmp_path):
    record = IssueRecord(**{**vars(APPROVED), "phase": Phase.AUDITED, "result_comment_id": 3})
    save_state(tmp_path, WatchState(watching_since=SINCE, issues={5: record}))
    server = FakeServer(advance_result=AUDITED)

    github, _ = pass_over(tmp_path, [raw_issue(number=5)], server=server)

    assert server.advanced == []
    assert github.comments_posted == []
    assert load_state(tmp_path).issues == {5: record}


def test_guard_a_failed_audited_post_keeps_approved_and_the_next_pass_posts_exactly_one(tmp_path):
    github = FlakyGitHub([raw_issue(number=5)])

    approved_pass(tmp_path, AUDITED, github=github)

    assert load_state(tmp_path).issues == {5: APPROVED}

    pass_over(tmp_path, [], server=FakeServer(advance_result=AUDITED), github=github)

    assert len(github.comments_posted) == 1
    assert load_state(tmp_path).issues[5].phase is Phase.AUDITED


class RefusingApproveServer(FakeServer):
    def post(self, path, body=None):
        if path.endswith("/approve"):
            raise ServerRefused(409, "wrong state", "task is queued")
        return super().post(path, body)


def test_guard_a_refused_approve_does_not_write_approved(tmp_path):
    save_state(tmp_path, WatchState(watching_since=SINCE, issues={5: AWAITING}))
    github = FakeGitHub([raw_issue(number=5)], reactions={7: [Reaction("+1", 1001, "alice")]}, comments={5: [PLAN_COMMENT]})

    pass_over(tmp_path, [], server=RefusingApproveServer(approval=APPROVAL), github=github)

    assert load_state(tmp_path).issues == {5: AWAITING}


THUMBS_UP = [Reaction("+1", 1001, "alice")]
CHANGED = IssueRecord(**{**vars(AWAITING), "phase": Phase.CHANGED, "result_comment_id": 1})


def assert_changed_once_without_approval(tmp_path, github, server):
    assert server.approved == []
    [(issue_number, body)] = github.comments_posted
    assert issue_number == 5
    assert body.startswith("<!-- factory:task=t-9:kind=changed -->")
    assert "no approval was made" in body
    assert load_state(tmp_path).issues == {5: CHANGED}


def test_an_edited_plan_comment_is_not_approved_and_goes_changed_with_one_comment(tmp_path):
    edited = Comment(7, PLAN_BODY + "\n\n6. also delete the tests", 4242, "factory-bot")

    github, server = awaiting_pass(tmp_path, THUMBS_UP, comments=[edited])

    assert_changed_once_without_approval(tmp_path, github, server)


def test_a_deleted_plan_comment_is_not_approved_and_goes_changed_with_one_comment(tmp_path):
    same_text_other_comment = Comment(8, PLAN_BODY, 4242, "factory-bot")

    github, server = awaiting_pass(tmp_path, THUMBS_UP, comments=[same_text_other_comment])

    assert_changed_once_without_approval(tmp_path, github, server)


def test_changed_review_findings_on_the_server_are_not_approved_and_go_changed(tmp_path):
    findings_changed = {**APPROVAL, "review_findings": ["nit: naming"], "required": []}

    github, server = awaiting_pass(tmp_path, THUMBS_UP, approval=findings_changed)

    assert server.fetched == ["/tasks/t-9/approval"]
    assert_changed_once_without_approval(tmp_path, github, server)


def test_a_changed_plan_digest_on_the_server_is_not_approved_and_goes_changed(tmp_path):
    github, server = awaiting_pass(tmp_path, THUMBS_UP, approval={**APPROVAL, "plan_digest": "digest-2"})

    assert_changed_once_without_approval(tmp_path, github, server)


class PlanChangedServer(FakeServer):
    def post(self, path, body=None):
        if path.endswith("/approve"):
            raise ServerRefused(409, "plan changed", "the plan differs from the one shown")
        return super().post(path, body)


def test_an_approve_the_server_refuses_as_plan_changed_goes_changed_with_one_comment(tmp_path):
    github, server = awaiting_pass(tmp_path, THUMBS_UP, server=PlanChangedServer(approval=APPROVAL))

    assert_changed_once_without_approval(tmp_path, github, server)


def test_guard_a_changed_issue_is_left_alone_on_the_next_pass(tmp_path):
    awaiting_pass(tmp_path, THUMBS_UP, approval={**APPROVAL, "plan_digest": "digest-2"})

    github, server = awaiting_pass(tmp_path, THUMBS_UP, approval={**APPROVAL, "plan_digest": "digest-2"})

    assert github.comments_posted == []
    assert github.reactions_fetched == [] and github.comments_fetched == []
    assert server.fetched == [] and server.approved == [] and server.advanced == []
    assert load_state(tmp_path).issues == {5: CHANGED}


def test_guard_a_failed_changed_post_records_nothing_and_approves_nothing(tmp_path):
    github = FlakyGitHub([raw_issue(number=5)])
    github.reactions_by_comment = {7: THUMBS_UP}
    github.comments_by_issue = {5: []}

    awaiting_pass(tmp_path, THUMBS_UP, github=github)

    assert load_state(tmp_path).issues == {5: AWAITING}

    _, server = awaiting_pass(tmp_path, THUMBS_UP, github=github)

    assert server.approved == []
    assert len(github.comments_posted) == 1
    assert load_state(tmp_path).issues == {5: CHANGED}


def test_guard_a_non_approver_thumbs_up_fetches_neither_the_comment_nor_the_approval(tmp_path):
    github, server = awaiting_pass(tmp_path, [Reaction("+1", 666, "mallory")], comments=[])

    assert github.comments_fetched == []
    assert server.fetched == []
    assert github.comments_posted == []
    assert load_state(tmp_path).issues == {5: AWAITING}


THUMBS_DOWN = [Reaction("-1", 1001, "alice")]
REJECTED = IssueRecord(**{**vars(AWAITING), "phase": Phase.REJECTED, "result_comment_id": 1})


def assert_rejected_once_without_approval(tmp_path, github, server):
    assert server.rejected == ["/tasks/t-9/reject"]
    assert server.approved == []
    [(issue_number, body)] = github.comments_posted
    assert issue_number == 5
    assert body.startswith("<!-- factory:task=t-9:kind=rejected -->")
    assert "Plan rejected" in body
    assert load_state(tmp_path).issues == {5: REJECTED}


def test_an_approver_thumbs_down_rejects_the_task_with_one_comment_and_goes_rejected(tmp_path):
    github, server = awaiting_pass(tmp_path, THUMBS_DOWN)

    assert_rejected_once_without_approval(tmp_path, github, server)


def test_an_approver_thumbs_down_wins_over_an_approver_thumbs_up(tmp_path):
    github, server = awaiting_pass(tmp_path, [*THUMBS_UP, *THUMBS_DOWN])

    assert_rejected_once_without_approval(tmp_path, github, server)


def test_guard_an_approver_thumbs_down_needs_no_integrity_check(tmp_path):
    edited = Comment(7, PLAN_BODY + "\n\nedited", 4242, "factory-bot")

    github, server = awaiting_pass(tmp_path, THUMBS_DOWN, comments=[edited], approval={**APPROVAL, "plan_digest": "digest-2"})

    assert github.comments_fetched == [5] and server.fetched == []  # the one fetch is the search for an earlier rejected comment
    assert_rejected_once_without_approval(tmp_path, github, server)


def test_guard_a_thumbs_down_from_a_non_approver_with_an_approvers_login_does_nothing(tmp_path):
    github, server = awaiting_pass(tmp_path, [Reaction("-1", 2002, "alice"), Reaction("-1", 666, "mallory")])

    assert server.rejected == []
    assert github.comments_posted == []
    assert load_state(tmp_path).issues == {5: AWAITING}


def test_guard_a_rejected_issue_is_left_alone_on_the_next_pass(tmp_path):
    awaiting_pass(tmp_path, THUMBS_DOWN)

    github, server = awaiting_pass(tmp_path, THUMBS_DOWN)

    assert github.comments_posted == []
    assert github.reactions_fetched == [] and github.comments_fetched == []
    assert server.rejected == [] and server.approved == [] and server.advanced == []
    assert load_state(tmp_path).issues == {5: REJECTED}


def test_guard_a_failed_rejected_post_records_nothing(tmp_path):
    github = FlakyGitHub([raw_issue(number=5)])
    github.reactions_by_comment = {7: THUMBS_DOWN}

    awaiting_pass(tmp_path, THUMBS_DOWN, github=github)

    assert load_state(tmp_path).issues == {5: AWAITING}
    assert github.comments_posted == []


def test_guard_an_unchanged_plan_comment_findings_and_digest_approve(tmp_path):
    github, server = awaiting_pass(tmp_path, THUMBS_UP)

    assert github.comments_fetched == [5]
    assert server.fetched == ["/tasks/t-9/approval"]
    assert server.approved == [("/tasks/t-9/approve", {
        "plan_digest": "digest-1", "review_findings": ["[required] add a test", "nit: naming"], "waive": []})]
    assert load_state(tmp_path).issues[5].phase is Phase.APPROVED


def test_an_awaiting_approval_task_the_human_approved_at_the_terminal_is_advanced_without_approving_again(tmp_path):
    save_state(tmp_path, WatchState(watching_since=SINCE, issues={5: AWAITING}))
    server = FakeServer(advance_result=AUDITED, task_state="queued")

    github, _ = pass_over(tmp_path, [], server=server, github=FakeGitHub([raw_issue(number=5)]))

    assert server.approved == []
    assert github.reactions_fetched == []
    assert server.advanced == ["/tasks/t-9/advance"]
    [(_, body)] = github.comments_posted
    assert "factory merge t-9" in body
    assert load_state(tmp_path).issues[5].phase is Phase.AUDITED


def test_a_task_the_server_says_failed_gets_one_result_comment_with_the_reason_fenced_and_goes_failed(tmp_path):
    save_state(tmp_path, WatchState(watching_since=SINCE, issues={5: AWAITING}))
    server = FakeServer(task_state="failed", failure_reason="plan rejected at /home/jsh/x: too vague")
    github = FakeGitHub([raw_issue(number=5)])

    run_pass(github, server, tmp_path, {1001}, lambda: SINCE, home=Path("/home/jsh"))

    [(issue_number, body)] = github.comments_posted
    assert issue_number == 5
    assert body.startswith("<!-- factory:task=t-9:kind=result -->")
    assert "```\nplan rejected at ~/x: too vague\n```" in body
    assert server.advanced == []
    assert server.approved == []
    assert load_state(tmp_path).issues[5].phase is Phase.FAILED


def test_guard_a_thumbs_up_on_a_task_the_server_already_queued_does_not_approve_twice(tmp_path):
    github, server = awaiting_pass(tmp_path, THUMBS_UP, server=FakeServer(advance_result=AUDITED, task_state="queued"))

    assert server.approved == []
    assert server.advanced == ["/tasks/t-9/advance"]


def test_guard_a_reviewed_task_awaiting_approval_still_follows_the_thumbs_up(tmp_path):
    github, server = awaiting_pass(tmp_path, THUMBS_UP, server=FakeServer(advance_result=REVIEWED, approval=APPROVAL, task_state="reviewed"))

    assert len(server.approved) == 1
    assert load_state(tmp_path).issues[5].phase is Phase.APPROVED


@pytest.mark.parametrize("task_state", ["audited", "merged"])
def test_a_task_the_server_says_is_audited_or_merged_goes_audited_without_a_comment_or_advance(tmp_path, task_state):
    save_state(tmp_path, WatchState(watching_since=SINCE, issues={5: AWAITING}))
    server = FakeServer(task_state=task_state)
    github = FakeGitHub([raw_issue(number=5)])

    pass_over(tmp_path, [], server=server, github=github)

    assert github.comments_posted == []
    assert github.reactions_fetched == []
    assert server.advanced == []
    assert server.approved == []
    assert load_state(tmp_path).issues[5].phase is Phase.AUDITED


def test_guard_a_failed_post_for_a_server_failed_task_records_nothing(tmp_path):
    save_state(tmp_path, WatchState(watching_since=SINCE, issues={5: AWAITING}))
    server = FakeServer(task_state="failed", failure_reason="too vague")

    pass_over(tmp_path, [], server=server, github=FlakyGitHub([raw_issue(number=5)]))

    assert load_state(tmp_path).issues == {5: AWAITING}


def rendered_plan_body(tmp_path):
    """The plan comment the watcher renders for t-1 from APPROVAL, captured through a real pass in a scratch state dir."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    github, _ = pass_over(scratch, [raw_issue(number=5)], server=FakeServer(advance_result=REVIEWED, approval=APPROVAL))
    [(_, body)] = github.comments_posted
    return body


def test_a_restarted_watcher_adopts_its_own_unedited_plan_comment_instead_of_posting_again(tmp_path):
    body = rendered_plan_body(tmp_path)
    github = FakeGitHub([raw_issue(number=5)], comments={5: [Comment(88, body, 4242, "factory-bot")]})

    pass_over(tmp_path, [], server=FakeServer(advance_result=REVIEWED, approval=APPROVAL), github=github)

    assert github.comments_posted == []
    assert load_state(tmp_path).issues == {
        5: IssueRecord(
            task_id="t-1", phase=Phase.AWAITING_APPROVAL, plan_comment_id=88, posted_digest="digest-1",
            plan_body_hash=sha256(body), findings_hash=sha256(APPROVAL_FINDINGS),
        )
    }


def test_a_plan_comment_planted_by_another_account_with_the_watchers_login_is_ignored_and_a_fresh_one_posted(tmp_path):
    body = rendered_plan_body(tmp_path)
    github = FakeGitHub([raw_issue(number=5)], comments={5: [Comment(88, body, 666, "factory-bot")]})

    pass_over(tmp_path, [], server=FakeServer(advance_result=REVIEWED, approval=APPROVAL), github=github)

    assert github.comments_posted == [(5, body)]
    assert load_state(tmp_path).issues[5].plan_comment_id == 1


def test_guard_an_own_plan_comment_edited_since_it_was_posted_is_not_adopted_and_a_fresh_one_posted(tmp_path):
    body = rendered_plan_body(tmp_path)
    edited = Comment(88, body + "\n\n6. also delete the tests", 4242, "factory-bot")
    github = FakeGitHub([raw_issue(number=5)], comments={5: [edited]})

    pass_over(tmp_path, [], server=FakeServer(advance_result=REVIEWED, approval=APPROVAL), github=github)

    assert github.comments_posted == [(5, body)]
    assert load_state(tmp_path).issues[5].plan_comment_id == 1
    assert load_state(tmp_path).issues[5].plan_body_hash == sha256(body)


def ending_pass(tmp_path, ending, existing):
    """Run the pass that ends the task the given way, with `existing` comments already on issue 5; returns the fake GitHub."""
    advance_results = {"failed": FAILED, "stopped": {**REVIEWED, "stopped": "step cap (10) reached"}}
    if ending in advance_results:
        github = FakeGitHub([raw_issue(number=5)], comments={5: list(existing)})
        pass_over(tmp_path, [], server=FakeServer(advance_result=advance_results[ending]), github=github)
        return github
    if ending == "audited":
        github = FakeGitHub([raw_issue(number=5)], comments={5: list(existing)})
        approved_pass(tmp_path, AUDITED, github=github)
        return github
    reactions, approval = {"changed": (THUMBS_UP, {**APPROVAL, "plan_digest": "digest-2"}), "rejected": (THUMBS_DOWN, APPROVAL)}[ending]
    github, _ = awaiting_pass(tmp_path, reactions, comments=[PLAN_COMMENT, *existing], approval=approval)
    return github


# ending -> (task id, comment kind, final phase)
ENDINGS = {
    "failed": ("t-1", "result", Phase.FAILED),
    "stopped": ("t-1", "result", Phase.STOPPED),
    "audited": ("t-9", "result", Phase.AUDITED),
    "changed": ("t-9", "changed", Phase.CHANGED),
    "rejected": ("t-9", "rejected", Phase.REJECTED),
}


@pytest.mark.parametrize("ending", ENDINGS)
def test_a_restarted_watcher_adopts_its_own_ending_comment_instead_of_posting_again(tmp_path, ending):
    task_id, kind, phase = ENDINGS[ending]
    own = Comment(88, f"<!-- factory:task={task_id}:kind={kind} -->\n\nposted before the crash", 4242, "factory-bot")

    github = ending_pass(tmp_path, ending, [own])

    assert github.comments_posted == []
    record = load_state(tmp_path).issues[5]
    assert (record.result_comment_id, record.phase) == (88, phase)


RESULT_MARKER = "<!-- factory:task=t-1:kind=result -->"


@pytest.mark.parametrize("comment", [
    Comment(88, f"{RESULT_MARKER}\n\nplanted", 666, "factory-bot"),
    Comment(88, "<!-- factory:task=t-2:kind=result -->\n\nanother task", 4242, "factory-bot"),
    Comment(88, "<!-- factory:task=t-1:kind=plan -->\n\nanother kind", 4242, "factory-bot"),
    Comment(88, f"```\n{RESULT_MARKER}\n```", 4242, "factory-bot"),
    Comment(88, f"a note first\n{RESULT_MARKER}", 4242, "factory-bot"),
    Comment(88, f"{RESULT_MARKER} trailing text", 4242, "factory-bot"),
], ids=["another account", "another task", "another kind", "fenced", "not the first line", "not the whole first line"])
def test_guard_a_comment_that_is_not_the_watchers_own_marker_for_this_ending_is_not_adopted(tmp_path, comment):
    github = ending_pass(tmp_path, "failed", [comment])

    assert len(github.comments_posted) == 1
    assert load_state(tmp_path).issues[5].result_comment_id == 1


@pytest.mark.parametrize("phase", [Phase.FAILED, Phase.STOPPED, Phase.AUDITED, Phase.CHANGED, Phase.REJECTED])
def test_guard_an_issue_whose_ending_comment_is_recorded_does_not_search_comments(tmp_path, phase):
    save_state(tmp_path, WatchState(watching_since=SINCE, issues={5: IssueRecord(task_id="t-9", phase=phase, result_comment_id=7)}))

    github, _ = pass_over(tmp_path, [raw_issue(number=5)], server=FakeServer(advance_result=FAILED))

    assert github.comments_fetched == []


class FailingCommentsGitHub(FakeGitHub):
    """Reading the comments of the failing issues raises the given error."""

    def __init__(self, issues, error, failing=(5,)):
        super().__init__(issues)
        self.error, self.failing = error, failing

    def comments(self, issue_number):
        if issue_number in self.failing:
            raise self.error
        return super().comments(issue_number)


def test_a_github_failure_on_one_issue_logs_it_escaped_with_the_issue_number_and_the_pass_continues(tmp_path, capsys):
    server = FakeServer(advance_result=REVIEWED, approval=APPROVAL)
    github = FailingCommentsGitHub([raw_issue(number=5), raw_issue(number=6)], GitHubError("\x1b[31mrate limited\x07\n"))

    pass_over(tmp_path, [], server=server, github=github)

    logged = capsys.readouterr().err
    assert "5" in logged and "rate limited" in logged
    assert "\x1b" not in logged and "\x07" not in logged
    assert logged.count("\n") == 1
    assert load_state(tmp_path).issues[5] == IssueRecord(task_id="t-1", phase=Phase.NEW)
    assert load_state(tmp_path).issues[6].phase is Phase.AWAITING_APPROVAL


def test_a_server_failed_task_with_no_reason_gets_a_fixed_fallback_text_and_goes_failed(tmp_path):
    save_state(tmp_path, WatchState(watching_since=SINCE, issues={5: AWAITING}))
    github = FakeGitHub([raw_issue(number=5)])

    pass_over(tmp_path, [], server=FakeServer(task_state="failed", failure_reason=None), github=github)

    [(_, body)] = github.comments_posted
    assert "```\nNo reason recorded.\n```" in body
    assert load_state(tmp_path).issues[5].phase is Phase.FAILED


def test_guard_a_failed_advance_with_no_reason_gets_the_same_fallback_text(tmp_path):
    server = FakeServer(advance_result={**FAILED, "failure_reason": None})

    github, _ = pass_over(tmp_path, [raw_issue(number=5)], server=server)

    [(_, body)] = github.comments_posted
    assert "```\nNo reason recorded.\n```" in body


class FailingListGitHub(FakeGitHub):
    def __init__(self, error):
        super().__init__([])
        self.error = error

    def open_issues(self):
        raise self.error


def test_a_github_failure_listing_issues_logs_it_escaped_and_ends_the_pass_without_raising(tmp_path, capsys):
    github = FailingListGitHub(GitHubError("\x1b[31mHTTP 502\n"))

    pass_over(tmp_path, [], github=github)

    logged = capsys.readouterr().err
    assert "HTTP 502" in logged and "\x1b" not in logged
    assert logged.count("\n") == 1
    assert load_state(tmp_path).issues == {}


class FailingSelfGitHub(FakeGitHub):
    def self_user(self):
        raise GitHubError("\x1b[31mbad credentials")


def test_guard_a_github_failure_looking_up_its_own_account_ends_the_pass_the_same_way(tmp_path, capsys):
    github = FailingSelfGitHub([raw_issue(number=5)])

    server = FakeServer()
    pass_over(tmp_path, [], server=server, github=github)

    logged = capsys.readouterr().err
    assert "bad credentials" in logged and "\x1b" not in logged
    assert server.created == []


class RefusingRejectServer(FakeServer):
    def post(self, path, body=None):
        if path.endswith("/reject"):
            raise ServerRefused(409, "wrong state", "task is rejected\x1b[2J")
        return super().post(path, body)


def test_guard_a_refused_reject_logs_escaped_leaves_the_record_awaiting_and_the_pass_continues(tmp_path, capsys):
    save_state(tmp_path, WatchState(watching_since=SINCE, issues={5: AWAITING}))
    github = FakeGitHub([raw_issue(number=5), raw_issue(number=6)], reactions={7: [Reaction("-1", 1001, "alice")]}, comments={5: [PLAN_COMMENT]})
    server = RefusingRejectServer(advance_result=REVIEWED, approval=APPROVAL)

    pass_over(tmp_path, [], server=server, github=github)

    logged = capsys.readouterr().err
    assert "5" in logged and "wrong state" in logged and "\x1b" not in logged
    assert github.comments_posted[0][0] == 6
    assert load_state(tmp_path).issues[5] == AWAITING


class FailingServer(FakeServer):
    """Fetching the task of the failing task ids raises the given error."""

    def __init__(self, error, failing=("t-1",), **kwargs):
        super().__init__(**kwargs)
        self.error, self.failing = error, failing

    def get(self, path):
        if path.split("/")[2] in self.failing:
            raise self.error
        return super().get(path)


def test_a_server_outage_on_one_issue_logs_it_and_the_pass_continues_with_the_record_unchanged(tmp_path, capsys):
    server = FailingServer(ServerUnreachable("\x1b[2Jconnection refused"), advance_result=REVIEWED, approval=APPROVAL)
    github = FakeGitHub([raw_issue(number=5), raw_issue(number=6)])

    pass_over(tmp_path, [], server=server, github=github)

    logged = capsys.readouterr().err
    assert "5" in logged and "connection refused" in logged and "\x1b" not in logged
    assert load_state(tmp_path).issues[5] == IssueRecord(task_id="t-1", phase=Phase.NEW)
    assert load_state(tmp_path).issues[6].phase is Phase.AWAITING_APPROVAL
