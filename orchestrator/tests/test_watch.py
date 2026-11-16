import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from factory.answer_questions import MAX_ANSWER_BYTES
from factory.client import ServerRefused, ServerUnreachable
from factory.comments import render_comment
from factory.github import Comment, GitHubError, Reaction, User
from factory.merge_task import ALREADY_IN_MAIN, MAIN_MOVED

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
                plan_body_hash="h1", findings_hash="h2", phase=Phase.AWAITING_MERGE,
                merge_comment_id=43, merge_body_hash="h3", merge_sha="abc123",
            )
        },
    )

    save_state(tmp_path, state)

    assert load_state(tmp_path) == state
    assert [p.name for p in tmp_path.iterdir()] == ["watch.json"]


def test_a_rebase_offer_survives_a_save_and_load(tmp_path):
    state = WatchState(
        watching_since=SINCE,
        issues={7: IssueRecord(task_id="t-1", phase=Phase.AWAITING_REBASE, rebase_comment_id=44, rebase_sha="abc123")},
    )

    save_state(tmp_path, state)

    assert load_state(tmp_path) == state


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
        self.next_comment_id = 1

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
        comment_id = self.next_comment_id
        self.next_comment_id += 1
        return comment_id


class FakeServer:
    def __init__(self, advance_result=None, approval=None, job_state="succeeded", task_state="planned", failure_reason=None,
                 questions=None, grill_rounds=0, merge_preview=None, merge_job=None, task_state_after_merge=None,
                 audited_sha=None, rebase_job=None, task_state_after_rebase=None, audited_sha_after_rebase=None):
        self.audited_sha = audited_sha
        self.audited_sha_after_rebase = audited_sha_after_rebase
        self.task_state_after_rebase = task_state_after_rebase
        self.rebase_job = rebase_job or {"state": "succeeded", "error": None, "result": {"state": "agent_done", "failure_reason": None}}
        self.rebase_requests = []
        self.task_state_after_merge = task_state_after_merge
        self.merge_preview = merge_preview
        self.merge_job = merge_job or {"state": "succeeded", "error": None, "result": {"state": "merged", "failure_reason": None}}
        self.merge_requests = []
        self.job_state = job_state
        self.task_state = task_state
        self.questions = questions
        self.grill_rounds = grill_rounds
        self.answered = []
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
        if path.endswith("/answer"):
            self.answered.append((path, body))
            return {"id": path.split("/")[2], "state": "proposed"}
        if path.endswith("/advance"):
            self.advanced.append(path)
            return {"job": {"id": "j-1"}}
        if path.endswith("/approve"):
            self.approved.append((path, body))
            return {"id": path.split("/")[2], "state": "queued"}
        if path.endswith("/merge"):
            self.merge_requests.append((path, body))
            return {"job": {"id": "j-merge"}}
        if path.endswith("/rebase"):
            self.rebase_requests.append(path)
            return {"job": {"id": "j-rebase"}}
        assert path == "/tasks"
        self.created.append(body["goal"])
        return {"id": next(self.ids)}

    def wait(self, job_id):
        if job_id == "j-rebase":
            self.task_state = self.task_state_after_rebase or self.task_state
            self.audited_sha = self.audited_sha_after_rebase or self.audited_sha
            return self.rebase_job
        if job_id == "j-merge":
            self.task_state = self.task_state_after_merge or self.task_state
            return self.merge_job
        if self.job_state != "succeeded":
            return {"state": self.job_state, "error": "boom", "result": None}
        return {"state": "succeeded", "error": None, "result": self.advance_result}

    def get(self, path):
        if path.endswith("/questions"):
            return self.questions
        if path.endswith("/merge-preview"):
            if isinstance(self.merge_preview, Exception):
                raise self.merge_preview
            return self.merge_preview
        if not path.endswith("/approval"):
            return {"state": self.task_state, "failure_reason": self.failure_reason, "grill_rounds": self.grill_rounds,
                    "audited_sha": self.audited_sha}
        self.fetched.append(path)
        return self.approval


def pass_over(tmp_path, issues, server=None, github=None, merge=False):
    server = server or FakeServer()
    github = github or FakeGitHub(issues)
    save_state_if_missing(tmp_path)
    run_pass(github, server, tmp_path, {1001}, lambda: SINCE, merge=merge)
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


ASKING = {"steps": [], "state": "open_questions", "failure_reason": None, "stopped": None}
QUESTION = {"number": 1, "title": "Which db", "body": "Pick @one.", "recommended": "sqlite"}
QUESTIONS = {"round": 1, "questions": [QUESTION]}


def test_a_task_that_opens_questions_gets_one_questions_comment_and_waits_for_answers(tmp_path):
    server = FakeServer(advance_result=ASKING, questions=QUESTIONS, task_state="open_questions", grill_rounds=1)

    github, _ = pass_over(tmp_path, [raw_issue(number=5)], server=server)

    [(issue_number, body)] = github.comments_posted
    assert issue_number == 5
    assert body.startswith("<!-- factory:task=t-1:kind=questions -->")
    assert "**Questions (round 1)**" in body and "```\n1. Which db\nPick @one.\nrecommended: sqlite\n```" in body
    record = load_state(tmp_path).issues[5]
    assert (record.phase, record.questions_comment_id, record.questions_round) == (Phase.AWAITING_ANSWERS, 1, 1)


