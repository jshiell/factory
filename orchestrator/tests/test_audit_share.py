import json

import pytest

from factory.audit_corpus import CASES, corpus_hash
from factory.audit_share import build_bundle, render_markdown, shareable_meta

HOME = "/home/someone/.factory/_shared/plugins"
SECRET = "never share this appended prompt"


def raw_meta():
    return {
        "corpus_hash": "c0", "case_hashes": {"case-01": "h1"}, "cases": ["case-01"], "runs": 3, "complete": True,
        "prompt_hash": "p0",
        "audit_config": {"model": "sonnet", "budget_usd": 0.5, "append_system_prompt": SECRET,
                         "plugin_dirs": [f"{HOME}/alpha", f"{HOME}/beta"]},
        "plugin_hashes": {f"{HOME}/alpha": "a1", f"{HOME}/beta": "b1"},
    }


def test_the_shareable_meta_carries_no_path_and_no_prompt_text():
    text = json.dumps(shareable_meta(raw_meta()))

    assert "/home/someone" not in text and SECRET not in text


def test_the_shareable_meta_names_plugins_by_directory_name_and_hashes_the_prompt():
    meta = shareable_meta(raw_meta())

    assert meta["audit_config"]["plugin_dirs"] == ["alpha", "beta"]
    assert meta["plugin_hashes"] == {"alpha": "a1", "beta": "b1"}
    assert "append_system_prompt" not in meta["audit_config"]
    assert len(meta["audit_config"]["append_system_prompt_sha256"]) == 16
    assert (meta["corpus_hash"], meta["runs"], meta["complete"], meta["prompt_hash"]) == ("c0", 3, True, "p0")


def test_different_prompts_give_different_hashes_and_none_stays_none():
    other = raw_meta()
    other["audit_config"]["append_system_prompt"] = SECRET + "!"
    none = raw_meta()
    none["audit_config"]["append_system_prompt"] = None

    assert shareable_meta(other)["audit_config"] != shareable_meta(raw_meta())["audit_config"]
    assert shareable_meta(none)["audit_config"]["append_system_prompt_sha256"] is None


def test_shareable_meta_is_idempotent_so_a_bundle_and_a_directory_compare_equal():
    once = shareable_meta(raw_meta())

    assert shareable_meta(once) == once


def test_the_shareable_meta_names_the_harness_and_a_missing_one_reads_as_claude():
    other = raw_meta()
    other["harness"] = "other"

    assert shareable_meta(other)["harness"] == "other"
    assert shareable_meta(raw_meta())["harness"] == "claude"


def test_the_shareable_meta_names_the_sandbox_and_a_missing_one_reads_as_nono():
    other = raw_meta()
    other["sandbox"] = "other"

    assert shareable_meta(other)["sandbox"] == "other"
    assert shareable_meta(raw_meta())["sandbox"] == "nono"


def test_two_plugin_directories_with_one_name_are_refused():
    meta = raw_meta()
    meta["audit_config"]["plugin_dirs"] = ["/a/same", "/b/same"]
    meta["plugin_hashes"] = {"/a/same": "x", "/b/same": "y"}

    with pytest.raises(ValueError, match="same"):
        shareable_meta(meta)


PAIR = [CASES[0], CASES[1]]


def write_eval(directory, cases=PAIR, runs=2, complete=True, config=None):
    config = config or {"model": "sonnet", "append_system_prompt": SECRET, "plugin_dirs": [f"{HOME}/alpha"]}
    meta = {"corpus_hash": corpus_hash(cases), "case_hashes": {c.id: "h" for c in cases}, "cases": [c.id for c in cases],
            "runs": runs, "complete": complete, "prompt_hash": "p0", "audit_config": config,
            "plugin_hashes": {f"{HOME}/alpha": "a1"}}
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "meta.json").write_text(json.dumps(meta))
    for case in cases:
        for run in range(1, runs + 1):
            record = {"case": case.id, "run": run, "label": case.label, "category": case.category, "outcome": case.label,
                      "findings": [f"see {directory}/x.py"], "cost_usd": 0.05, "assumed_usd": 0.0}
            (directory / f"{case.id}-r{run}.json").write_text(json.dumps(record))
    return directory


