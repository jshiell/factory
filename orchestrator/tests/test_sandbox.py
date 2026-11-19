from factory.sandbox import NonoSandbox, SandboxPolicy, sandbox_named, without_private


def test_a_directory_that_holds_a_private_one_is_dropped_and_others_are_kept(tmp_path):
    tmp_path = tmp_path.resolve()
    private = tmp_path / "home" / "state"
    private.mkdir(parents=True)
    other = tmp_path / "bin"
    other.mkdir()

    kept = without_private([str(tmp_path / "home"), str(other)], [str(private)])

    assert kept == (str(other),)


def test_a_directory_equal_to_or_inside_a_private_one_is_kept_only_when_inside(tmp_path):
    tmp_path = tmp_path.resolve()
    private = tmp_path / "state"
    inside = private / "tools"
    inside.mkdir(parents=True)

    assert without_private([str(private)], [str(private)]) == ()
    assert without_private([str(inside)], [str(private)]) == (str(inside),)


def test_an_empty_private_list_keeps_everything(tmp_path):
    assert without_private([str(tmp_path), "/"], []) == (str(tmp_path), "/")


def test_relative_and_symlinked_inputs_are_compared_resolved(tmp_path, monkeypatch):
    tmp_path = tmp_path.resolve()
    private = tmp_path / "home" / "state"
    private.mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "home")
    monkeypatch.chdir(tmp_path)

    assert without_private([str(link)], [str(private)]) == ()
    assert without_private(["home"], [str(private)]) == ()


def test_nono_sandbox_without_an_executable_runs_nono():
    assert NonoSandbox().wrap(SandboxPolicy("/wt"))[0] == "nono"


def test_sandbox_named_without_an_executable_uses_the_sandboxes_own_default():
    assert sandbox_named("nono").wrap(SandboxPolicy("/wt"))[0] == "nono"
    assert sandbox_named("nono", "/x/nono").wrap(SandboxPolicy("/wt"))[0] == "/x/nono"


def test_nono_sandbox_keeps_a_given_executable_even_an_empty_one():
    assert NonoSandbox("/x/nono").wrap(SandboxPolicy("/wt"))[0] == "/x/nono"
    assert NonoSandbox("").wrap(SandboxPolicy("/wt"))[0] == ""