def awaiting_answers(tmp_path, comment_id=7, round_number=1):
    record = IssueRecord(task_id="t-9", phase=Phase.AWAITING_ANSWERS, questions_comment_id=comment_id, questions_round=round_number)
    save_state(tmp_path, WatchState(watching_since=SINCE, issues={5: record}))
    return record


def test_an_answers_wait_ends_when_the_task_has_moved_past_the_questions(tmp_path):
    awaiting_answers(tmp_path)
    server = FakeServer(task_state="proposed", grill_rounds=1)

    pass_over(tmp_path, [raw_issue(number=5)], server=server)

    assert server.advanced == ["/tasks/t-9/advance"]
    assert load_state(tmp_path).issues[5].phase is Phase.NEW


def test_an_answers_wait_for_an_earlier_round_posts_the_current_rounds_questions(tmp_path):
    awaiting_answers(tmp_path, round_number=1)
    second_round = {"round": 2, "questions": [QUESTION]}
    server = FakeServer(advance_result=ASKING, questions=second_round, task_state="open_questions", grill_rounds=2)

    github, _ = pass_over(tmp_path, [raw_issue(number=5)], server=server)

    [(_, body)] = github.comments_posted
    assert "**Questions (round 2)**" in body
    record = load_state(tmp_path).issues[5]
    assert (record.phase, record.questions_round) == (Phase.AWAITING_ANSWERS, 2)


def test_an_answers_wait_for_the_current_round_neither_advances_nor_comments(tmp_path):
    record = awaiting_answers(tmp_path)
    server = FakeServer(task_state="open_questions", grill_rounds=1)
    github = FakeGitHub([raw_issue(number=5)], comments={5: [QUESTIONS_COMMENT]})

    github, _ = pass_over(tmp_path, [], server=server, github=github)

    assert server.advanced == [] and github.comments_posted == []
    assert load_state(tmp_path).issues[5] == record


def test_a_state_file_from_before_grilling_still_loads(tmp_path):  # guard (green on arrival)
    old = {
        "watching_since": "2026-11-13T00:00:00+00:00",
        "cursor": None,
        "issues": {"5": {"task_id": "t-9", "phase": "awaiting_approval"}},
    }
    (tmp_path / "watch.json").write_text(json.dumps(old))

    record = load_state(tmp_path).issues[5]

    assert (record.phase, record.questions_comment_id, record.questions_round) == (Phase.AWAITING_APPROVAL, None, None)


QUESTIONS_COMMENT = Comment(7, "<!-- factory:task=t-9:kind=questions -->\n\n**Task**", 4242, "factory-bot")


def answer_pass(tmp_path, comments, **server_fields):
    awaiting_answers(tmp_path)
    server = FakeServer(task_state="open_questions", grill_rounds=1, **server_fields)
    github = FakeGitHub([raw_issue(number=5)], comments={5: comments})
    pass_over(tmp_path, [], server=server, github=github)
    return server


def test_a_second_round_forwards_only_the_comments_after_its_own_questions(tmp_path):  # guard (green on arrival)
    awaiting_answers(tmp_path, comment_id=9, round_number=2)
    old_answer = Comment(8, "round one: sqlite", 1001, "Alice")
    second_questions = Comment(9, "<!-- factory:task=t-9:kind=questions -->\n\n**Task**", 4242, "factory-bot")
    new_answer = Comment(10, "round two: yes", 1001, "Alice")
    server = FakeServer(task_state="open_questions", grill_rounds=2)
    github = FakeGitHub([raw_issue(number=5)], comments={5: [QUESTIONS_COMMENT, old_answer, second_questions, new_answer]})

    pass_over(tmp_path, [], server=server, github=github)

    assert server.answered == [("/tasks/t-9/answer", {"round": 2, "answers": "round two: yes"})]


def test_approver_comments_after_the_questions_are_forwarded_with_the_round_and_the_wait_ends(tmp_path):
    first = Comment(8, "use sqlite", 1001, "Alice")
    second = Comment(9, "and keep it small", 1001, "Alice")

    server = answer_pass(tmp_path, [QUESTIONS_COMMENT, first, second])

    assert server.answered == [("/tasks/t-9/answer", {"round": 1, "answers": "use sqlite\n\nand keep it small"})]
    assert server.advanced == ["/tasks/t-9/advance"]
    assert load_state(tmp_path).issues[5].phase is Phase.NEW


def test_a_deleted_questions_comment_stops_the_task_with_a_result_comment_pointing_at_the_terminal(tmp_path):
    server = answer_pass(tmp_path, [Comment(8, "sqlite", 1001, "Alice")])

    assert server.answered == [] and server.advanced == []
    record = load_state(tmp_path).issues[5]
    assert record.phase is Phase.STOPPED and record.result_comment_id == 1


