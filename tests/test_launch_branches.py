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
    opencode's config is seeded once so it skips the first-run model picker.
    pi is deliberately NOT seeded (the user picks a model; pi persists it)."""
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
    assert env["PI_STATE_DIR"] == str(state / "alice-pi")
    for var in ("CLAUDE_STATE_DIR", "OPENCODE_CONFIG_DIR", "OPENCODE_DATA_DIR", "CODEX_STATE_DIR", "PI_STATE_DIR"):
        assert Path(env[var]).is_dir(), f"{var} dir not created"

    seed = _json.loads((state / "alice-opencode" / "opencode.json").read_text())
    assert seed["model"] == "anthropic/claude-sonnet-4-5"
    assert seed["autoupdate"] is False

    # pi's agent dir is created (so a memory.init hook can link AGENTS.md into it,
    # like the other CLIs' instruction dirs) but NOT seeded — no settings.json.
    assert (state / "alice-pi" / "agent").is_dir()
    assert not (state / "alice-pi" / "agent" / "settings.json").exists()


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


# --- a project with several repos -------------------------------------------

def _wire_project(monkeypatch, tmp_path, mem_repo, repos: list):
    """Like `_wire`, but the single project manifest declares several repos.

    `repos` is a list of (repo_path, repository_url); each gets its own clone
    label (the repository's basename)."""
    monkeypatch.setenv("KARAKUM_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KARAKUM_CONFIG_DIR", str(tmp_path / "config"))

    monkeypatch.setattr(cli.manifest, "agent_path", lambda a: f"AGENT:{a}")
    monkeypatch.setattr(cli.manifest, "project_path", lambda p: f"PROJECT:{p}")

    def load(path):
        path = str(path)
        if path.startswith("AGENT:"):
            return {"memory": {"path": str(mem_repo), "repository": "https://example.com/mem.git"}}
        if path.startswith("PROJECT:"):
            return {"name": "platform",
                    "repos": [{"path": str(rp), "repository": url} for rp, url in repos]}
        raise AssertionError(path)

    monkeypatch.setattr(cli.manifest, "load", load)
    monkeypatch.setattr(cli.manifest, "karakum_root", lambda: tmp_path)
    monkeypatch.setattr(cli.preflight, "check_tools", lambda: None)
    monkeypatch.setattr(cli.preflight, "check_repo", lambda *a, **k: None)
    monkeypatch.setattr(cli.ksecrets, "load", lambda: ({}, []))
    monkeypatch.setattr(cli.os, "chdir", lambda p: None)


def _platform(monkeypatch, tmp_path, urls=("dewey", "mundaneum")):
    mem = tmp_path / "src_mem"
    _mkrepo(mem)
    repos = []
    for i, n in enumerate(urls):
        r = tmp_path / f"src_{i}"
        _mkrepo(r)
        repos.append((r, f"https://example.com/{n}.git"))
    _wire_project(monkeypatch, tmp_path, mem, repos)


def _out(res):
    """CliRunner captures stdout/stderr itself, so console output lands here."""
    return res.output + (res.stderr or "")


def test_every_repo_of_a_project_gets_a_clone_on_the_agent_branch(monkeypatch, tmp_path):
    """One project, several repos: all clones share `<agent>/<slug>`, and the
    memory branch stays `<project>/<slug>` — one project per session."""
    _platform(monkeypatch, tmp_path)
    monkeypatch.setattr(cli.os, "execvpe", lambda *a, **k: (_ for _ in ()).throw(_Exec()))

    res = CliRunner().invoke(cli.main, ["launch", "alice", "platform", "init"])
    assert isinstance(res.exception, _Exec), res.output

    assert _clones(tmp_path, "alice", "init") == {
        "scratchpad": "platform/init",
        "dewey.git": "alice/init",
        "mundaneum.git": "alice/init",
    }


def test_project_env_is_singular_first_repo_and_plural_all(monkeypatch, tmp_path):
    """KARAKUM_PROJECT keeps pointing at one path so existing docs/skills hold;
    KARAKUM_PROJECTS carries every repo, colon-separated."""
    _platform(monkeypatch, tmp_path)
    captured = _capture_exec(monkeypatch)

    res = CliRunner().invoke(cli.main, ["launch", "alice", "platform", "init"])
    assert isinstance(res.exception, _Exec), res.output

    argv = captured["argv"]
    assert "KARAKUM_PROJECT=/home/agent/dewey.git" in argv
    assert "KARAKUM_PROJECTS=/home/agent/dewey.git:/home/agent/mundaneum.git" in argv
    mounts = [a for a in argv if str(a).endswith(":rw")]
    assert sum("dewey.git" in m for m in mounts) == 1
    assert sum("mundaneum.git" in m for m in mounts) == 1


def test_one_repo_project_behaves_exactly_as_before(monkeypatch, tmp_path):
    """The shorthand manifest is unchanged, and so is everything it produces."""
    mem, proj = tmp_path / "src_mem", tmp_path / "src_proj"
    _mkrepo(mem)
    _mkrepo(proj)
    _wire(monkeypatch, tmp_path, mem, proj)      # top-level path/repository form
    captured = _capture_exec(monkeypatch)

    res = CliRunner().invoke(cli.main, ["launch", "alice", "webapp", "fix-login"])
    assert isinstance(res.exception, _Exec), res.output

    assert _clones(tmp_path, "alice", "fix-login") == {
        "scratchpad": "webapp/fix-login", "proj.git": "alice/fix-login"}
    assert "KARAKUM_PROJECT=/home/agent/proj.git" in captured["argv"]
    assert "KARAKUM_PROJECTS=/home/agent/proj.git" in captured["argv"]


def test_repos_sharing_a_basename_refuse_to_launch(monkeypatch, tmp_path):
    """Two repos with the same basename would mount on one path and share a clone."""
    _platform(monkeypatch, tmp_path, urls=("tools", "tools"))
    monkeypatch.setattr(cli.os, "execvpe", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("must not reach docker")))

    res = CliRunner().invoke(cli.main, ["launch", "alice", "platform", "init"])
    assert res.exit_code == 2, res.output
    assert not (tmp_path / "data" / "sessions" / "alice" / "init").exists()


