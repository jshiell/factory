import json

from factory.audit_compare import compare, find_previous

IDS = ["case-01", "case-02", "case-03"]


def make_run(root, name, verdicts, *, hashes=None, complete=True, cases=None, config=None, prompt="p1", cost=0.03):
    directory = root / name
    directory.mkdir(parents=True)
    hashes = hashes or {i: f"h-{i}" for i in IDS}
    meta = {"corpus_hash": "c", "case_hashes": hashes, "cases": cases or IDS, "runs": len(next(iter(verdicts.values()))),
            "complete": complete, "audit_config": config or {"model": "sonnet"}, "prompt_hash": prompt}
    (directory / "meta.json").write_text(json.dumps(meta))
    for case_id, outcomes in verdicts.items():
        for run, outcome in enumerate(outcomes, 1):
            record = {"case": case_id, "run": run, "label": "pass", "category": "x", "outcome": outcome,
                      "findings": [], "cost_usd": cost, "assumed_usd": 0}
            (directory / f"{case_id}-r{run}.json").write_text(json.dumps(record))
    return directory


def steady():
    return {i: ["pass", "pass", "pass"] for i in IDS}


def test_the_previous_run_is_the_latest_complete_full_one_before_this_one(tmp_path):
    make_run(tmp_path, "20260101T000000", steady())
    newest_full = make_run(tmp_path, "20260102T000000", steady())
    make_run(tmp_path, "20260103T000000", steady(), complete=False)      # stopped early
    make_run(tmp_path, "20260104T000000", {"case-01": ["pass"]}, cases=["case-01"])  # a smoke run
    current = make_run(tmp_path, "20260105T000000", steady())

    assert find_previous(tmp_path, current, IDS) == newest_full


def test_there_is_no_previous_run_when_none_is_complete_and_full(tmp_path):
    current = make_run(tmp_path, "20260105T000000", steady())
    make_run(tmp_path, "20260104T000000", steady(), complete=False)

    assert find_previous(tmp_path, current, IDS) is None


def test_a_missing_evals_directory_means_no_previous_run(tmp_path):
    assert find_previous(tmp_path / "nope", tmp_path / "x", IDS) is None


def test_verdict_changes_are_listed_per_case_with_the_cost_change(tmp_path):
    old = make_run(tmp_path, "old", steady(), cost=0.03)
    new_verdicts = {**steady(), "case-02": ["pass", "fail", "pass"]}
    new = make_run(tmp_path, "new", new_verdicts, cost=0.05)

    text = "\n".join(compare(old, new))

    assert "case-02: pass pass pass -> pass fail pass" in text
    assert "case-01" not in text.split("changed")[1] and "cost" in text and "0.27" in text and "0.45" in text


def test_no_change_is_said_plainly(tmp_path):
    old, new = make_run(tmp_path, "old", steady()), make_run(tmp_path, "new", steady())

    assert "no verdict changed" in "\n".join(compare(old, new))


def test_a_case_whose_content_hash_differs_is_not_compared(tmp_path):
    old = make_run(tmp_path, "old", steady())
    new = make_run(tmp_path, "new", {**steady(), "case-03": ["fail", "fail", "fail"]},
                   hashes={"case-01": "h-case-01", "case-02": "h-case-02", "case-03": "different"})

    text = "\n".join(compare(old, new))

    assert "case-03" in text and "not compared" in text and "pass pass pass -> fail" not in text


def test_the_header_says_whether_the_config_or_prompt_differ(tmp_path):
    old = make_run(tmp_path, "old", steady())
    new = make_run(tmp_path, "new", steady(), config={"model": "opus"}, prompt="p2")

    text = "\n".join(compare(old, new))

    assert "AUDIT_CONFIG differs" in text and "audit prompt differs" in text
    assert "AUDIT_CONFIG differs" not in "\n".join(compare(old, make_run(tmp_path, "same", steady())))


def test_a_directory_without_meta_is_compared_by_case_id_with_a_warning(tmp_path):
    old = tmp_path / "plan22"
    old.mkdir()
    for run in (1, 2, 3):
        (old / f"case-01-r{run}.json").write_text(json.dumps({"case": "case-01", "run": run, "label": "pass", "category": "x",
                                                                "outcome": "pass", "findings": [], "cost_usd": 0.03, "assumed_usd": 0}))
    new = make_run(tmp_path, "new", {"case-01": ["pass", "pass", "fail"]})

    text = "\n".join(compare(old, new))

    assert "no meta.json" in text and "compared by id" in text and "case-01: pass pass pass -> pass pass fail" in text