def test_the_stopped_result_for_a_deleted_questions_comment_names_the_terminal_command(tmp_path):
    awaiting_answers(tmp_path)
    github = FakeGitHub([raw_issue(number=5)], comments={5: []})

    pass_over(tmp_path, [], server=FakeServer(task_state="open_questions", grill_rounds=1), github=github)

    [(_, body)] = github.comments_posted
    assert body.startswith("<!-- factory:task=t-9:kind=result -->") and "factory answer t-9" in body


class AnswerRefusingServer(FakeServer):
    def __init__(self, error, **fields):
        super().__init__(**fields)
        self.error = error

    def post(self, path, body=None):
        if path.endswith("/answer"):
            raise ServerRefused(409, self.error, "t-9")
        return super().post(path, body)


@pytest.mark.parametrize("error", ["round changed", "wrong state"])
def test_an_answer_refused_because_the_task_moved_on_resets_the_wait_instead_of_failing(tmp_path, error):
    awaiting_answers(tmp_path)
    server = AnswerRefusingServer(error, task_state="open_questions", grill_rounds=1)
    github = FakeGitHub([raw_issue(number=5)], comments={5: [QUESTIONS_COMMENT, Comment(8, "sqlite", 1001, "Alice")]})

    pass_over(tmp_path, [], server=server, github=github)

    assert load_state(tmp_path).issues[5].phase is Phase.NEW
    assert server.advanced == ["/tasks/t-9/advance"]


@pytest.mark.parametrize("text", ["x" * 30_000, "é" * 15_000], ids=["ascii", "multibyte"])
def test_forwarded_answers_are_cut_to_the_servers_byte_cap(tmp_path, text):
    server = answer_pass(tmp_path, [QUESTIONS_COMMENT, Comment(8, text, 1001, "Alice")])

    [(_, body)] = server.answered
    assert 0 < len(body["answers"].encode()) <= MAX_ANSWER_BYTES
    assert text.startswith(body["answers"])


@pytest.mark.parametrize("comments", [
    [QUESTIONS_COMMENT, Comment(8, "use sqlite", 2002, "mallory")],
    [Comment(8, "use sqlite", 1001, "Alice"), QUESTIONS_COMMENT],
    [QUESTIONS_COMMENT, Comment(8, "<!-- factory:task=t-9:kind=plan -->\nuse sqlite", 1001, "Alice")],
    [QUESTIONS_COMMENT],
], ids=["not-an-approver", "before-the-questions", "factory-marker", "no-answer"])
def test_guard_comments_that_are_not_answers_are_not_forwarded(tmp_path, comments):
    # guard (green on arrival)
    server = answer_pass(tmp_path, comments)

    assert server.answered == [] and server.advanced == []
    assert load_state(tmp_path).issues[5].phase is Phase.AWAITING_ANSWERS


def posted_questions_body(tmp_path, questions):
    server = FakeServer(advance_result=ASKING, questions=questions)
    github, _ = pass_over(tmp_path, [raw_issue(number=5)], server=server)
    return github.comments_posted[0][1]


def test_guard_after_a_crash_only_an_exact_copy_of_the_questions_comment_is_adopted(tmp_path):
    # guard (green on arrival)
    for name in ("first", "second", "third"):
        (tmp_path / name).mkdir()
    body = posted_questions_body(tmp_path / "first", QUESTIONS)
    round_one = Comment(66, body, 4242, "factory-bot")
    by_someone_else = Comment(55, body, 1001, "Alice")
    github = FakeGitHub([raw_issue(number=5)], comments={5: [by_someone_else, round_one]})

    pass_over(tmp_path / "second", [], server=FakeServer(advance_result=ASKING, questions=QUESTIONS), github=github)

    assert load_state(tmp_path / "second").issues[5].questions_comment_id == 66 and github.comments_posted == []

    github = FakeGitHub([raw_issue(number=5)], comments={5: [round_one]})
    second_round = {"round": 2, "questions": [QUESTION]}
    pass_over(tmp_path / "third", [], server=FakeServer(advance_result=ASKING, questions=second_round), github=github)

    assert len(github.comments_posted) == 1
    assert load_state(tmp_path / "third").issues[5].questions_comment_id == 1


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


MERGE_PREVIEW = {"branch": "factory/t-9", "audited_sha": "abc123", "diffstat": " calc.py | 2 ++", "findings": ["nit: naming"]}


