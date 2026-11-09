from datetime import datetime

import pytest

from factory.approve_plan import build_implementation_prompt
from factory.digest import plan_digest
from factory.retry_task import RetryRefused, retry_task, retry_with_plan
from factory.store import TaskStore
from factory.task import Task, TaskState

NOW = datetime(2026, 1, 2, 3, 4, 5)


@pytest.fixture
def store(tmp_path):
    (tmp_path / "tasks").mkdir()
    return TaskStore(tmp_path / "tasks")


def put(store, state, task_id="t1", **fields):
    task = Task(id=task_id, prompt="", state=state, **fields)
    store.save(task)
    return task


@pytest.mark.parametrize("state", [s for s in TaskState if s not in (TaskState.FAILED, TaskState.AUDITED)])
def test_retry_refuses_a_task_that_is_not_failed_or_audited(store, state):
    put(store, state, goal="g")

    with pytest.raises(RetryRefused, match=state.value):
        retry_task("t1", store, lambda: NOW)


@pytest.mark.parametrize("state", [TaskState.FAILED, TaskState.AUDITED])
def test_retry_refuses_a_task_with_no_goal(store, state):
    put(store, state)

    with pytest.raises(RetryRefused, match="goal"):
        retry_task("t1", store, lambda: NOW)


def test_retrying_a_failed_task_creates_a_proposed_task_with_the_goal_and_leaves_the_old_one(store):
    old = put(store, TaskState.FAILED, goal="add multiply", failure_reason="tests_failed")

    new = retry_task("t1", store, lambda: NOW)

    assert (new.state, new.goal, new.retry_of) == (TaskState.PROPOSED, "add multiply", "t1")
    assert new.id == "20260102-030405"
    assert store.load(new.id) == new and store.load("t1") == old


def test_an_audited_task_can_be_retried_too(store):
    put(store, TaskState.AUDITED, goal="g")

    assert retry_task("t1", store, lambda: NOW).retry_of == "t1"


def test_two_retries_in_the_same_second_get_distinct_ids(store):
    put(store, TaskState.FAILED, goal="g")

    first = retry_task("t1", store, lambda: NOW)
    second = retry_task("t1", store, lambda: NOW)

    assert (first.id, second.id) == ("20260102-030405", "20260102-030405-2")


PLAN = "1. add it"


def put_approved(store, state=TaskState.FAILED, task_id="t1", **fields):
    """What approve_plan leaves behind: plan, digest, review and the approved prompt."""
    fields = {"goal": "add power", "plan": PLAN, "plan_digest": plan_digest(PLAN), "plan_risks": ("r",),
              "review_findings": ("f",), **fields}
    task = Task(id=task_id, prompt=build_implementation_prompt(fields["goal"], fields["plan"]), state=state, **fields)
    store.save(task)
    return task


def test_keeping_the_plan_queues_a_new_task_with_the_approved_plan_and_leaves_the_old_one(store):
    old = put_approved(store, failure_reason="tests_failed", worktree="/w", base_sha="abc", fix_rounds_used=2)

    new = retry_with_plan("t1", store, lambda: NOW, plan_digest(PLAN), ("f",))

    assert new.state is TaskState.QUEUED and new.retry_of == "t1" and new.id == "20260102-030405"
    assert (new.goal, new.plan, new.plan_risks, new.plan_digest, new.review_findings) == (
        "add power", PLAN, ("r",), plan_digest(PLAN), ("f",))
    assert new.prompt == build_implementation_prompt("add power", PLAN, ("f",))
    assert (new.worktree, new.base_sha, new.failure_reason, new.fix_rounds_used) == (None, None, None, 0)
    assert store.load(new.id) == new and store.load("t1") == old


def refused_keeping_the_plan(store, shown=None, match=None):
    before = sorted(store._directory.iterdir())
    with pytest.raises(RetryRefused, match=match):
        retry_with_plan("t1", store, lambda: NOW, shown or plan_digest(PLAN), ("f",))
    assert sorted(store._directory.iterdir()) == before


