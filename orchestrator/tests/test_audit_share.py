import json

import pytest

from factory.audit_share import shareable_meta

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


def test_two_plugin_directories_with_one_name_are_refused():
    meta = raw_meta()
    meta["audit_config"]["plugin_dirs"] = ["/a/same", "/b/same"]
    meta["plugin_hashes"] = {"/a/same": "x", "/b/same": "y"}

    with pytest.raises(ValueError, match="same"):
        shareable_meta(meta)