def test_with_merge_on_an_approved_task_that_audits_posts_the_result_then_a_merge_comment_and_awaits_the_merge(tmp_path):
    save_state(tmp_path, WatchState(watching_since=SINCE, issues={5: APPROVED}))
    server = FakeServer(advance_result=AUDITED, merge_preview=MERGE_PREVIEW)

    github, _ = pass_over(tmp_path, [], server=server, github=FakeGitHub([raw_issue(number=5)]), merge=True)

    [(_, result_body), (issue_number, merge_body)] = github.comments_posted
    assert issue_number == 5
    assert result_body.startswith("<!-- factory:task=t-9:kind=result -->")
    assert merge_body.startswith("<!-- factory:task=t-9:kind=merge -->")
    assert "👍" in merge_body and "👎" in merge_body
    assert "```\nabc123\n```" in merge_body
    assert "```\n calc.py | 2 ++\n```" in merge_body
    assert "```\n- nit: naming\n```" in merge_body
    assert load_state(tmp_path).issues == {5: IssueRecord(
        **{**vars(APPROVED), "phase": Phase.AWAITING_MERGE, "result_comment_id": 1, "merge_comment_id": 2,
           "merge_body_hash": hashlib.sha256(merge_body.encode()).hexdigest(), "merge_sha": "abc123"})}


MERGE_BODY = "<!-- factory:task=t-9:kind=merge -->\n\n**Task**"
MERGE_COMMENT = Comment(2, MERGE_BODY, 4242, "factory-bot")
AWAITING_THE_MERGE = IssueRecord(
    **{**vars(APPROVED), "phase": Phase.AWAITING_MERGE, "result_comment_id": 1, "merge_comment_id": 2,
       "merge_body_hash": hashlib.sha256(MERGE_BODY.encode()).hexdigest(), "merge_sha": "abc123"})


def merge_reaction_pass(tmp_path, reactions, server=None, comment=MERGE_COMMENT):
    save_state(tmp_path, WatchState(watching_since=SINCE, issues={5: AWAITING_THE_MERGE}))
    server = server or FakeServer(task_state="audited", merge_preview=MERGE_PREVIEW)
    github = FakeGitHub([raw_issue(number=5)], reactions={2: reactions}, comments={5: [comment]})
    pass_over(tmp_path, [], server=server, github=github, merge=True)
    return github, server


def test_an_approver_thumbs_up_on_the_merge_comment_merges_the_shown_commit_and_posts_merged(tmp_path):
    github, server = merge_reaction_pass(tmp_path, [Reaction("+1", 1001, "alice")])

    assert server.merge_requests == [("/tasks/t-9/merge", {"audited_sha": "abc123"})]
    [(issue_number, body)] = github.comments_posted
    assert issue_number == 5
    assert body.startswith("<!-- factory:task=t-9:kind=merged -->")
    assert "unsigned" in body
    assert load_state(tmp_path).issues[5].phase is Phase.MERGED


def test_a_thumbs_up_when_the_merge_preview_is_now_refused_posts_merge_refused_and_does_not_merge(tmp_path):
    refusal = ServerRefused(409, "merge refused", "main moved since the audit")
    server = FakeServer(task_state="audited", merge_preview=refusal)

    github, server = merge_reaction_pass(tmp_path, [Reaction("+1", 1001, "alice")], server=server)

    assert server.merge_requests == []
    [(_, body)] = github.comments_posted
    assert body.startswith("<!-- factory:task=t-9:kind=merge-refused -->")
    assert "main moved since the audit" in body
    assert "factory rebase t-9" in body
    assert load_state(tmp_path).issues[5].phase is Phase.MERGE_STOPPED


def test_guard_a_thumbs_up_when_the_merge_preview_says_main_moved_offers_a_rebase_and_does_not_merge(tmp_path):
    refusal = ServerRefused(409, "merge refused", MAIN_MOVED)
    server = FakeServer(task_state="audited", merge_preview=refusal, audited_sha="abc123")

    github, server = merge_reaction_pass(tmp_path, [Reaction("+1", 1001, "alice")], server=server)

    assert server.merge_requests == []
    [(_, body)] = github.comments_posted
    assert body.startswith("<!-- factory:task=t-9:kind=merge-moved -->")
    record = load_state(tmp_path).issues[5]
    assert (record.phase, record.rebase_sha) == (Phase.AWAITING_REBASE, "abc123")


def test_a_thumbs_up_when_the_audited_commit_is_already_in_main_posts_merge_refused_without_rebase_advice(tmp_path):
    refusal = ServerRefused(409, "merge refused", ALREADY_IN_MAIN)
    server = FakeServer(task_state="audited", merge_preview=refusal)

    github, server = merge_reaction_pass(tmp_path, [Reaction("+1", 1001, "alice")], server=server)

    [(_, body)] = github.comments_posted
    assert body.startswith("<!-- factory:task=t-9:kind=merge-refused -->")
    assert ALREADY_IN_MAIN in body
    assert "factory rebase" not in body and "factory merge" not in body
    assert load_state(tmp_path).issues[5].phase is Phase.MERGE_STOPPED


def test_a_failed_merge_job_because_the_audited_commit_is_already_in_main_posts_merge_refused_without_rebase_advice(tmp_path):
    failed = {"state": "failed", "error": f"merge refused: {ALREADY_IN_MAIN}", "result": None}
    server = FakeServer(task_state="audited", merge_preview=MERGE_PREVIEW, merge_job=failed)

    github, server = merge_reaction_pass(tmp_path, [Reaction("+1", 1001, "alice")], server=server)

    [(_, body)] = github.comments_posted
    assert body.startswith("<!-- factory:task=t-9:kind=merge-refused -->")
    assert "factory rebase" not in body and "factory merge" not in body
    assert load_state(tmp_path).issues[5].phase is Phase.MERGE_STOPPED