@pytest.mark.parametrize("state", [s for s in TaskState if s not in (TaskState.FAILED, TaskState.AUDITED)])
def test_keeping_the_plan_refuses_a_task_that_is_not_failed_or_audited(store, state):
    put_approved(store, state)

    refused_keeping_the_plan(store, match=state.value)


def test_keeping_the_plan_refuses_a_task_with_no_goal(store):
    put_approved(store, goal=None)

    refused_keeping_the_plan(store, match="goal")


def test_keeping_the_plan_refuses_a_task_that_failed_before_it_had_a_plan(store):
    put(store, TaskState.FAILED, goal="g", failure_reason="plan_failed")

    refused_keeping_the_plan(store, match="plan")


def test_keeping_the_plan_refuses_a_stored_digest_that_does_not_match_the_plan(store):
    put_approved(store, plan_digest="0" * 64)

    refused_keeping_the_plan(store, match="digest")


def test_keeping_the_plan_refuses_a_shown_digest_that_does_not_match(store):
    put_approved(store)

    refused_keeping_the_plan(store, shown="f" * 64, match="digest")


@pytest.mark.parametrize("reason", ["review_rejected", "plan_rejected_by_human"])
def test_keeping_the_plan_refuses_a_plan_that_was_never_approved(store, reason):
    task = put_approved(store, failure_reason=reason)
    store.save(Task(**{**task.__dict__, "prompt": ""}))  # approve_plan is the only writer of a non-empty prompt

    refused_keeping_the_plan(store, match="never approved")


# Characterisation (these start green): tasks whose approved plan may be kept.
def test_guard_an_audited_task_can_keep_its_plan(store):
    put_approved(store, TaskState.AUDITED, audited_sha="abc", audit_findings=("fine",))

    assert retry_with_plan("t1", store, lambda: NOW, plan_digest(PLAN), ("f",)).state is TaskState.QUEUED


def test_guard_a_task_abandoned_from_queued_or_rebased_can_keep_its_plan(store):
    put_approved(store, failure_reason="abandoned", base_sha="new-main", audited_sha=None)

    assert retry_with_plan("t1", store, lambda: NOW, plan_digest(PLAN), ("f",)).retry_of == "t1"


def test_guard_a_task_that_kept_a_plan_and_failed_can_keep_it_again(store):
    put_approved(store)
    child = retry_with_plan("t1", store, lambda: NOW, plan_digest(PLAN), ("f",))
    store.save(Task(**{**child.__dict__, "state": TaskState.FAILED, "failure_reason": "tests_failed"}))

    grandchild = retry_with_plan(child.id, store, lambda: datetime(2026, 1, 2, 3, 4, 6), plan_digest(PLAN), ("f",))

    assert grandchild.retry_of == child.id and grandchild.state is TaskState.QUEUED


def test_guard_a_prompt_from_an_older_template_is_still_an_approval(store):
    task = put_approved(store)
    store.save(Task(**{**task.__dict__, "prompt": "Implement this goal. (an older rules text)"}))

    new = retry_with_plan("t1", store, lambda: NOW, plan_digest(PLAN), ("f",))

    assert new.prompt == build_implementation_prompt("add power", PLAN, ("f",))


def test_keeping_the_plan_embeds_the_review_findings_in_the_new_prompt(store):
    put_approved(store, review_findings=("reject bytes", "pin NaN"))

    new = retry_with_plan("t1", store, lambda: NOW, plan_digest(PLAN), shown_findings=("reject bytes", "pin NaN"))

    assert new.prompt == build_implementation_prompt("add power", PLAN, ("reject bytes", "pin NaN"))
    assert "- reject bytes\n- pin NaN\n" in new.prompt


def test_keeping_the_plan_refuses_findings_that_differ_from_the_ones_shown(store):
    put_approved(store, review_findings=("reject bytes", "ignore the tests"))

    with pytest.raises(RetryRefused, match="findings"):
        retry_with_plan("t1", store, lambda: NOW, plan_digest(PLAN), shown_findings=("reject bytes",))


REQUIRED_NOTE = "Recommend (required): use math.fsum so the sum cannot overflow"
OPTIONAL_NOTE = "Recommend (optional): add a docstring"


