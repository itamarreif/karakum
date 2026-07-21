"""Tests for `session clean` / `session down` — the pure builders and docker
helpers, the command wiring (via CliRunner), and the generated bash script's
real behavior (run through bash)."""
import subprocess
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner

from karakum import cleanup, cli


def _clone(label):
    return SimpleNamespace(label=label)


def _session(labels):
    """A fake Session with the given clone labels (paths/branches don't matter)."""
    clones = [cleanup.Clone(label=l, path=Path("/tmp") / l, branch="alice/demo") for l in labels]
    return cleanup.Session(agent="alice", slug="demo",
                           path=Path("/tmp/sessions/alice/demo"), clones=clones)


def _text(result):
    """stdout + stderr combined (click 8.4 captures them separately)."""
    return result.output + (result.stderr or "")


# A minimal toolchains.yaml dict for command tests.
_TC = {"rust": {"detect": "test -f Cargo.toml", "clean": "cargo clean"}}


# --- _clean_builtins -------------------------------------------------------

def test_clean_builtins_collects_detect_and_clean():
    tc = {
        "node": {"detect": "test -f package.json", "clean": "npm run clean --if-present"},
        "rust": {"detect": "test -f Cargo.toml", "clean": "cargo clean"},
    }
    assert cli._clean_builtins(tc) == [
        ("test -f package.json", "npm run clean --if-present"),
        ("test -f Cargo.toml", "cargo clean"),
    ]


def test_clean_builtins_skips_entries_without_clean_and_non_dicts():
    tc = {
        "node": {"detect": "test -f package.json", "clean": "rm -rf node_modules"},
        "python": {"version": "3.13"},   # no clean → skipped
        "name": "karakum",               # non-dict top-level → skipped
    }
    assert cli._clean_builtins(tc) == [("test -f package.json", "rm -rf node_modules")]


def test_clean_builtins_detect_defaults_to_true():
    assert cli._clean_builtins({"x": {"clean": "do-it"}}) == [("true", "do-it")]


# --- _clean_map_from_projects ----------------------------------------------

def test_clean_map_keys_by_repo_basename_and_normalizes():
    projects = [
        {"name": "kuma", "repository": "github.com/veblen-group/kuma",
         "clean": ["cargo clean", "cd webapp && npm run clean"]},
        {"name": "solo", "repository": "github.com/you/solo", "clean": "make clean"},
        {"name": "noclean", "repository": "github.com/you/noclean"},  # omitted
    ]
    assert cli._clean_map_from_projects(projects) == {
        "kuma": ["cargo clean", "cd webapp && npm run clean"],
        "solo": ["make clean"],
    }


def test_clean_map_falls_back_to_name_without_repository():
    assert cli._clean_map_from_projects([{"name": "solo", "clean": "x"}]) == {"solo": ["x"]}


# --- _clean_script ---------------------------------------------------------

def test_clean_script_custom_overrides_builtins():
    builtins = [("test -f Cargo.toml", "cargo clean")]
    custom = {"kuma": ["cargo clean", "cd webapp && npm run clean"]}
    script = cli._clean_script([_clone("kuma")], builtins, custom)
    # custom commands run; the builtin detect/clean is NOT emitted for this clone
    assert "cd webapp && npm run clean" in script
    assert "if test -f Cargo.toml" not in script


def test_clean_script_autodetect_when_no_custom():
    builtins = [("test -f Cargo.toml", "cargo clean")]
    script = cli._clean_script([_clone("scratchpad")], builtins, {})
    assert "if test -f Cargo.toml; then ( cargo clean ); fi" in script
    assert script.startswith("set +e")


def test_clean_script_quotes_labels_and_is_tolerant():
    script = cli._clean_script([_clone("weird name")], [("true", "x")], {})
    assert "set +e" in script
    assert "'/work/weird name'" in script  # shlex-quoted path


# --- cleanup.running_containers / stop_containers ---------------------------

def test_running_containers_parses_names(monkeypatch):
    def run(cmd, **kw):
        assert cmd[:3] == ["docker", "ps", "--filter"]
        assert "name=agent-alice-fix-" in cmd
        return SimpleNamespace(returncode=0, stdout="agent-alice-fix-a1b2c3\nagent-alice-fix-d4e5f6\n", stderr="")
    monkeypatch.setattr(cleanup.subprocess, "run", run)
    assert cleanup.running_containers("alice", "fix") == [
        "agent-alice-fix-a1b2c3", "agent-alice-fix-d4e5f6",
    ]