def test_repo_without_a_path_is_rejected(monkeypatch, tmp_path):
    """project_repos normalizes shape only; the launcher is what requires a path."""
    mem = tmp_path / "src_mem"
    _mkrepo(mem)
    _wire_project(monkeypatch, tmp_path, mem, [])
    monkeypatch.setattr(cli.manifest, "load", lambda path: (
        {"memory": {"path": str(mem), "repository": "https://example.com/mem.git"}}
        if str(path).startswith("AGENT:") else
        {"name": "platform", "repos": [{"repository": "https://example.com/a.git"}]}))

    res = CliRunner().invoke(cli.main, ["launch", "alice", "platform", "init"])
    assert res.exit_code == 2, res.output


def test_reuse_reports_the_branch_the_clone_is_actually_on(monkeypatch, tmp_path):
    """Reuse never switches branches, so the log must say what is checked out."""
    _platform(monkeypatch, tmp_path)
    monkeypatch.setattr(cli.os, "execvpe", lambda *a, **k: (_ for _ in ()).throw(_Exec()))

    CliRunner().invoke(cli.main, ["launch", "alice", "platform", "init"])
    # force a divergence the way a renamed project would
    subprocess.run(["git", "-C", str(tmp_path / "data" / "sessions" / "alice" / "init" / "scratchpad"),
                    "checkout", "-q", "-b", "other/init"], check=True)

    res = CliRunner().invoke(cli.main, ["launch", "alice", "platform", "init"])
    assert isinstance(res.exception, _Exec), res.output
    text = _out(res)
    assert _clones(tmp_path, "alice", "init")["scratchpad"] == "other/init"
    assert "other/init" in text
    assert "NOT platform/init" in text


def test_reuse_on_the_same_branch_stays_quiet(monkeypatch, tmp_path):
    """The normal resume path must not warn."""
    _platform(monkeypatch, tmp_path)
    monkeypatch.setattr(cli.os, "execvpe", lambda *a, **k: (_ for _ in ()).throw(_Exec()))

    CliRunner().invoke(cli.main, ["launch", "alice", "platform", "init"])
    res = CliRunner().invoke(cli.main, ["launch", "alice", "platform", "init"])
    assert isinstance(res.exception, _Exec), res.output
    assert "NOT" not in _out(res)
    assert "reusing agent session" in _out(res)
