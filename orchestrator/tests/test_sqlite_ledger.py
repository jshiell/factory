from factory.sqlite_ledger import SqliteLedger


def test_entries_come_back_in_append_order_with_cost_and_assumed_flag(tmp_path):
    ledger = SqliteLedger(tmp_path / "factory.db")

    ledger.append("t1", 0.25)
    ledger.append("t2", 0.5, assumed=True)

    assert ledger.entries() == [
        {"task_id": "t1", "cost_usd": 0.25, "assumed": False},
        {"task_id": "t2", "cost_usd": 0.5, "assumed": True},
    ]


def test_stage_is_recorded_when_given_and_absent_otherwise(tmp_path):
    ledger = SqliteLedger(tmp_path / "factory.db")
    ledger.append("t1", 0.1, stage="audit")
    ledger.append("t1", 0.2)

    assert ledger.entries()[0]["stage"] == "audit"
    assert "stage" not in ledger.entries()[1]