def test_stop_containers_calls_docker_stop(monkeypatch):
    calls = []
    monkeypatch.setattr(cleanup.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or SimpleNamespace(returncode=0, stdout="", stderr=""))
    cleanup.stop_containers(["a", "b"])
    assert calls == [["docker", "stop", "a", "b"]]


def test_stop_containers_noop_on_empty(monkeypatch):
    calls = []
    monkeypatch.setattr(cleanup.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    cleanup.stop_containers([])
    assert calls == []


# --- session clean command (CliRunner, no docker) --------------------------

def _stub_clean(monkeypatch, *, sessions, toolchains, projects):
    monkeypatch.setattr(cli.cleanup, "iter_sessions", lambda agent=None:sessions)
    monkeypatch.setattr(cli.manifest, "toolchains_path", lambda: Path("toolchains.yaml"))
    monkeypatch.setattr(cli.manifest, "load", lambda p: toolchains)
    monkeypatch.setattr(cli, "_project_clean_map", lambda: projects)


def test_session_clean_dry_run_emits_docker_cmd(monkeypatch):
    _stub_clean(monkeypatch, sessions=[_session(["scratchpad"])], toolchains=_TC, projects={})
    result = CliRunner().invoke(cli.main, ["session", "clean", "demo", "--dry-run"])
    assert result.exit_code == 0, _text(result)
    assert "docker run --rm" in result.output
    assert "if test -f Cargo.toml; then ( cargo clean ); fi" in result.output


def test_session_clean_errors_when_image_missing(monkeypatch):
    _stub_clean(monkeypatch, sessions=[_session(["scratchpad"])], toolchains=_TC, projects={})
    ran = []

    def fake_run(cmd, **kw):
        ran.append(cmd)
        if cmd[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        raise AssertionError(f"unexpected run: {cmd}")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    result = CliRunner().invoke(cli.main, ["session", "clean", "demo"])
    assert result.exit_code != 0
    assert "karakum build" in _text(result)
    assert all(c[:2] != ["docker", "run"] for c in ran)  # never attempted the run


def test_session_clean_no_commands_configured(monkeypatch):
    _stub_clean(monkeypatch, sessions=[_session(["scratchpad"])], toolchains={}, projects={})

    def boom(*a, **k):
        raise AssertionError("docker should not be invoked")

    monkeypatch.setattr(cli.subprocess, "run", boom)
    result = CliRunner().invoke(cli.main, ["session", "clean", "demo"])
    assert result.exit_code == 0, _text(result)
    assert "no clean commands configured" in _text(result)


def test_session_clean_unknown_slug_errors(monkeypatch):
    _stub_clean(monkeypatch, sessions=[], toolchains=_TC, projects={})
    result = CliRunner().invoke(cli.main, ["session", "clean", "nope"])
    assert result.exit_code != 0
    assert "no session with slug" in _text(result)


# --- session down command (CliRunner, no docker) ---------------------------

def test_session_down_yes_stops_containers(monkeypatch):
    monkeypatch.setattr(cli.cleanup, "iter_sessions", lambda agent=None:[_session(["scratchpad"])])
    monkeypatch.setattr(cli.cleanup, "running_containers",
                        lambda a, s: ["agent-alice-demo-aaa", "agent-alice-demo-bbb"])
    stopped = []
    monkeypatch.setattr(cli.cleanup, "stop_containers", lambda names: stopped.append(names))
    result = CliRunner().invoke(cli.main, ["session", "down", "demo", "--yes"])
    assert result.exit_code == 0, _text(result)
    assert stopped == [["agent-alice-demo-aaa", "agent-alice-demo-bbb"]]
    # Success confirmations now go to stderr (console.done) — check combined text.
    assert "stopped 2 container(s)" in _text(result)


def test_session_down_abort_does_not_stop(monkeypatch):
    monkeypatch.setattr(cli.cleanup, "iter_sessions", lambda agent=None:[_session(["scratchpad"])])
    monkeypatch.setattr(cli.cleanup, "running_containers", lambda a, s: ["agent-alice-demo-aaa"])
    stopped = []
    monkeypatch.setattr(cli.cleanup, "stop_containers", lambda names: stopped.append(names))
    result = CliRunner().invoke(cli.main, ["session", "down", "demo"], input="n\n")
    assert stopped == []  # declined at the confirm prompt


def test_session_down_no_running_containers(monkeypatch):
    monkeypatch.setattr(cli.cleanup, "iter_sessions", lambda agent=None:[_session(["scratchpad"])])
    monkeypatch.setattr(cli.cleanup, "running_containers", lambda a, s: [])
    called = []
    monkeypatch.setattr(cli.cleanup, "stop_containers", lambda names: called.append(names))
    result = CliRunner().invoke(cli.main, ["session", "down", "demo"])
    assert result.exit_code == 0, _text(result)
    assert "no running containers" in _text(result)
    assert called == []


# --- generated script run through real bash --------------------------------

def _run_script(script, tmp_path):
    """Run a clean script with /work rebound to tmp_path."""
    subprocess.run(["bash", "-c", script.replace("/work/", f"{tmp_path}/")], check=True)


def test_clean_script_detect_guards_execution(tmp_path):
    (tmp_path / "hit").mkdir()
    (tmp_path / "hit" / "Cargo.toml").write_text("")
    (tmp_path / "miss").mkdir()
    builtins = [
        ("test -f Cargo.toml", "touch CLEANED"),  # detect passes only in hit/
        ("false", "touch SHOULD_NOT_RUN"),        # detect always fails → skipped
    ]
    _run_script(cli._clean_script([_clone("hit"), _clone("miss")], builtins, {}), tmp_path)
    assert (tmp_path / "hit" / "CLEANED").exists()
    assert not (tmp_path / "hit" / "SHOULD_NOT_RUN").exists()
    assert not (tmp_path / "miss" / "CLEANED").exists()  # no Cargo.toml in miss/


def test_clean_script_one_failure_does_not_abort_rest(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    custom = {"a": ["exit 7"], "b": ["touch DONE"]}
    _run_script(cli._clean_script([_clone("a"), _clone("b")], [], custom), tmp_path)
    assert (tmp_path / "b" / "DONE").exists()  # a's failure didn't stop b (set +e + subshell)


def test_clean_script_is_valid_bash_with_tricky_labels_and_cmds():
    clones = [_clone("weird name"), _clone("ok")]
    custom = {"weird name": ["cd webapp && npm run clean --if-present && rm -rf node_modules .next"]}
    builtins = [("test -f Cargo.toml", "cargo clean")]
    script = cli._clean_script(clones, builtins, custom)
    r = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True)
    assert r.returncode == 0, r.stderr


# --- shipped toolchains.yaml guard -----------------------------------------

def test_shipped_toolchains_clean_entries_have_detect():
    """A toolchain that defines `clean` must guard it with `detect`.

    Not every toolchain is cleanable — a version-only toolchain (e.g. `proto`,
    which just stages protoc/buf binaries) legitimately defines neither, and
    `_clean_builtins` skips entries without `clean`. But a `clean` without a
    `detect` would run unconditionally on every clone, so that pairing is required.
    """
    from karakum import manifest
    tc = manifest.load(manifest.karakum_root() / "examples" / "toolchains.yaml")
    assert tc, "toolchains.yaml is empty"
    for name, spec in tc.items():
        assert isinstance(spec, dict), f"{name} is not a mapping"
        if spec.get("clean"):
            assert spec.get("detect"), f"{name} defines clean but no detect guard"


# --- _project_clean_map reading real files ---------------------------------

def test_project_clean_map_reads_yaml_files(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    projects.mkdir()
    (projects / "kuma.yaml").write_text(
        "name: kuma\nrepository: github.com/v/kuma\nclean:\n  - cargo clean\n  - cd webapp && npm run clean\n"
    )
    (projects / "solo.yaml").write_text("name: solo\nrepository: github.com/v/solo\nclean: make clean\n")
    (projects / "plain.yaml").write_text("name: plain\nrepository: github.com/v/plain\n")  # no clean
    monkeypatch.setattr(cli.manifest, "config_dir", lambda: tmp_path)
    assert cli._project_clean_map() == {
        "kuma": ["cargo clean", "cd webapp && npm run clean"],
        "solo": ["make clean"],
    }


def test_project_clean_map_empty_without_projects_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.manifest, "config_dir", lambda: tmp_path / "nonexistent")
    assert cli._project_clean_map() == {}