def test_a_failed_merge_job_because_main_moved_offers_a_rebase(tmp_path):
    failed = {"state": "failed", "error": f"merge refused: {MAIN_MOVED}", "result": None}
    server = FakeServer(task_state="audited", merge_preview=MERGE_PREVIEW, merge_job=failed, audited_sha="abc123")

    github, server = merge_reaction_pass(tmp_path, [Reaction("+1", 1001, "alice")], server=server)

    [(_, body)] = github.comments_posted
    assert body.startswith("<!-- factory:task=t-9:kind=merge-moved -->")
    assert load_state(tmp_path).issues[5].phase is Phase.AWAITING_REBASE


def test_a_failed_merge_job_on_a_task_still_audited_posts_merge_refused_with_the_jobs_error(tmp_path):
    failed = {"state": "failed", "error": "merge refused: main moved since the audit", "result": None}
    server = FakeServer(task_state="audited", merge_preview=MERGE_PREVIEW, merge_job=failed)

    github, server = merge_reaction_pass(tmp_path, [Reaction("+1", 1001, "alice")], server=server)

    [(_, body)] = github.comments_posted
    assert body.startswith("<!-- factory:task=t-9:kind=merge-refused -->")
    assert "merge refused: main moved since the audit" in body
    assert load_state(tmp_path).issues[5].phase is Phase.MERGE_STOPPED


def test_a_failed_merge_job_on_a_task_that_is_merged_posts_merged(tmp_path):
    failed = {"state": "failed", "error": "server restarted after the merge", "result": None}
    server = FakeServer(task_state="audited", task_state_after_merge="merged", merge_preview=MERGE_PREVIEW, merge_job=failed)

    github, server = merge_reaction_pass(tmp_path, [Reaction("+1", 1001, "alice")], server=server)

    [(_, body)] = github.comments_posted
    assert body.startswith("<!-- factory:task=t-9:kind=merged -->")
    assert load_state(tmp_path).issues[5].phase is Phase.MERGED


def test_an_approvers_thumbs_down_on_the_merge_comment_wins_over_a_thumbs_up_and_posts_not_merged_without_merging(tmp_path):
    reactions = [Reaction("+1", 1001, "alice"), Reaction("-1", 1001, "alice")]

    github, server = merge_reaction_pass(tmp_path, reactions)

    assert server.merge_requests == []
    [(_, body)] = github.comments_posted
    assert body.startswith("<!-- factory:task=t-9:kind=not-merged -->")
    assert load_state(tmp_path).issues[5].phase is Phase.NOT_MERGED


def test_an_awaiting_merge_task_sent_back_for_a_re_audit_gets_a_new_merge_comment_for_the_new_audited_commit(tmp_path):
    save_state(tmp_path, WatchState(watching_since=SINCE, issues={5: AWAITING_THE_MERGE}))
    earlier_result = Comment(1, "<!-- factory:task=t-9:kind=result -->\n\n**Steps**", 4242, "factory-bot")
    github = FakeGitHub([raw_issue(number=5)], comments={5: [earlier_result, MERGE_COMMENT]})
    server = FakeServer(task_state="agent_done", advance_result=AUDITED, merge_preview=MOVED_PREVIEW)

    pass_over(tmp_path, [], server=server, github=github, merge=True)

    [(_, body)] = github.comments_posted
    assert body.startswith("<!-- factory:task=t-9:kind=merge -->")
    record = load_state(tmp_path).issues[5]
    assert (record.phase, record.merge_sha) == (Phase.AWAITING_MERGE, "def456")
    assert record.merge_comment_id != AWAITING_THE_MERGE.merge_comment_id


def test_an_approved_task_the_server_already_reports_audited_posts_the_result_and_merge_comments_when_merge_is_on(tmp_path):
    save_state(tmp_path, WatchState(watching_since=SINCE, issues={5: APPROVED}))
    github = FakeGitHub([raw_issue(number=5)])

    pass_over(tmp_path, [], server=FakeServer(task_state="audited", merge_preview=MERGE_PREVIEW), github=github, merge=True)

    kinds = [body.split("\n", 1)[0] for _, body in github.comments_posted]
    assert kinds == ["<!-- factory:task=t-9:kind=result -->", "<!-- factory:task=t-9:kind=merge -->"]
    assert load_state(tmp_path).issues[5].phase is Phase.AWAITING_MERGE


def test_guard_an_approved_task_the_server_already_reports_audited_is_only_marked_audited_when_merge_is_off(tmp_path):
    save_state(tmp_path, WatchState(watching_since=SINCE, issues={5: APPROVED}))
    github = FakeGitHub([raw_issue(number=5)])

    pass_over(tmp_path, [], server=FakeServer(task_state="audited"), github=github, merge=False)

    assert github.comments_posted == []
    assert load_state(tmp_path).issues[5].phase is Phase.AUDITED


