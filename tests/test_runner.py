"""``run_command``: starting tools the way a terminal finds them."""

from __future__ import annotations

import os
import sys

import pytest

from sciproof.runner import CommandNotRunnable, CommandTimeout, _resolve_batch_tool, run_command

windows_only = pytest.mark.skipif(sys.platform != "win32", reason=".cmd lookup is Windows-only")


def _tool(directory, name="othertool.cmd", text="@echo off\r\necho from-%~n0\r\n"):
    directory.mkdir(exist_ok=True)
    path = directory / name
    path.write_bytes(text.encode())
    return path


def _env(directory):
    return {**os.environ, "PATH": f"{directory}{os.pathsep}{os.environ['PATH']}"}


@windows_only
def test_a_cmd_tool_on_path_is_started_by_its_bare_name(tmp_path):
    _tool(tmp_path / "bin")
    result = run_command(["othertool"], tmp_path, 30, _env(tmp_path / "bin"))
    assert result.exit_code == 0
    assert b"from-othertool" in result.stdout


@windows_only
def test_a_cmd_tool_is_started_by_its_name_with_extension(tmp_path):
    _tool(tmp_path / "bin")
    result = run_command(["othertool.cmd"], tmp_path, 30, _env(tmp_path / "bin"))
    assert b"from-othertool" in result.stdout


@windows_only
def test_the_project_directory_is_not_searched_for_a_bare_name(tmp_path):
    _tool(tmp_path, "localtool.cmd")  # in the working directory, not on PATH
    assert _resolve_batch_tool("localtool", {"PATH": str(tmp_path / "empty")}) is None
    with pytest.raises(CommandNotRunnable):
        run_command(["localtool"], tmp_path, 30, {**os.environ, "PATH": str(tmp_path / "empty")})


@windows_only
def test_an_executable_earlier_on_path_wins_over_a_later_cmd(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    (first / "othertool.exe").write_bytes(b"")
    _tool(second)
    env = {**os.environ, "PATH": f"{first}{os.pathsep}{second}"}
    assert _resolve_batch_tool("othertool", env) is None


@windows_only
def test_a_path_with_a_separator_is_left_alone(tmp_path):
    _tool(tmp_path / "bin")
    assert _resolve_batch_tool("bin/othertool", _env(tmp_path / "bin")) is None


def test_other_platforms_never_rewrite_the_command(tmp_path):
    if sys.platform == "win32":
        pytest.skip("covered by the Windows tests")
    assert _resolve_batch_tool("othertool", _env(tmp_path)) is None


def test_a_missing_command_is_still_reported(tmp_path):
    with pytest.raises(CommandNotRunnable, match="nonexistent-tool-xyz"):
        run_command(["nonexistent-tool-xyz"], tmp_path, 30)


def test_command_timeout_kills_process_and_raises(tmp_path):
    with pytest.raises(CommandTimeout, match="did not finish within"):
        run_command([sys.executable, "-c", "import time; time.sleep(5)"], tmp_path, timeout=0.1)


def test_command_in_nonexistent_directory_raises(tmp_path):
    with pytest.raises(CommandNotRunnable):
        run_command([sys.executable, "-c", "pass"], tmp_path / "missing", timeout=30)


# ------------------------------------------------------------- process trees

_TREE_SCRIPT = (
    "import subprocess, sys, time;"
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']);"
    "open(sys.argv[1], 'w').write(str(child.pid));"
    "time.sleep(60)"
)

_DETACHED_SCRIPT = (
    "import subprocess, sys, time;"
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],"
    " start_new_session=True);"
    "open(sys.argv[1], 'w').write(str(child.pid));"
    "time.sleep(60)"
)

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="pins the POSIX killpg branch")
windows_tree = pytest.mark.skipif(
    sys.platform != "win32", reason="pins the Windows taskkill branch"
)


@posix_only
def test_a_timed_out_command_kills_its_whole_process_tree(tmp_path):
    """The child a command started must not survive the timeout (the bug of #34)."""
    import signal
    import time

    pid_file = tmp_path / "child.pid"
    started = time.monotonic()
    with pytest.raises(CommandTimeout):
        run_command([sys.executable, "-c", _TREE_SCRIPT, str(pid_file)], tmp_path, timeout=1)
    assert time.monotonic() - started < 30  # nowhere near the 60s the tree asked for
    child_pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)  # signal 0 only probes: the child must be gone
    with pytest.raises(ProcessLookupError):  # belt and braces: it really is dead
        os.kill(child_pid, signal.SIGKILL)


@windows_tree
def test_a_timed_out_command_kills_its_whole_process_tree_on_windows(tmp_path):
    """taskkill /T must take down the child along with the timed-out command."""
    import subprocess
    import time

    pid_file = tmp_path / "child.pid"
    started = time.monotonic()
    with pytest.raises(CommandTimeout):
        run_command([sys.executable, "-c", _TREE_SCRIPT, str(pid_file)], tmp_path, timeout=1)
    assert time.monotonic() - started < 30
    child_pid = int(pid_file.read_text())
    probe = subprocess.run(
        ["tasklist", "/FI", f"PID eq {child_pid}"], capture_output=True, text=True, check=False
    )
    assert "No tasks" in probe.stdout


@posix_only
def test_a_detached_grandchild_cannot_hold_the_timeout_open(tmp_path):
    """A grandchild outside the process group keeps the pipes open: drain and return anyway."""
    import signal
    import time

    pid_file = tmp_path / "grandchild.pid"
    started = time.monotonic()
    with pytest.raises(CommandTimeout):
        run_command([sys.executable, "-c", _DETACHED_SCRIPT, str(pid_file)], tmp_path, timeout=1)
    elapsed = time.monotonic() - started
    grandchild_pid = int(pid_file.read_text())
    os.kill(grandchild_pid, signal.SIGKILL)  # it survived the kill by design; clean it up
    assert elapsed < 30  # the drain step returned instead of waiting out the 60s
