import os
import sys

from factory.process import run_to_exit


def python(code):
    return [sys.executable, "-c", code]


def test_the_tail_of_the_combined_output_is_returned_up_to_the_limit(tmp_path):
    code = "import sys; print('x' * 5000, flush=True); print('to stderr', file=sys.stderr, flush=True); print('last line')"

    result = run_to_exit(python(code), tmp_path, None, 10, capture_limit=100)

    assert result.exit_code == 0 and result.timed_out is False
    assert len(result.tail) <= 100
    assert result.tail.endswith("last line\n") and "to stderr" in result.tail


def test_without_a_limit_nothing_is_captured(tmp_path):
    result = run_to_exit(python("print('hi')"), tmp_path, None, 10)

    assert result.tail is None


def test_invalid_utf8_in_the_output_is_replaced_not_raised(tmp_path):
    code = "import sys; sys.stdout.buffer.write(b'ok \\xff\\xfe end')"

    result = run_to_exit(python(code), tmp_path, None, 10, capture_limit=100)

    assert result.tail.startswith("ok ") and result.tail.endswith(" end")


def test_a_timeout_still_kills_the_tree_while_capturing(tmp_path):
    marker = tmp_path / "pid"
    code = f"import os, time; open({str(marker)!r}, 'w').write(str(os.getpid())); print('before', flush=True); time.sleep(30)"

    result = run_to_exit(python(code), tmp_path, None, 1, capture_limit=100)

    assert result.timed_out is True and result.exit_code is None
    assert "before" in result.tail
    pid = int(marker.read_text())
    try:
        os.kill(pid, 0)
        alive = True
    except ProcessLookupError:
        alive = False
    assert not alive
