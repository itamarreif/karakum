"""Tests for `session clean` / `session down` helpers — the pure builders in
cli.py and the docker-facing container helpers in cleanup.py."""
from types import SimpleNamespace

from karakum import cleanup, cli


def _clone(label):
    return SimpleNamespace(label=label)


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
        assert "name=agent-takwin-fix-" in cmd
        return SimpleNamespace(returncode=0, stdout="agent-takwin-fix-a1b2c3\nagent-takwin-fix-d4e5f6\n", stderr="")
    monkeypatch.setattr(cleanup.subprocess, "run", run)
    assert cleanup.running_containers("takwin", "fix") == [
        "agent-takwin-fix-a1b2c3", "agent-takwin-fix-d4e5f6",
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
