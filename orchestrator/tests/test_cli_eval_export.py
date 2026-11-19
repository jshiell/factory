import io
import json

from factory.cli import IO, main
from test_audit_share import SECRET, write_eval


def export(*argv):
    out, err = io.StringIO(), io.StringIO()
    code = main(["eval-export", *map(str, argv)], IO(io.StringIO(), out, err), None)
    return code, out.getvalue(), err.getvalue()


def test_export_writes_run_json_and_run_md_beside_the_eval_and_names_them(tmp_path):
    directory = write_eval(tmp_path / "2026-01-02")

    code, out, err = export(directory)

    assert code == 0 and err == ""
    bundle = json.loads((directory / "run.json").read_text())
    assert bundle["schema"] == 1 and bundle["eval"] == "2026-01-02"
    assert (directory / "run.md").read_text().startswith("# Auditor eval 2026-01-02")
    assert str(directory / "run.json") in out and str(directory / "run.md") in out


def test_export_with_out_writes_there_and_leaves_the_eval_directory_alone(tmp_path):
    directory = write_eval(tmp_path / "2026-01-02")
    (tmp_path / "share").mkdir()

    code, _, _ = export(directory, "--out", tmp_path / "share")

    assert code == 0 and (tmp_path / "share" / "run.json").is_file() and (tmp_path / "share" / "run.md").is_file()
    assert not (directory / "run.json").exists()


def test_export_leaves_findings_prompt_text_and_paths_out_unless_asked(tmp_path):
    directory = write_eval(tmp_path / "2026-01-02")

    export(directory)

    text = (directory / "run.json").read_text() + (directory / "run.md").read_text()
    assert "see " not in text and SECRET not in text and "/home/someone" not in text and str(tmp_path) not in text


def test_export_with_findings_includes_them_in_both_files(tmp_path):
    directory = write_eval(tmp_path / "2026-01-02")

    export(directory, "--with-findings")

    assert "x.py" in (directory / "run.json").read_text() and "## Findings" in (directory / "run.md").read_text()


def test_export_overwrites_an_earlier_export(tmp_path):
    directory = write_eval(tmp_path / "2026-01-02")
    (directory / "run.json").write_text("old")

    export(directory)

    assert json.loads((directory / "run.json").read_text())["schema"] == 1


def test_a_missing_directory_or_out_is_a_usage_error_and_writes_nothing(tmp_path):
    directory = write_eval(tmp_path / "2026-01-02")

    missing = export(tmp_path / "nowhere")
    bad_out = export(directory, "--out", tmp_path / "nowhere")

    assert missing[0] == bad_out[0] == 2 and "eval directory" in missing[2] and "--out" in bad_out[2]
    assert not (directory / "run.json").exists()


def test_an_eval_that_cannot_be_exported_is_refused_with_the_reason_and_writes_nothing(tmp_path):
    (tmp_path / "e").mkdir()

    code, _, err = export(tmp_path / "e")

    assert code == 1 and "meta.json" in err and not (tmp_path / "e" / "run.json").exists()
