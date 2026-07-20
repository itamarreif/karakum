"""End-to-end (offline) check of the per-role session branch naming.

Drives the real `karakum launch` code path against throwaway git repos, stubbing
only the externals (docker exec, manifests, preflight, secrets), and asserts the
branch each clone actually checks out:

  - project clone  → <agent>/<slug>
  - memory clone   → <project>/<slug>   (or a bare <slug> when there's no project)

This is the behavior the launcher is responsible for; `session.ensure` does the
real clone + checkout, so the assertions read live git state, not a mock.
"""
import subprocess
from pathlib import Path

from click.testing import CliRunner

from karakum import cli


class _Exec(Exception):
    """Raised in place of os.execvpe so launch stops before docker."""


def _run(*args):
    subprocess.run(args, check=True, capture_output=True)


def _mkrepo(path):
    """A throwaway git repo with one commit and an `origin` remote."""
    path.mkdir(parents=True)
    p = str(path)
    _run("git", "init", "-q", "-b", "main", p)
    (path / "README").write_text("x")
    _run("git", "-C", p, "add", "-A")
    _run("git", "-C", p, "-c", "user.email=a@b.c", "-c", "user.name=t",
         "-c", "commit.gpgsign=false", "commit", "-qm", "init")
    _run("git", "-C", p, "remote", "add", "origin", "https://example.com/x.git")


def _branch(path):
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True,
    ).stdout.strip()


def _wire(monkeypatch, tmp_path, mem_repo, proj_repo):
    """Stub every external so launch runs offline against the throwaway repos."""
    monkeypatch.setenv("KARAKUM_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KARAKUM_CONFIG_DIR", str(tmp_path / "config"))  # no config.yaml → defaults

    monkeypatch.setattr(cli.manifest, "agent_path", lambda a: f"AGENT:{a}")
    monkeypatch.setattr(cli.manifest, "project_path", lambda p: f"PROJECT:{p}")

    def load(path):
        if str(path).startswith("AGENT:"):
            return {"memory": {"path": str(mem_repo), "repository": "https://example.com/mem.git"}}
        if str(path).startswith("PROJECT:"):
            return {"path": str(proj_repo), "repository": "https://example.com/proj.git"}
        raise AssertionError(path)

    monkeypatch.setattr(cli.manifest, "load", load)
    monkeypatch.setattr(cli.manifest, "karakum_root", lambda: tmp_path)
    monkeypatch.setattr(cli.preflight, "check_tools", lambda: None)
    monkeypatch.setattr(cli.preflight, "check_repo", lambda *a, **k: None)
    monkeypatch.setattr(cli.ksecrets, "load", lambda: ({}, []))
    monkeypatch.setattr(cli.os, "chdir", lambda p: None)
    monkeypatch.setattr(cli.os, "execvpe", lambda *a, **k: (_ for _ in ()).throw(_Exec()))


def _clones(tmp_path, agent, slug):
    """Map label -> branch for every clone the session created."""
    session = tmp_path / "data" / "sessions" / agent / slug
    return {d.name: _branch(d) for d in session.iterdir() if d.is_dir()}


def test_project_and_memory_get_distinct_namespaces(monkeypatch, tmp_path):
    mem, proj = tmp_path / "src_mem", tmp_path / "src_proj"
    _mkrepo(mem)
    _mkrepo(proj)
    _wire(monkeypatch, tmp_path, mem, proj)

    res = CliRunner().invoke(cli.main, ["launch", "alice", "webapp", "fix-login"])
    assert isinstance(res.exception, _Exec), res.output  # reached the docker handoff

    clones = _clones(tmp_path, "alice", "fix-login")
    # memory clone (label "scratchpad") is namespaced by PROJECT; project clone by AGENT.
    assert clones["scratchpad"] == "webapp/fix-login"
    assert clones["proj.git"] == "alice/fix-login"


def _capture_exec(monkeypatch):
    """Record the docker argv/env passed to execvpe instead of running it."""
    captured = {}

    def fake_exec(file, argv, env):
        captured["argv"], captured["env"] = argv, env
        raise _Exec()

    monkeypatch.setattr(cli.os, "execvpe", fake_exec)
    return captured