def test_an_awaiting_merge_task_the_server_reports_merged_posts_merged_without_a_reaction(tmp_path):
    save_state(tmp_path, WatchState(watching_since=SINCE, issues={5: AWAITING_THE_MERGE}))
    github = FakeGitHub([raw_issue(number=5)], comments={5: [MERGE_COMMENT]})

    pass_over(tmp_path, [], server=FakeServer(task_state="merged"), github=github, merge=True)

    [(_, body)] = github.comments_posted
    assert body.startswith("<!-- factory:task=t-9:kind=merged -->")
    assert load_state(tmp_path).issues[5].phase is Phase.MERGED


def test_guard_a_merged_comment_already_on_the_issue_is_adopted_not_posted_again(tmp_path):
    save_state(tmp_path, WatchState(watching_since=SINCE, issues={5: AWAITING_THE_MERGE}))
    already = Comment(9, "<!-- factory:task=t-9:kind=merged -->\n\n**Merged**", 4242, "factory-bot")
    github = FakeGitHub([raw_issue(number=5)], comments={5: [MERGE_COMMENT, already]})

    pass_over(tmp_path, [], server=FakeServer(task_state="merged"), github=github, merge=True)

    assert github.comments_posted == []
    assert load_state(tmp_path).issues[5].phase is Phase.MERGED


def test_guard_an_interrupted_merge_job_leaves_the_task_awaiting_the_merge(tmp_path):
    interrupted = {"state": "interrupted", "error": "server restarted", "result": None}
    server = FakeServer(task_state="audited", merge_preview=MERGE_PREVIEW, merge_job=interrupted)

    github, server = merge_reaction_pass(tmp_path, [Reaction("+1", 1001, "alice")], server=server)

    assert github.comments_posted == []
    assert load_state(tmp_path).issues[5] == AWAITING_THE_MERGE


EDITED_COMMENT = Comment(2, MERGE_BODY + "\nedited", 4242, "factory-bot")
MOVED_PREVIEW = {**MERGE_PREVIEW, "audited_sha": "def456"}


@pytest.mark.parametrize("comment, preview", [(EDITED_COMMENT, MERGE_PREVIEW), (MERGE_COMMENT, MOVED_PREVIEW)],
                         ids=["comment edited", "audited commit changed"])
def test_a_thumbs_up_on_a_merge_comment_that_is_no_longer_what_was_shown_posts_merge_changed_and_does_not_merge(tmp_path, comment, preview):
    server = FakeServer(task_state="audited", merge_preview=preview)

    github, server = merge_reaction_pass(tmp_path, [Reaction("+1", 1001, "alice")], server=server, comment=comment)

    assert server.merge_requests == []
    [(_, body)] = github.comments_posted
    assert body.startswith("<!-- factory:task=t-9:kind=merge-changed -->")
    assert load_state(tmp_path).issues[5].phase is Phase.MERGE_STOPPED


def test_guard_a_thumbs_up_by_a_non_approver_does_not_merge_and_fetches_no_comments_or_preview(tmp_path):
    github, server = merge_reaction_pass(tmp_path, [Reaction("+1", 666, "mallory")])

    assert server.merge_requests == []
    assert github.comments_posted == []
    assert github.reactions_fetched == [2]
    assert github.comments_fetched == []
    assert load_state(tmp_path).issues[5] == AWAITING_THE_MERGE


def test_a_refused_merge_preview_posts_a_merge_refused_comment_even_with_the_audited_result_already_on_the_issue(tmp_path):
    save_state(tmp_path, WatchState(watching_since=SINCE, issues={5: APPROVED}))
    audited_result = Comment(3, "<!-- factory:task=t-9:kind=result -->\n\n**Steps**", 4242, "factory-bot")
    github = FakeGitHub([raw_issue(number=5)], comments={5: [audited_result]})
    refusal = ServerRefused(409, "merge refused", "the worktree is dirty")
    server = FakeServer(advance_result=AUDITED, merge_preview=refusal)

    pass_over(tmp_path, [], server=server, github=github, merge=True)

    [(issue_number, body)] = github.comments_posted
    assert issue_number == 5
    assert body.startswith("<!-- factory:task=t-9:kind=merge-refused -->")
    assert "```\nthe worktree is dirty\n```" in body
    assert "factory rebase t-9" in body
    assert load_state(tmp_path).issues[5].phase is Phase.MERGE_STOPPED


def test_a_preview_refusal_because_main_moved_posts_merge_moved_and_awaits_the_rebase_even_with_the_audited_result_already_on_the_issue(tmp_path):
    save_state(tmp_path, WatchState(watching_since=SINCE, issues={5: APPROVED}))
    audited_result = Comment(3, "<!-- factory:task=t-9:kind=result -->\n\n**Steps**", 4242, "factory-bot")
    github = FakeGitHub([raw_issue(number=5)], comments={5: [audited_result]})
    refusal = ServerRefused(409, "merge refused", MAIN_MOVED)
    server = FakeServer(advance_result=AUDITED, merge_preview=refusal, audited_sha="abc123")

    pass_over(tmp_path, [], server=server, github=github, merge=True)

    [(issue_number, body)] = github.comments_posted
    assert issue_number == 5
    assert body.startswith("<!-- factory:task=t-9:kind=merge-moved -->")
    assert "```\nabc123\n```" in body and "```\n" + MAIN_MOVED + "\n```" in body
    assert "👍" in body and "👎" in body
    record = load_state(tmp_path).issues[5]
    assert (record.phase, record.rebase_comment_id, record.rebase_sha) == (Phase.AWAITING_REBASE, 1, "abc123")


