"""Tests for `karakum serve` — the Open WebUI service harness.

Guards the properties that are easy to break silently: which compose overlays a
given flag combination selects, that no port is published unless asked, and that
host-wide secrets stay out of a network-reachable container.
"""
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from karakum import cli


@pytest.fixture
def harness(monkeypatch, tmp_path):
    """Run `serve` with docker + the filesystem stubbed; capture the compose call."""
    calls = []

    monkeypatch.setattr(cli.preflight, "check_tools", lambda: None)
    monkeypatch.setattr(cli.preflight, "check_repo", lambda *a, **kw: None)
    monkeypatch.setattr(cli.manifest, "load", lambda p: {"name": "alice", "memory": {}})
    monkeypatch.setattr(cli.manifest, "agent_path", lambda a: tmp_path / f"{a}.yaml")
    monkeypatch.setattr(cli.manifest, "karakum_root", lambda: tmp_path)
    monkeypatch.setattr(cli.manifest, "expand_path", lambda p: tmp_path / "vault")
    monkeypatch.setattr(cli.manifest, "get", lambda d, k: "x")
    monkeypatch.setattr(cli.config, "state_root", lambda: tmp_path / "state")
    monkeypatch.setattr(cli.ksecrets, "load",
                        lambda: ({"GH_TOKEN": "secret", "BEDROCK_API_KEY": "bk",
                                  "BEDROCK_API_BASE_URL": "https://example.invalid/openai/v1",
                                  "WEBUI_SECRET_KEY": "s"}, []))

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    return calls


def _compose_files(cmd):
    return [cmd[i + 1] for i, a in enumerate(cmd) if a == "-f"]


def test_default_publishes_no_port(harness):
    """AGENTS.md: no service publishes ports to the host. The localhost overlay
    is opt-in, so a bare `serve` must not chain it."""
    assert CliRunner().invoke(cli.serve, ["alice"]).exit_code == 0
    files = _compose_files(harness[-1][0])
    assert cli.COMPOSE_LOCALHOST not in files
    assert files == [cli.COMPOSE_BASE, cli.COMPOSE_OPENWEBUI]


def test_publish_chains_localhost_overlay(harness):
    assert CliRunner().invoke(cli.serve, ["alice", "--publish"]).exit_code == 0
    assert cli.COMPOSE_LOCALHOST in _compose_files(harness[-1][0])


def test_mock_chains_mock_overlay_last(harness):
    """Order matters: the mock overlay overrides the model backend, so it has to
    come after the base service definition."""
    assert CliRunner().invoke(cli.serve, ["alice", "--mock"]).exit_code == 0
    files = _compose_files(harness[-1][0])
    assert files[-1] == cli.COMPOSE_MOCK


def test_secrets_are_not_injected_into_the_container(harness):
    """`launch` passes `-e VAR` per secret; `serve` must not. Secrets reach the
    compose *process* for ${VAR} substitution, and only what the compose
    `environment:` block names reaches a container that listens on a port."""
    assert CliRunner().invoke(cli.serve, ["alice"]).exit_code == 0
    cmd, kw = harness[-1]
    assert "-e" not in cmd
    assert kw["env"]["GH_TOKEN"] == "secret"          # available for substitution
    assert kw["env"]["OPENWEBUI_API_KEY"] == "bk"     # mapped to the compose var


def test_mock_does_not_read_bedrock_credentials(harness):
    """--mock exists to run with no AWS at all; it must not depend on the key."""
    assert CliRunner().invoke(cli.serve, ["alice", "--mock"]).exit_code == 0
    assert "OPENWEBUI_API_KEY" not in harness[-1][1]["env"]


def test_down_stops_instead_of_starting(harness):
    assert CliRunner().invoke(cli.serve, ["alice", "--down"]).exit_code == 0
    assert harness[-1][0][-1] == "down"


def test_state_dir_is_suffixed_and_created(harness, tmp_path):
    """Must not nest inside the claude mount (<state_root>/<agent>)."""
    assert CliRunner().invoke(cli.serve, ["alice"]).exit_code == 0
    state = tmp_path / "state" / "alice-openwebui"
    assert state.is_dir()
    assert harness[-1][1]["env"]["OPENWEBUI_STATE_DIR"] == str(state)
