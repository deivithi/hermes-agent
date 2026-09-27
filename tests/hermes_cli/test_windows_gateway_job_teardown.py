"""Regression tests for #48820 (4th repro): job-object teardown killed the
post-update respawned gateway silently, and the updater printed
"✓ Restarting Windows gateway profile(s)" anyway.

Two fixes under test:

1. ``_spawn_gateway_restart_watcher``'s inlined watcher source must
   (a) route the respawned gateway's stray stdout/stderr to
       ``logs/gateway-stdio.log`` (it was ``DEVNULL`` — a gateway killed by
       parent Job Object teardown left ZERO trace anywhere), and
   (b) stamp ``_HERMES_GATEWAY_BREAKAWAY`` =1/0 on the respawn env exactly
       like the canonical ``gateway_windows._spawn_detached``, so the
       lifecycle/exit-diag records show whether the gateway escaped the
       parent's Job Object.

2. ``_resume_windows_gateways_after_update`` must verify a stable gateway
   process actually exists (via ``gateway_windows._wait_for_gateway_ready``)
   before printing the ✓ — a truthy launch return only proves the watcher
   process was created, not that the respawned gateway survived the
   updater's Job Object teardown.
"""

import ast
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import hermes_cli.gateway as gateway


@pytest.fixture(autouse=True)
def isolated_runtime_store(tmp_path, monkeypatch):
    # Resolve the real launcher against an empty test-owned store, never the
    # installed manifest. The real bootstrap and subprocess still execute.
    monkeypatch.setenv("HERMES_RUNTIME_DIR", str(tmp_path / "runtime"))

# ---------------------------------------------------------------------------
# 1. Watcher template contract
# ---------------------------------------------------------------------------

def _captured_watcher_source(monkeypatch) -> str:
    """Spawn the watcher with a mocked Popen and return the inlined -c source."""
    captured = {}

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs

        class _P:
            pid = 12345

        return _P()

    monkeypatch.setattr(gateway.subprocess, "Popen", fake_popen)
    assert gateway._spawn_gateway_restart_watcher(
        999999, ["python", "-m", "hermes_cli.main", "gateway", "run"]
    )
    argv = captured["argv"]
    bootstrap = ast.parse(argv[argv.index("-c") + 1])
    return ast.literal_eval(bootstrap.body[-1].value.args[0])


def test_restart_watcher_bootstraps_dependencies_in_fresh_process(tmp_path, monkeypatch):
    """Run the captured real watcher in a fresh interpreter and isolated home.

    A bare PM Python -c watcher dies importing ruamel via gateway.status;
    the parent process having bootstrapped does not provision its child.
    """
    home = tmp_path / "home"
    home.mkdir()
    marker = tmp_path / "respawned"
    monkeypatch.setenv("HERMES_HOME", str(home))
    # Give the sandbox its own committed selection without installing packages
    # or leasing/mutating the real home's generation. Reuse this test runtime's
    # installed dependencies through a .pth in the disposable generation.
    import ruamel.yaml
    from pm.environments import install_state_dir, site_packages
    state = install_state_dir(gateway.PROJECT_ROOT)
    environment = state / "environments" / "fixture" / "venv"
    packages = site_packages(environment)
    packages.mkdir(parents=True)
    (environment / "pyvenv.cfg").write_text("include-system-site-packages = false\n")
    (packages / "fixture.pth").write_text(str(Path(ruamel.yaml.__file__).resolve().parents[2]) + "\n")
    (state / "facts.json").write_text(json.dumps({"packages": {"venv": {"environment": str(environment)}}}))
    captured = {}
    # Use a real exited process PID, never a live gateway.
    departed = subprocess.Popen([sys.executable, "-c", "pass"])
    departed.wait(timeout=20)
    stub = f"from pathlib import Path; Path({str(marker)!r}).write_text('ok')"
    with monkeypatch.context() as patch:
        def capture(argv, **kwargs):
            captured.update(argv=argv, kwargs=kwargs)
            return object()
        patch.setattr(gateway.subprocess, "Popen", capture)
        assert gateway._spawn_gateway_restart_watcher(
            departed.pid, [sys.executable, "-c", stub], host=False,
        )
    clean_env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}}
    result = subprocess.run(
        captured["argv"], cwd=tmp_path, env=clean_env,
        capture_output=True, text=True, timeout=40,
    )
    assert result.returncode == 0, result.stderr
    deadline = time.monotonic() + 10
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert marker.read_text() == "ok"

class TestWatcherRespawnTemplate:

    def test_respawn_source_compiles(self, monkeypatch):
        """The inlined -c template is built via str.format over a
        dedented literal — guard against brace/indentation regressions."""
        src = _captured_watcher_source(monkeypatch)
        compile(src, "<watcher>", "exec")

# ---------------------------------------------------------------------------
# 2. Post-update resume liveness gate
# ---------------------------------------------------------------------------