AWAITING_MERGE = IssueRecord(
    **{**vars(APPROVED), "phase": Phase.AWAITING_MERGE, "result_comment_id": 1, "merge_comment_id": 2,
       "merge_body_hash": "h", "merge_sha": "abc123"})


def test_guard_an_awaiting_merge_task_the_server_still_calls_audited_is_neither_advanced_nor_collapsed_to_audited(tmp_path):
    save_state(tmp_path, WatchState(watching_since=SINCE, issues={5: AWAITING_MERGE}))
    server = FakeServer(advance_result=AUDITED, task_state="audited", merge_preview=MERGE_PREVIEW)

    github, _ = pass_over(tmp_path, [], server=server, github=FakeGitHub([raw_issue(number=5)]), merge=True)

    assert server.advanced == []
    assert github.comments_posted == []
    assert load_state(tmp_path).issues == {5: AWAITING_MERGE}


AWAITING_THE_REBASE = IssueRecord(
    **{**vars(APPROVED), "phase": Phase.AWAITING_REBASE, "result_comment_id": 1, "rebase_comment_id": 2, "rebase_sha": "abc123"})


def rebase_reaction_pass(tmp_path, reactions, server=None, merge=True):
    save_state(tmp_path, WatchState(watching_since=SINCE, issues={5: AWAITING_THE_REBASE}))
    server = server or FakeServer(task_state="audited", audited_sha="abc123")
    github = FakeGitHub([raw_issue(number=5)], reactions={2: reactions})
    pass_over(tmp_path, [], server=server, github=github, merge=merge)
    return github, server


def test_an_awaiting_rebase_task_the_server_calls_audited_is_neither_advanced_nor_collapsed_and_only_the_rebase_comment_is_read(tmp_path):
    github, server = rebase_reaction_pass(tmp_path, [])

    assert server.advanced == [] and server.rebase_requests == []
    assert github.comments_posted == []
    assert github.reactions_fetched == [2]
    assert load_state(tmp_path).issues == {5: AWAITING_THE_REBASE}


def test_guard_a_thumbs_up_from_someone_who_is_not_an_approver_does_not_rebase(tmp_path):
    github, server = rebase_reaction_pass(tmp_path, [Reaction("+1", 9999, "mallory")])

    assert server.rebase_requests == []
    assert load_state(tmp_path).issues == {5: AWAITING_THE_REBASE}


def test_an_approver_thumbs_up_on_the_rebase_offer_rebases_the_task_and_sets_approved_with_no_comment(tmp_path):
    server = FakeServer(task_state="audited", audited_sha="abc123", task_state_after_rebase="agent_done")

    github, server = rebase_reaction_pass(tmp_path, [Reaction("+1", 1001, "alice")], server=server)

    assert server.rebase_requests == ["/tasks/t-9/rebase"]
    assert github.comments_posted == []
    assert load_state(tmp_path).issues[5].phase is Phase.APPROVED


def test_a_failed_rebase_job_on_a_task_still_audited_at_the_offered_commit_posts_rebase_refused_and_stops(tmp_path):
    failed = {"state": "failed", "error": f"rebase refused: conflicts in {Path.home()}/wt/t-9", "result": None}
    server = FakeServer(task_state="audited", audited_sha="abc123", rebase_job=failed)

    github, server = rebase_reaction_pass(tmp_path, [Reaction("+1", 1001, "alice")], server=server)

    [(issue_number, body)] = github.comments_posted
    assert issue_number == 5
    assert body.startswith("<!-- factory:task=t-9:kind=rebase-refused -->")
    assert "```\nrebase refused: conflicts in ~/wt/t-9\n```" in body
    assert "factory rebase t-9" in body
    assert load_state(tmp_path).issues[5].phase is Phase.MERGE_STOPPED


def test_a_failed_rebase_job_on_a_task_that_is_now_rebased_counts_as_success(tmp_path):
    failed = {"state": "failed", "error": "server restarted after the rebase", "result": None}
    server = FakeServer(task_state="audited", audited_sha="abc123", rebase_job=failed, task_state_after_rebase="agent_done")

    github, server = rebase_reaction_pass(tmp_path, [Reaction("+1", 1001, "alice")], server=server)

    assert github.comments_posted == []
    assert load_state(tmp_path).issues[5].phase is Phase.APPROVED


