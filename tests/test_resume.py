"""Tests for `resume` and the shared `_resolve_session` slug/agent resolution.

Covers: bare-slug vs `<agent>/<slug>` disambiguation (used by resume/rm/clean/down),
resume reconstructing agent+project from the clones on disk, and the clone-label →
project-manifest-name reverse lookup.
"""
from pathlib import Path
from types import SimpleNamespace

import click
import pytest
from click.testing import CliRunner

from karakum import cli


def _sess(agent, slug, labels):
    return SimpleNamespace(
        agent=agent, slug=slug, path=Path(f"/s/{agent}/{slug}"),
        clones=[SimpleNamespace(label=l) for l in labels],
    )


def _patch_sessions(monkeypatch, sessions):
    """Fake cleanup.iter_sessions that honors the agent filter like the real one."""
    def fake_iter(agent=None):
        return [s for s in sessions if agent is None or s.agent == agent]
    monkeypatch.setattr(cli.cleanup, "iter_sessions", fake_iter)


def _capture_launch(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_do_launch", lambda *a, **k: calls.append(a))
    return calls


def _text(res):
    return res.output + (res.stderr or "")


# --- _resolve_session -------------------------------------------------------

def test_resolve_single_match(monkeypatch):
    _patch_sessions(monkeypatch, [_sess("alice", "fixes", ["scratchpad"])])
    assert cli._resolve_session("fixes").agent == "alice"


def test_resolve_ambiguous_bare_slug_errors(monkeypatch):
    _patch_sessions(monkeypatch, [_sess("alice", "fixes", ["scratchpad"]),
                                  _sess("bob", "fixes", ["scratchpad"])])
    with pytest.raises(click.ClickException) as e:
        cli._resolve_session("fixes")
    assert "multiple agents" in e.value.message


def test_resolve_qualified_agent_slug_disambiguates(monkeypatch):
    _patch_sessions(monkeypatch, [_sess("alice", "fixes", ["scratchpad"]),
                                  _sess("bob", "fixes", ["scratchpad"])])
    assert cli._resolve_session("bob/fixes").agent == "bob"


def test_resolve_no_match(monkeypatch):
    _patch_sessions(monkeypatch, [])
    with pytest.raises(click.ClickException):
        cli._resolve_session("nope")


# --- resume -----------------------------------------------------------------

def test_resume_memory_only(monkeypatch):
    _patch_sessions(monkeypatch, [_sess("alice", "notes", ["scratchpad"])])
    calls = _capture_launch(monkeypatch)
    res = CliRunner().invoke(cli.main, ["resume", "notes"])
    assert res.exit_code == 0, _text(res)
    # _do_launch(agent, project, slug)
    assert calls[0] == ("alice", "-", "notes")


def test_resume_with_project_maps_label_to_name(monkeypatch):
    _patch_sessions(monkeypatch, [_sess("alice", "fix-login", ["scratchpad", "webapp"])])
    monkeypatch.setattr(cli, "_project_for_label", lambda label: "web" if label == "webapp" else None)
    calls = _capture_launch(monkeypatch)
    res = CliRunner().invoke(cli.main, ["resume", "fix-login"])
    assert res.exit_code == 0, _text(res)
    assert calls[0] == ("alice", "web", "fix-login")


def test_resume_multiple_projects_errors(monkeypatch):
    _patch_sessions(monkeypatch, [_sess("alice", "big", ["scratchpad", "webapp", "api"])])
    calls = _capture_launch(monkeypatch)
    res = CliRunner().invoke(cli.main, ["resume", "big"])
    assert res.exit_code != 0
    assert "multiple projects" in _text(res)
    assert calls == []  # never launched


def test_resume_unmappable_label_errors(monkeypatch):
    _patch_sessions(monkeypatch, [_sess("alice", "x", ["scratchpad", "ghost"])])
    monkeypatch.setattr(cli, "_project_for_label", lambda label: None)
    calls = _capture_launch(monkeypatch)
    res = CliRunner().invoke(cli.main, ["resume", "x"])
    assert res.exit_code != 0
    assert "project manifest" in _text(res)
    assert calls == []


# --- _project_for_label -----------------------------------------------------

def test_project_for_label_maps_repo_basename(monkeypatch, tmp_path):
    projects = tmp_path / "config" / "projects"
    projects.mkdir(parents=True)
    (projects / "web.yaml").write_text("name: web\nrepository: github.com/org/webapp\n")
    monkeypatch.setenv("KARAKUM_CONFIG_DIR", str(tmp_path / "config"))
    assert cli._project_for_label("webapp") == "web"   # filename stem, not repo basename
    assert cli._project_for_label("nope") is None
