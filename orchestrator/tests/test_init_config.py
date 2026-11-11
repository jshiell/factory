import io
import re

import pytest

from factory.cli import IO
from factory.init_config import TEMPLATE, init_global_config
from factory.settings import load_settings


@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_HOME", str(tmp_path / "fh"))
    return tmp_path / "fh"


def run_init():
    out, err = io.StringIO(), io.StringIO()
    return init_global_config(IO(None, out, err)), out.getvalue(), err.getvalue()


def test_init_writes_the_template_into_the_harness_home(harness):
    code, out, _ = run_init()

    assert code == 0
    assert (harness / "factory.toml").read_text() == TEMPLATE
    assert str(harness / "factory.toml") in out


def test_every_commented_example_in_the_template_is_a_valid_global_setting(harness, repo):
    lines = [re.sub(r"^# ", "", line) for line in TEMPLATE.splitlines() if re.match(r"^# (\[\w+\]|\w+ = )", line)]
    assert lines
    harness.mkdir()
    (harness / "factory.toml").write_text("\n".join(lines) + "\n")

    load_settings(None, cwd=repo)
