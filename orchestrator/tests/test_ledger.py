from factory.ledger import Ledger


def test_total_sums_appended_costs(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")

    ledger.append(task_id="t1", cost_usd=0.25)
    ledger.append(task_id="t2", cost_usd=0.5)

    assert ledger.total() == 0.75


def test_total_is_zero_when_ledger_file_does_not_exist(tmp_path):
    assert Ledger(tmp_path / "missing.jsonl").total() == 0
