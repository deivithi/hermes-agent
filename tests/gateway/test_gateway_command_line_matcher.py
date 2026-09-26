"""Tests for the strict gateway command-line matcher.

Regression guard for the Windows ``hermes gateway restart`` silent-outage bug:
the previous loose substring match (``"... gateway" in cmdline``) false-matched
``gateway status``/``dashboard`` siblings and unrelated processes such as
``python -m tui_gateway``, which let ``restart()`` race a still-draining old
process and ``status``/``start`` report false positives.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from gateway.status import (
    gateway_spawn_intent_subcommand as spawn_intent,
    looks_like_gateway_command_line as matches,
    looks_like_gateway_runtime_command_line as matches_runtime,
)


@pytest.mark.parametrize("quoted", [False, True])
@pytest.mark.parametrize("args,expected", [
    (["gateway", "run"], True),
    (["gateway", "restart"], True),
    (["--profile", "work", "gateway", "run"], True),
    (["gateway", "status"], False),
    (["serve"], False),
])
def test_native_pm_bootstrap_is_the_running_cli(tmp_path, quoted, args, expected):
    from hermes_cli._launchers import runtime_command

    command = runtime_command(tmp_path / "install with spaces", args, python="python.exe")
    text = subprocess.list2cmdline(command) if quoted else " ".join(command)
    assert matches_runtime(text) is expected


@pytest.mark.skipif(os.name != "nt", reason="Windows CommandLineToArgvW quoting contract")
@pytest.mark.parametrize("quoted", [False, True])
@pytest.mark.parametrize("suffix", ["O'Neil", "home with spaces", "O'Neil\\"])
def test_native_pm_bootstrap_preserves_windows_path_quotes(tmp_path, quoted, suffix):
    from hermes_cli._launchers import runtime_command

    command = runtime_command(
        tmp_path / "O'Neil" / "source tree", ["gateway", "run"],
        python=str(tmp_path / "Python tools" / "python.exe"), home=str(tmp_path / suffix),
    )
    text = subprocess.list2cmdline(command) if quoted else " ".join(command)
    assert matches_runtime(text)


@pytest.mark.parametrize("quoted", [False, True])
@pytest.mark.parametrize("mutation", [
    "prepend", "append", "exec", "wrong_module", "watcher", "other_interpreter",
])
def test_native_pm_recognition_rejects_inline_impostors(tmp_path, mutation, quoted):
    from hermes_cli._launchers import runtime_command

    command = runtime_command(tmp_path, ["gateway", "run"], python="python.exe")
    if mutation == "prepend":
        command[3] = "print('not a launcher'); " + command[3]
    elif mutation == "append":
        command[3] += "; print('not a launcher')"
    elif mutation == "exec":
        command = runtime_command(tmp_path, ["gateway", "run"], python="python.exe", code="print('gateway')")
    elif mutation == "wrong_module":
        command = runtime_command(tmp_path, ["gateway", "run"], python="python.exe", module="not_hermes")
    elif mutation == "watcher":
        command[3] = "print(" + repr(command[3]) + ")"
    else:
        command[0] = "not-python.exe"
    text = subprocess.list2cmdline(command) if quoted else " ".join(command)
    assert not matches_runtime(text)


def test_native_pm_identity_keeps_live_pid_metadata(tmp_path, monkeypatch):
    from gateway import status
    from hermes_cli._launchers import runtime_command

    command = runtime_command(tmp_path / "source tree", ["gateway", "run"], python="python.exe")
    record = {
        "pid": os.getpid(), "kind": "hermes-gateway", "argv": command,
        "start_time": status._get_process_start_time(os.getpid()),
        "hermes_home": str(tmp_path.resolve()),
    }
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(status, "_build_pid_record", lambda: record)
    monkeypatch.setattr(status, "_read_process_cmdline", lambda pid: " ".join(command))
    assert status.acquire_gateway_runtime_lock()
    try:
        status.write_pid_file()
        # This unscoped reader used to unlink a LIVE gateway's PID metadata,
        # causing the next strict update inventory to refuse the installation.
        assert status.get_running_pid() == os.getpid()
        assert (tmp_path / "gateway.pid").exists()
        assert (tmp_path / "gateway.lock").exists()
        identity = status.get_running_pid_identity_strict(tmp_path / "gateway.pid")
        assert identity is not None and identity[0] == os.getpid()
    finally:
        status.release_gateway_runtime_lock()


ACCEPT = [
    "pythonw.exe -m hermes_cli.main gateway run",
    r"C:\Users\me\hermes\venv\Scripts\pythonw.exe -m hermes_cli.main gateway run",
    "python -m hermes_cli.main --profile work gateway run",
    "python -m hermes_cli.main gateway run --replace",
    "python -m hermes_cli/main.py gateway run",
    "python gateway/run.py",
    "hermes-gateway.exe",
    "hermes gateway",          # bare `hermes gateway` defaults to run
    "hermes gateway run",
    # profile selector AFTER the `gateway` token (argv is profile-position
    # agnostic — _apply_profile_override strips --profile/-p anywhere)
    "hermes gateway --profile work run",
    "python -m hermes_cli.main gateway -p work run",
    "hermes gateway --profile=work run",
    # a profile literally NAMED "gateway"
    "hermes -p gateway gateway run",
    "python -m hermes_cli.main --profile gateway gateway run",
    # quoted Windows paths with spaces (shlex-aware tokenization)
    r'"C:\Program Files\Hermes\hermes-gateway.exe"',
    r'"C:\Program Files\Hermes\gateway\run.py" run',
    r'"C:\Program Files\Py\pythonw.exe" -m hermes_cli.main gateway run',
]

REJECT = [
    "python -m tui_gateway",                              # unrelated module
    "python -m hermes_cli.main gateway status",           # other subcommand
    "python -m hermes_cli.main gateway restart",
    "python -m hermes_cli.main gateway stop",
    "python -m hermes_cli.main --profile x dashboard",    # non-gateway subcommand
    "some random python -m mygateway thing",
    "",
    None,
]


@pytest.mark.parametrize("cmd", ACCEPT)
def test_accepts_real_gateway_run(cmd):
    assert matches(cmd) is True


@pytest.mark.parametrize("cmd", REJECT)
def test_rejects_non_gateway_run(cmd):
    assert matches(cmd) is False


# ``python -c <src> <old_pid> <gateway argv…>`` — the detached restart watcher
# (hermes_cli.gateway._spawn_gateway_restart_watcher). Its trailing argv is the command it will
# spawn LATER, so reading identity off it made the updater's post-relaunch liveness poll vouch for
# the watcher instead of a gateway (#107002).
INLINE_SOURCE_REJECT = [
    'python -c "import time; time.sleep(1)" 14980 python -m hermes_cli.main gateway run',
    r'"C:\Users\me\hermes\venv\Scripts\python.exe" -c "import os" 14980 '
    r'"C:\Users\me\hermes\venv\Scripts\python.exe" -m hermes_cli.main gateway run',
    'python -u -c "import os" 14980 python -m hermes_cli.main --profile work gateway run',
    'python -uc "import os" 14980 hermes gateway run',
    # Options that take a SEPARATE operand must not end the option walk before ``-c`` (the operand
    # is not the start of the program's own argv). The repo itself spawns ``-I -S -B -X utf8 …``
    # (hermes_cli/_old_updater.py, _update_takeover.py), so this shape is not hypothetical.
    'python -X utf8 -c "import os" 14980 python -m hermes_cli.main gateway run',
    'python -W ignore -c "import os" 14980 python -m hermes_cli.main gateway run',
    'python --check-hash-based-pycs always -c "import os" 14980 hermes gateway run',
    'python -I -S -B -X utf8 -c "import os" 14980 python -m hermes_cli.main gateway run',
    # ``-q`` (quiet) takes NO operand, unlike ``-Q``; a case-folded walk would skip past the ``-c``.
    'python -q -c "import os" 14980 python -m hermes_cli.main gateway run',
]


# Real gateways whose interpreter carries operand-taking options must STILL be recognised — the
# value-aware walk must not over-reject. Mirror image of INLINE_SOURCE_REJECT.
INTERPRETER_OPTION_ACCEPT = [
    "python -X utf8 -m hermes_cli.main gateway run",
    "python -W ignore -m hermes_cli.main gateway run",
    "python -q -m hermes_cli.main gateway run",
    "python -I -S -B -X utf8 -m hermes_cli.main gateway run",
    "python --check-hash-based-pycs always -m hermes_cli.main gateway run",
]


@pytest.mark.parametrize("cmd", INTERPRETER_OPTION_ACCEPT)
def test_accepts_gateway_behind_operand_taking_interpreter_options(cmd):
    assert matches(cmd) is True


# The repo's own non-gateway ``-X utf8`` spawn shapes must stay unmatched.
@pytest.mark.parametrize(
    "cmd",
    [
        "python -I -S -B -X utf8 /tmp/update_takeover.py",
        "python -X utf8 -E script.py",
    ],
)
def test_operand_taking_options_do_not_manufacture_a_gateway(cmd):
    assert matches(cmd) is False


@pytest.mark.parametrize("cmd", INLINE_SOURCE_REJECT)
def test_rejects_interpreter_running_inline_source(cmd):
    assert matches(cmd) is False
    assert matches_runtime(cmd) is False


# Spawn INTENT is the mirror image of process identity: the same wrapper that must not be read as a
# live gateway MUST still be recognised as "launching this eventually produces a gateway runtime".
# tests/_fixtures/live_system_guard.py relies on it — without this, the autouse guard stopped
# blocking the detached restart watcher and real gateways leaked out of the test run.
@pytest.mark.parametrize("cmd", INLINE_SOURCE_REJECT)
def test_spawn_intent_sees_through_the_inline_source_wrapper(cmd):
    assert spawn_intent(cmd) == "run"


@pytest.mark.parametrize("cmd", ACCEPT)
def test_spawn_intent_matches_plain_gateway_run(cmd):
    assert spawn_intent(cmd) == "run"


@pytest.mark.parametrize("cmd", REJECT)
def test_spawn_intent_rejects_non_gateway_commands(cmd):
    assert spawn_intent(cmd) != "run"


def test_spawn_intent_keeps_read_only_subcommands_spawnable():
    """The guard only blocks run/start/restart; a ``-c``-wrapped ``gateway status`` must stay
    launchable (tests/test_live_system_guard_self_test.py asserts it passes through)."""
    cmd = 'python -c "import sys; print(sys.argv[1:])" -m hermes_cli.main gateway status'
    assert spawn_intent(cmd) == "status"


def test_spawn_intent_ignores_inline_source_without_a_gateway_argv():
    assert spawn_intent('python -c "import time; time.sleep(1)" 14980') is None


# Atomic Hermes' bundled desktop runner (regression for #22418): it shares
# HERMES_HOME with the CLI and must be recognised as a gateway so
# ``gateway run --replace`` enters the replace/lock-handoff path instead of
# colliding with the desktop runner's still-held scoped locks.
ATOMIC_DESKTOP = (
    "/Applications/Atomic Hermes.app/Contents/Resources/python-server/python "
    "/Applications/Atomic Hermes.app/Contents/Resources/python-server/desktop-gateway.py"
)


def test_accepts_atomic_desktop_gateway():
    assert matches(ATOMIC_DESKTOP) is True
    assert matches_runtime(ATOMIC_DESKTOP) is True