def test_the_bundle_has_meta_records_summary_and_the_exit_code(tmp_path):
    directory = write_eval(tmp_path / "2026-01-02")

    bundle = build_bundle(directory, CASES)

    assert bundle["schema"] == 1 and bundle["eval"] == "2026-01-02"
    assert bundle["meta"] == shareable_meta(json.loads((directory / "meta.json").read_text()))
    assert [(r["case"], r["run"], r["outcome"]) for r in bundle["records"]] == [(c.id, n, c.label) for c in PAIR for n in (1, 2)]
    assert bundle["summary"]["runs"] == 4 and bundle["exit_code"] == 0


def test_the_bundle_is_json_with_no_path_finding_or_prompt_text_by_default(tmp_path):
    directory = write_eval(tmp_path / "2026-01-02")

    text = json.dumps(build_bundle(directory, CASES))

    assert str(tmp_path) not in text and "/home/someone" not in text and SECRET not in text and "see " not in text
    assert all("findings" not in r for r in json.loads(text)["records"])


def test_the_exit_code_comes_from_the_meta_runs_and_complete(tmp_path):
    directory = write_eval(tmp_path / "e", complete=False)

    assert build_bundle(directory, CASES)["exit_code"] == 3


def test_a_directory_without_meta_is_refused(tmp_path):
    (tmp_path / "e").mkdir()

    with pytest.raises(ValueError, match=r"meta\.json"):
        build_bundle(tmp_path / "e", CASES)


def test_a_corpus_that_changed_since_the_run_is_refused(tmp_path):
    directory = write_eval(tmp_path / "e")
    meta = json.loads((directory / "meta.json").read_text())
    meta["corpus_hash"] = "0" * 16
    (directory / "meta.json").write_text(json.dumps(meta))

    with pytest.raises(ValueError, match="corpus"):
        build_bundle(directory, CASES)


def test_a_case_that_is_no_longer_in_the_corpus_is_refused(tmp_path):
    directory = write_eval(tmp_path / "e")

    with pytest.raises(ValueError, match="corpus"):
        build_bundle(directory, CASES[1:])


def test_findings_are_included_only_on_request_and_with_control_characters_escaped(tmp_path):
    directory = write_eval(tmp_path / "e")
    path = directory / f"{PAIR[0].id}-r1.json"
    record = json.loads(path.read_text())
    record["findings"] = ["\x1b[31mred\x1b[0m"]
    path.write_text(json.dumps(record))

    bundle = build_bundle(directory, CASES, include_findings=True)

    first = bundle["records"][0]
    assert "\x1b" not in first["findings"][0] and "red" in first["findings"][0]
    assert all("findings" in r for r in bundle["records"])


def bundle_of(tmp_path, findings=False):
    return json.loads(json.dumps(build_bundle(write_eval(tmp_path / "2026-01-02"), CASES, include_findings=findings)))


def test_the_markdown_has_the_run_the_table_the_intervals_and_the_cost(tmp_path):
    text = render_markdown(bundle_of(tmp_path))

    assert text.startswith("# Auditor eval 2026-01-02")
    assert f"| {PAIR[0].id} | {PAIR[0].label} | {PAIR[0].category} | {PAIR[0].label} {PAIR[0].label} |" in text
    bad = sum(c.label == "fail" for c in PAIR)
    assert f"false-pass (bad judged pass): majority wrong 0/{bad} cases (Wilson 95% " in text
    assert "cost (real) $0.200" in text and "over cases, not runs" in text
    assert "exit code 0" in text and "complete" in text


def test_the_markdown_names_the_harness(tmp_path):
    assert "harness claude" in render_markdown(bundle_of(tmp_path))


def test_the_markdown_is_deterministic_and_leaks_nothing(tmp_path):
    bundle = bundle_of(tmp_path)

    text = render_markdown(bundle)

    assert render_markdown(bundle) == text
    assert str(tmp_path) not in text and "/home/someone" not in text and SECRET not in text


def test_a_run_without_a_verdict_shows_a_question_mark(tmp_path):
    bundle = bundle_of(tmp_path)
    bundle["records"][0]["outcome"] = "infra:timeout"

    assert f"| ? {PAIR[0].label} |" in render_markdown(bundle)


def test_findings_go_in_code_fences_that_text_cannot_close(tmp_path):
    bundle = bundle_of(tmp_path, findings=True)
    bundle["records"][0]["findings"] = ["x\n```\n![i](http://evil/p.png) @someone"]

    text = render_markdown(bundle)

    assert "````\nx" in text and "\n````\n" in text
    assert render_markdown(bundle_of(tmp_path / "again")).count("```") == 0