def test_guard_a_failed_rebase_job_on_a_task_audited_at_a_new_commit_counts_as_success(tmp_path):
    failed = {"state": "failed", "error": "server restarted after the rebase", "result": None}
    server = FakeServer(task_state="audited", audited_sha="abc123", rebase_job=failed, audited_sha_after_rebase="def456")

    github, server = rebase_reaction_pass(tmp_path, [Reaction("+1", 1001, "alice")], server=server)

    assert github.comments_posted == []
    assert load_state(tmp_path).issues[5].phase is Phase.APPROVED


def test_guard_when_main_moves_again_after_a_rebase_a_new_offer_is_posted_and_the_old_one_is_not_reused(tmp_path):
    save_state(tmp_path, WatchState(watching_since=SINCE, issues={5: APPROVED}))
    round_one = Comment(7, render_comment("t-9", "merge-moved", [("Audited commit", "abc123", True)]), 4242, "factory-bot")
    github = FakeGitHub([raw_issue(number=5)], comments={5: [round_one]}, reactions={7: [Reaction("+1", 1001, "alice")]})
    refusal = ServerRefused(409, "merge refused", MAIN_MOVED)
    server = FakeServer(advance_result=AUDITED, merge_preview=refusal, audited_sha="def456")

    pass_over(tmp_path, [], server=server, github=github, merge=True)

    assert server.rebase_requests == []
    [_result, (_, offer_body)] = github.comments_posted
    assert offer_body.startswith("<!-- factory:task=t-9:kind=merge-moved -->") and "def456" in offer_body
    record = load_state(tmp_path).issues[5]
    assert (record.phase, record.rebase_sha) == (Phase.AWAITING_REBASE, "def456")
    assert record.rebase_comment_id != 7


def test_an_awaiting_rebase_task_the_server_calls_merged_posts_merged(tmp_path):
    github, server = rebase_reaction_pass(tmp_path, [], server=FakeServer(task_state="merged"))

    [(_, body)] = github.comments_posted
    assert body.startswith("<!-- factory:task=t-9:kind=merged -->")
    assert load_state(tmp_path).issues[5].phase is Phase.MERGED


def test_guard_an_awaiting_rebase_task_rebased_at_the_terminal_is_re_audited_and_gets_a_new_merge_comment(tmp_path):
    server = FakeServer(task_state="agent_done", advance_result=AUDITED, merge_preview=MERGE_PREVIEW)

    github, server = rebase_reaction_pass(tmp_path, [], server=server)

    assert server.advanced == ["/tasks/t-9/advance"]
    [(_, result_body), (_, merge_body)] = github.comments_posted
    assert merge_body.startswith("<!-- factory:task=t-9:kind=merge -->")
    record = load_state(tmp_path).issues[5]
    assert (record.phase, record.merge_sha) == (Phase.AWAITING_MERGE, "abc123")
    assert record.merge_comment_id == 2


def test_guard_an_awaiting_rebase_task_the_server_calls_failed_posts_the_failure(tmp_path):
    server = FakeServer(task_state="failed", failure_reason="boom")

    github, server = rebase_reaction_pass(tmp_path, [], server=server)

    [(_, body)] = github.comments_posted
    assert "boom" in body
    assert load_state(tmp_path).issues[5].phase is Phase.FAILED


def test_an_approver_thumbs_down_on_the_rebase_offer_wins_over_a_thumbs_up_and_declines_the_rebase(tmp_path):
    reactions = [Reaction("+1", 1001, "alice"), Reaction("-1", 1001, "alice")]

    github, server = rebase_reaction_pass(tmp_path, reactions, server=FakeServer(task_state="audited", audited_sha="abc123"))

    assert server.rebase_requests == []
    [(_, body)] = github.comments_posted
    assert body.startswith("<!-- factory:task=t-9:kind=not-merged -->")
    assert "declined the rebase" in body
    assert load_state(tmp_path).issues[5].phase is Phase.NOT_MERGED


def test_guard_an_interrupted_rebase_job_leaves_the_task_awaiting_the_rebase(tmp_path):
    interrupted = {"state": "interrupted", "error": "server restarted", "result": None}
    server = FakeServer(task_state="audited", audited_sha="abc123", rebase_job=interrupted)

    github, server = rebase_reaction_pass(tmp_path, [Reaction("+1", 1001, "alice")], server=server)

    assert github.comments_posted == []
    assert load_state(tmp_path).issues == {5: AWAITING_THE_REBASE}


def test_a_thumbs_up_on_a_rebase_offer_for_a_commit_the_task_has_moved_past_does_not_rebase_again(tmp_path):
    server = FakeServer(task_state="audited", audited_sha="def456")

    github, server = rebase_reaction_pass(tmp_path, [Reaction("+1", 1001, "alice")], server=server)

    assert server.rebase_requests == []
    assert github.comments_posted == []
    assert load_state(tmp_path).issues[5].phase is Phase.APPROVED


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


@pytest.mark.parametrize("phase", [Phase.FAILED, Phase.STOPPED, Phase.AUDITED, Phase.CHANGED, Phase.REJECTED,
                                   Phase.MERGED, Phase.MERGE_STOPPED, Phase.NOT_MERGED])
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