def test_mount_at_agent_home_plus_service_and_init_hook(monkeypatch, tmp_path):
    mem, proj = tmp_path / "src_mem", tmp_path / "src_proj"
    _mkrepo(mem)
    _mkrepo(proj)
    _wire(monkeypatch, tmp_path, mem, proj)

    hook = 'ln -sfn "$KARAKUM_MEMORY/MASTER_PROMPT.md" "$HOME/.claude/CLAUDE.md"'

    def load(path):
        if str(path).startswith("AGENT:"):
            return {"memory": {"path": str(mem), "repository": "https://example.com/mem.git",
                               "init": hook}}
        if str(path).startswith("PROJECT:"):
            return {"path": str(proj), "repository": "https://example.com/proj.git"}
        raise AssertionError(path)

    monkeypatch.setattr(cli.manifest, "load", load)
    captured = _capture_exec(monkeypatch)

    res = CliRunner().invoke(cli.main, ["launch", "alice", "webapp", "fix-login"])
    assert isinstance(res.exception, _Exec), res.output

    argv = captured["argv"]
    # The vault mounts at ~/<agent>, not ~/scratchpad (no doubled scratchpad/).
    assert "KARAKUM_MEMORY=/home/agent/alice" in argv
    assert captured["env"]["MEMORY_MOUNT"] == "/home/agent/alice"
    # One image with every CLI: the compose service is the neutral `agent`.
    assert "agent" in argv
    # The init hook is passed through verbatim.
    assert f"KARAKUM_MEMORY_INIT={hook}" in argv


def test_no_init_env_when_hook_unset(monkeypatch, tmp_path):
    mem, proj = tmp_path / "src_mem", tmp_path / "src_proj"
    _mkrepo(mem)
    _mkrepo(proj)
    _wire(monkeypatch, tmp_path, mem, proj)
    captured = _capture_exec(monkeypatch)

    res = CliRunner().invoke(cli.main, ["launch", "alice", "-", "notes"])
    assert isinstance(res.exception, _Exec), res.output
    assert not any(str(a).startswith("KARAKUM_MEMORY_INIT=") for a in captured["argv"])


def test_memory_only_session_uses_bare_slug(monkeypatch, tmp_path):
    mem, proj = tmp_path / "src_mem", tmp_path / "src_proj"
    _mkrepo(mem)
    _mkrepo(proj)
    _wire(monkeypatch, tmp_path, mem, proj)

    res = CliRunner().invoke(cli.main, ["launch", "alice", "-", "fix-login"])
    assert isinstance(res.exception, _Exec), res.output

    clones = _clones(tmp_path, "alice", "fix-login")
    assert clones == {"scratchpad": "fix-login"}  # no project clone; memory on a bare slug


def test_per_cli_state_dirs_created_and_opencode_seeded(monkeypatch, tmp_path):
    """Each agent CLI gets its own persistent host state dir + env var, and
    opencode's config is seeded once so it skips the first-run model picker."""
    import json as _json

    mem, proj = tmp_path / "src_mem", tmp_path / "src_proj"
    _mkrepo(mem)
    _mkrepo(proj)
    _wire(monkeypatch, tmp_path, mem, proj)
    captured = _capture_exec(monkeypatch)

    res = CliRunner().invoke(cli.main, ["launch", "alice", "-", "notes"])
    assert isinstance(res.exception, _Exec), res.output

    state = tmp_path / "data" / "state"
    env = captured["env"]
    # claude keeps the bare <state_root>/<agent> path; the others hang off it.
    assert env["CLAUDE_STATE_DIR"] == str(state / "alice")
    assert env["OPENCODE_CONFIG_DIR"] == str(state / "alice-opencode")
    assert env["OPENCODE_DATA_DIR"] == str(state / "alice-opencode-data")
    assert env["CODEX_STATE_DIR"] == str(state / "alice-codex")
    for var in ("CLAUDE_STATE_DIR", "OPENCODE_CONFIG_DIR", "OPENCODE_DATA_DIR", "CODEX_STATE_DIR"):
        assert Path(env[var]).is_dir(), f"{var} dir not created"

    seed = _json.loads((state / "alice-opencode" / "opencode.json").read_text())
    assert seed["model"] == "anthropic/claude-sonnet-4-5"
    assert seed["autoupdate"] is False


def test_opencode_seed_not_clobbered_when_present(monkeypatch, tmp_path):
    """A pre-existing opencode.json (user's own model switch) is left untouched."""
    import json as _json

    mem, proj = tmp_path / "src_mem", tmp_path / "src_proj"
    _mkrepo(mem)
    _mkrepo(proj)
    _wire(monkeypatch, tmp_path, mem, proj)
    _capture_exec(monkeypatch)

    oc = tmp_path / "data" / "state" / "alice-opencode"
    oc.mkdir(parents=True)
    (oc / "opencode.json").write_text(_json.dumps({"model": "openai/gpt-5"}))

    res = CliRunner().invoke(cli.main, ["launch", "alice", "-", "notes"])
    assert isinstance(res.exception, _Exec), res.output
    assert _json.loads((oc / "opencode.json").read_text()) == {"model": "openai/gpt-5"}