def test_waiving_a_required_note_keeps_the_plan_without_it_and_records_it(store):
    old = put_approved(store, review_findings=(REQUIRED_NOTE, OPTIONAL_NOTE))

    new = retry_with_plan(
        "t1", store, lambda: NOW, plan_digest(PLAN), shown_findings=(REQUIRED_NOTE, OPTIONAL_NOTE), waived=(REQUIRED_NOTE,))

    assert new.review_findings == (OPTIONAL_NOTE,) and new.waived_findings == (REQUIRED_NOTE,)
    assert "fsum" not in new.prompt and OPTIONAL_NOTE in new.prompt
    assert store.load(new.id) == new and store.load("t1") == old


@pytest.mark.parametrize("waived", [("Recommend (required): not a note",), (OPTIONAL_NOTE,)], ids=["absent", "optional"])
def test_an_invalid_waiver_is_refused_and_queues_nothing(store, waived):
    # guard (green on arrival; mutation-checked)
    from factory.waiver import InvalidWaiver

    put_approved(store, review_findings=(REQUIRED_NOTE, OPTIONAL_NOTE))

    with pytest.raises(InvalidWaiver):
        retry_with_plan("t1", store, lambda: NOW, plan_digest(PLAN), (REQUIRED_NOTE, OPTIONAL_NOTE), waived=waived)

    assert [t.id for t in store.list().tasks] == ["t1"]


def test_a_kept_plan_retry_keeps_the_notes_already_waived(store):
    # guard (green on arrival; mutation-checked)
    earlier = "Recommend (required): earlier waived"
    put_approved(store, review_findings=(REQUIRED_NOTE,), waived_findings=(earlier,))

    new = retry_with_plan("t1", store, lambda: NOW, plan_digest(PLAN), (REQUIRED_NOTE,), waived=(REQUIRED_NOTE,))

    assert new.waived_findings == (earlier, REQUIRED_NOTE)


GAP = "the empty list was not handled"


def keep(store, task_id="t1", **kwargs):
    return retry_with_plan(task_id, store, lambda: NOW, plan_digest(PLAN), ("f",), **kwargs)


def test_keeping_the_plan_after_a_rejected_audit_sends_its_findings_to_the_new_attempt(store):
    put_approved(store, failure_reason="audit_rejected", audit_findings=(GAP,))

    assert GAP in keep(store).prompt


def test_guard_other_sources_send_no_gaps(store):
    # guard (green on arrival; mutation-checked)
    put_approved(store, failure_reason="tests_failed", audit_findings=(GAP,))
    put_approved(store, TaskState.AUDITED, task_id="t2", audit_findings=(GAP,))
    put_approved(store, task_id="t3", failure_reason="audit_rejected", audit_findings=("", "  \n "))

    for task_id in ("t1", "t2", "t3"):
        assert "EARLIER ATTEMPT" not in keep(store, task_id).prompt and GAP not in keep(store, task_id).prompt


def test_keeping_the_plan_refuses_gaps_that_differ_from_the_ones_shown(store):
    put_approved(store, failure_reason="audit_rejected", audit_findings=(GAP,))
    before = sorted(store._directory.iterdir())

    with pytest.raises(RetryRefused, match="audit"):
        keep(store, shown_audit_gaps=("something else",))

    assert sorted(store._directory.iterdir()) == before


def test_keeping_the_plan_accepts_the_gaps_that_were_shown(store):
    put_approved(store, failure_reason="audit_rejected", audit_findings=(GAP,))

    assert GAP in keep(store, shown_audit_gaps=(GAP,)).prompt


def test_a_gap_quoting_a_waived_note_reaches_neither_the_new_attempt_nor_its_auditor(store):
    from factory.audit_task import build_audit_prompt

    other = "tests were skipped"
    put_approved(store, review_findings=(REQUIRED_NOTE,), failure_reason="audit_rejected",
                 audit_findings=(f"Not handled: {REQUIRED_NOTE.lower()}.", other))

    new = retry_with_plan("t1", store, lambda: NOW, plan_digest(PLAN), (REQUIRED_NOTE,), waived=(REQUIRED_NOTE,))

    assert other in new.prompt
    for prompt in (new.prompt, build_audit_prompt(new, "diff")):
        assert "fsum" not in prompt


