from factory.ledger import Ledger


def test_total_sums_appended_costs(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")

    ledger.append(task_id="t1", cost_usd=0.25)
    ledger.append(task_id="t2", cost_usd=0.5)

    assert ledger.total() == 0.75
