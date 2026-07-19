"""Runs the container entrypoint offline to guard its control flow.

`runuser` is stubbed on PATH (form: `runuser -u <user> -- <cmd...>`), so this
needs neither root nor docker. Regression target: the final handoff must `exec`
a real program, not a shell function — `exec as_agent "$@"` silently shipped and
died in-container with `exec: as_agent: not found`.
"""
import os
import subprocess
from pathlib import Path

ENTRYPOINT = Path(__file__).parent.parent / "containers" / "base" / "entrypoint.sh"


def _run(tmp_path, extra_env, *cmd):
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    runuser = stub_dir / "runuser"
    runuser.write_text('#!/bin/sh\nshift 3   # drop -u <user> --\nexec "$@"\n')
    runuser.chmod(0o755)

    env = {
        "PATH": f"{stub_dir}:{os.environ['PATH']}",
        "KARAKUM_AGENT": "agent",  # == default → skip usermod/groupmod rename
    }
    env.update(extra_env)
    return subprocess.run(
        ["sh", str(ENTRYPOINT), *cmd],
        capture_output=True, text=True, env=env,
    )


def test_entrypoint_execs_the_command(tmp_path):
    r = _run(tmp_path, {}, "sh", "-c", "echo SESSION_OK")
    assert r.returncode == 0, r.stderr
    assert "SESSION_OK" in r.stdout
    assert "not found" not in r.stderr  # the exec-a-function regression


def test_entrypoint_runs_init_hook_then_command(tmp_path):
    r = _run(tmp_path, {"KARAKUM_MEMORY_INIT": "echo HOOK_RAN"}, "sh", "-c", "echo SESSION_OK")
    assert r.returncode == 0, r.stderr
    assert "HOOK_RAN" in r.stdout
    assert "SESSION_OK" in r.stdout


def test_failing_hook_is_non_fatal(tmp_path):
    r = _run(tmp_path, {"KARAKUM_MEMORY_INIT": "false"}, "sh", "-c", "echo SESSION_OK")
    assert r.returncode == 0, r.stderr          # broken hook must not block the session
    assert "SESSION_OK" in r.stdout
    assert "memory init hook failed" in r.stderr