def test_a_retry_of_a_kept_plan_retry_still_gets_the_gaps(store):
    put_approved(store, failure_reason="audit_rejected", audit_findings=(GAP,))
    child = keep(store)
    store.save(Task(**{**child.__dict__, "state": TaskState.FAILED, "failure_reason": "tests_failed"}))

    grandchild = retry_with_plan(child.id, store, lambda: datetime(2026, 1, 2, 3, 4, 6), plan_digest(PLAN), ("f",))

    assert GAP in grandchild.prompt and grandchild.prior_audit_findings == (GAP,)


def test_fresh_audit_findings_replace_the_inherited_ones(store):
    put_approved(store, prior_audit_findings=("old gap",), failure_reason="audit_rejected", audit_findings=(GAP,))

    new = keep(store)

    assert new.prior_audit_findings == (GAP,) and "old gap" not in new.prompt


def test_an_audited_task_passes_no_gaps_on(store):
    put_approved(store, TaskState.AUDITED, prior_audit_findings=("old gap",), audit_findings=("fine",))

    new = keep(store)

    assert new.prior_audit_findings == () and "old gap" not in new.prompt


def test_reinstating_an_earlier_waived_note_sends_it_again_and_takes_it_off_the_waived_list(store):
    from factory.audit_task import build_audit_prompt

    put_approved(store, review_findings=(OPTIONAL_NOTE,), waived_findings=(REQUIRED_NOTE,))

    new = retry_with_plan("t1", store, lambda: NOW, plan_digest(PLAN), (OPTIONAL_NOTE,), reinstated=(REQUIRED_NOTE,))

    assert set(new.review_findings) == {REQUIRED_NOTE, OPTIONAL_NOTE} and new.waived_findings == ()
    for prompt in (new.prompt, build_audit_prompt(new, "diff")):
        assert "fsum" in prompt and OPTIONAL_NOTE in prompt
    assert store.load(new.id) == new


def test_a_stored_marker_is_recomputed_when_notes_are_reinstated(store):
    from factory.audit_task import build_audit_prompt
    from factory.review_notes import MAX_ITEMS, TRUNCATED, capped_findings, notes_block

    required = tuple(f"Recommend (required): note {i}" for i in range(MAX_ITEMS - 1))
    stored = (*required, OPTIONAL_NOTE, TRUNCATED)
    put_approved(store, review_findings=stored, waived_findings=(REQUIRED_NOTE,))

    new = retry_with_plan("t1", store, lambda: NOW, plan_digest(PLAN), stored, reinstated=(REQUIRED_NOTE,))

    assert new.review_findings == capped_findings((*required, OPTIONAL_NOTE, REQUIRED_NOTE))
    assert new.review_findings.count(TRUNCATED) <= 1 and OPTIONAL_NOTE not in new.review_findings
    assert notes_block(new.review_findings) in new.prompt


def test_a_marker_goes_when_the_reinstated_list_is_no_longer_cut(store):
    from factory.review_notes import TRUNCATED

    put_approved(store, review_findings=(OPTIONAL_NOTE, TRUNCATED), waived_findings=(REQUIRED_NOTE,))

    new = retry_with_plan("t1", store, lambda: NOW, plan_digest(PLAN), (OPTIONAL_NOTE, TRUNCATED), reinstated=(REQUIRED_NOTE,))

    assert TRUNCATED not in new.review_findings


def test_a_note_that_is_not_waived_cannot_be_reinstated(store):
    # guard (green on arrival; mutation-checked)
    from factory.waiver import InvalidWaiver

    put_approved(store, review_findings=(OPTIONAL_NOTE,), waived_findings=(REQUIRED_NOTE,))

    with pytest.raises(InvalidWaiver):
        retry_with_plan("t1", store, lambda: NOW, plan_digest(PLAN), (OPTIONAL_NOTE,), reinstated=("Recommend (required): other",))

    assert [t.id for t in store.list().tasks] == ["t1"]


