from datetime import UTC, datetime

from factory.watch.progress import Progress
from factory.watch.state import IssueRecord, Phase, WatchState, load_state, save_state

SINCE = datetime(2026, 11, 13, tzinfo=UTC)


def test_save_writes_the_whole_state_file(tmp_path):
    state = WatchState(watching_since=SINCE, issues={1: IssueRecord("t1"), 2: IssueRecord("t2")})
    save_state(tmp_path, state)
    progress = Progress(state, tmp_path, state.issues[1])

    state.issues[1].phase = Phase.APPROVED
    state.issues[2].phase = Phase.FAILED
    progress.save()

    saved = load_state(tmp_path)
    assert (saved.issues[1].phase, saved.issues[2].phase) == (Phase.APPROVED, Phase.FAILED)


def test_the_record_is_the_one_it_was_given(tmp_path):
    state = WatchState(watching_since=SINCE, issues={1: IssueRecord("t1")})

    assert Progress(state, tmp_path, state.issues[1]).record is state.issues[1]
