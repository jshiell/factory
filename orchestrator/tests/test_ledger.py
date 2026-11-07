from factory.ledger import Ledger


def test_total_sums_appended_costs(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")

    ledger.append(task_id="t1", cost_usd=0.25)
    ledger.append(task_id="t2", cost_usd=0.5)

    assert ledger.total() == 0.75


def test_total_is_zero_when_ledger_file_does_not_exist(tmp_path):
    assert Ledger(tmp_path / "missing.jsonl").total() == 0


def test_would_exceed_only_when_total_plus_next_budget_is_over_cap(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(task_id="t1", cost_usd=1.0)

    assert not ledger.would_exceed(cap_usd=1.5, next_budget_usd=0.5)
    assert ledger.would_exceed(cap_usd=1.5, next_budget_usd=0.51)


def test_stage_is_recorded_when_given_and_absent_otherwise(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append("t1", 0.1, stage="audit")
    ledger.append("t1", 0.2)

    assert ledger.entries()[0]["stage"] == "audit"
    assert "stage" not in ledger.entries()[1]
