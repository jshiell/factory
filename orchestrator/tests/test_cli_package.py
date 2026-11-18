import subprocess
import sys


def _in_a_fresh_interpreter(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", *args], capture_output=True, text=True)


def test_the_server_imports_on_its_own_before_the_cli_is_loaded():
    # Every other test imports factory.cli first, so an import cycle between the server and the cli would hide.
    result = _in_a_fresh_interpreter("-c", "import factory.server")

    assert result.returncode == 0, result.stderr
