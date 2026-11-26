import pytest

from factory.cli import _preparser


def test_repo_option_is_taken_from_anywhere_on_the_command_line():
    known, rest = _preparser().parse_known_args(["list", "--repo", "proj"])

    assert (known.repo, rest) == ("proj", ["list"])


def test_no_repo_option_means_none():
    assert _preparser().parse_known_args(["list"])[0].repo is None


@pytest.mark.parametrize("name", ["a b", "../x", ".hidden", "-x", "_server", "_SHARED", "a/b"])
def test_a_repo_name_outside_the_charset_or_reserved_is_a_usage_error(name, capsys):
    with pytest.raises(SystemExit) as exit_:
        _preparser().parse_known_args(["--repo=" + name, "list"])

    assert exit_.value.code == 2
    assert "--repo" in capsys.readouterr().err