def test_a_note_waived_once_cannot_be_reinstated_twice(store):
    # guard (green on arrival; mutation-checked)
    from factory.waiver import InvalidWaiver

    put_approved(store, review_findings=(OPTIONAL_NOTE,), waived_findings=(REQUIRED_NOTE,))

    with pytest.raises(InvalidWaiver):
        retry_with_plan("t1", store, lambda: NOW, plan_digest(PLAN), (OPTIONAL_NOTE,), reinstated=(REQUIRED_NOTE, REQUIRED_NOTE))

    assert [t.id for t in store.list().tasks] == ["t1"]


def test_reinstating_one_of_two_identical_waived_notes_leaves_the_other_waived(store):
    # guard (green on arrival; mutation-checked)
    put_approved(store, review_findings=(OPTIONAL_NOTE,), waived_findings=(REQUIRED_NOTE, REQUIRED_NOTE))

    new = retry_with_plan("t1", store, lambda: NOW, plan_digest(PLAN), (OPTIONAL_NOTE,), reinstated=(REQUIRED_NOTE,))

    assert new.waived_findings == (REQUIRED_NOTE,) and new.review_findings.count(REQUIRED_NOTE) == 1


def test_waiving_and_reinstating_in_one_retry_is_refused(store):
    # guard (green on arrival; mutation-checked)
    from factory.waiver import InvalidWaiver

    other = "Recommend (required): other"
    put_approved(store, review_findings=(other,), waived_findings=(REQUIRED_NOTE,))

    with pytest.raises(InvalidWaiver):
        retry_with_plan("t1", store, lambda: NOW, plan_digest(PLAN), (other,), waived=(other,), reinstated=(REQUIRED_NOTE,))

    assert [t.id for t in store.list().tasks] == ["t1"]


def test_a_fresh_audit_gap_quoting_a_reinstated_note_is_still_sent(store):
    # guard (green on arrival; mutation-checked)
    gap = f"Not handled: {REQUIRED_NOTE.lower()}."
    put_approved(store, review_findings=(OPTIONAL_NOTE,), waived_findings=(REQUIRED_NOTE,), failure_reason="audit_rejected",
                 audit_findings=(gap,))

    new = retry_with_plan("t1", store, lambda: NOW, plan_digest(PLAN), (OPTIONAL_NOTE,), reinstated=(REQUIRED_NOTE,))

    assert gap in new.prompt


def test_reinstating_up_to_the_required_cap_is_accepted(store):
    # guard (green on arrival; mutation-checked)
    from factory.review_notes import MAX_ITEMS

    nine = tuple(f"Recommend (required): note {i}" for i in range(MAX_ITEMS - 1))
    put_approved(store, review_findings=nine, waived_findings=(REQUIRED_NOTE,))

    new = retry_with_plan("t1", store, lambda: NOW, plan_digest(PLAN), nine, reinstated=(REQUIRED_NOTE,))

    assert REQUIRED_NOTE in new.review_findings


def test_reinstating_beyond_the_required_cap_is_refused(store):
    # defensive guard on a hand-built task: the app paths never store more than the cap (green on arrival; mutation-checked)
    from factory.review_notes import MAX_ITEMS
    from factory.waiver import InvalidWaiver

    full = tuple(f"Recommend (required): note {i}" for i in range(MAX_ITEMS))
    put_approved(store, review_findings=full, waived_findings=(REQUIRED_NOTE,))

    with pytest.raises(InvalidWaiver):
        retry_with_plan("t1", store, lambda: NOW, plan_digest(PLAN), full, reinstated=(REQUIRED_NOTE,))

    assert [t.id for t in store.list().tasks] == ["t1"]




def test_a_failed_reinstate_is_worded_as_a_reinstate(store):
    from factory.waiver import InvalidWaiver

    put_approved(store, review_findings=(OPTIONAL_NOTE,), waived_findings=(REQUIRED_NOTE,))

    with pytest.raises(InvalidWaiver, match="reinstate") as error:
        retry_with_plan("t1", store, lambda: NOW, plan_digest(PLAN), (OPTIONAL_NOTE,), reinstated=("Recommend (required): other",))

    assert "waive" not in str(error.value).replace("waived", "")
