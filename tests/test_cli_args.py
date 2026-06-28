"""Tests for the docker-arg builders in cli.py — git identity/signing and
terminal forwarding. These guard regressions we've actually hit (the +agent
email subaddress order, SSH-signing gating)."""
from types import SimpleNamespace

import pytest

from karakum import cli


def _fake_git_config(values: dict):
    """Fake subprocess.run that answers `git config --global <key>` from `values`.

    A key absent from `values` returns exit code 1 (git's "unset" behavior)."""
    def run(cmd, **kw):
        assert cmd[:3] == ["git", "config", "--global"], cmd
        key = cmd[3]
        val = values.get(key)
        return SimpleNamespace(
            returncode=0 if val is not None else 1,
            stdout=(val + "\n") if val is not None else "",
            stderr="",
        )
    return run


def _env(args):
    """Turn ["-e", "K=V", "-e", "K2=V2", ...] into {"K": "V", ...}."""
    return dict(a.split("=", 1) for a in args if a != "-e")


# --- _git_identity_args ----------------------------------------------------

def test_identity_email_is_user_plus_agent_in_local_part(monkeypatch):
    monkeypatch.setattr(cli.subprocess, "run", _fake_git_config({"user.email": "itamar.reif@gmail.com"}))
    env = _env(cli._git_identity_args("takwin"))
    # +agent goes in the local part, NOT in front (which would route elsewhere)
    assert env["GIT_AUTHOR_EMAIL"] == "itamar.reif+takwin@gmail.com"
    assert env["GIT_COMMITTER_EMAIL"] == "itamar.reif+takwin@gmail.com"
    assert env["GIT_AUTHOR_NAME"] == "takwin"
    assert env["GIT_COMMITTER_NAME"] == "takwin"


def test_identity_email_without_at_falls_back(monkeypatch):
    monkeypatch.setattr(cli.subprocess, "run", _fake_git_config({"user.email": "localonly"}))
    assert _env(cli._git_identity_args("takwin"))["GIT_AUTHOR_EMAIL"] == "localonly+takwin"


def test_identity_no_email_returns_empty(monkeypatch):
    monkeypatch.setattr(cli.subprocess, "run", _fake_git_config({}))
    assert cli._git_identity_args("takwin") == []


# --- _git_signing_args -----------------------------------------------------

def test_signing_ssh_emits_config(monkeypatch):
    cfg = {"commit.gpgsign": "true", "gpg.format": "ssh", "user.signingkey": "ssh-ed25519 AAAA"}
    monkeypatch.setattr(cli.subprocess, "run", _fake_git_config(cfg))
    env = _env(cli._git_signing_args())
    assert env["GIT_CONFIG_COUNT"] == "3"
    pairs = {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"] for i in range(3)}
    assert pairs == {"commit.gpgsign": "true", "gpg.format": "ssh", "user.signingkey": "ssh-ed25519 AAAA"}
    # the host-only signer program is deliberately NOT propagated
    assert "gpg.ssh.program" not in pairs.values()


def test_signing_skipped_when_format_not_ssh(monkeypatch):
    cfg = {"commit.gpgsign": "true", "gpg.format": "openpgp", "user.signingkey": "X"}
    monkeypatch.setattr(cli.subprocess, "run", _fake_git_config(cfg))
    assert cli._git_signing_args() == []


def test_signing_skipped_when_disabled(monkeypatch):
    cfg = {"commit.gpgsign": "false", "gpg.format": "ssh", "user.signingkey": "X"}
    monkeypatch.setattr(cli.subprocess, "run", _fake_git_config(cfg))
    assert cli._git_signing_args() == []


def test_signing_skipped_when_no_key(monkeypatch):
    cfg = {"commit.gpgsign": "true", "gpg.format": "ssh"}
    monkeypatch.setattr(cli.subprocess, "run", _fake_git_config(cfg))
    assert cli._git_signing_args() == []


# --- _terminal_args --------------------------------------------------------

def test_terminal_forwards_host_term(monkeypatch):
    monkeypatch.setenv("TERM", "tmux-256color")
    env = _env(cli._terminal_args())
    assert env["TERM"] == "tmux-256color"
    assert env["COLORTERM"] == "truecolor"


def test_terminal_falls_back_when_term_unset(monkeypatch):
    monkeypatch.delenv("TERM", raising=False)
    env = _env(cli._terminal_args())
    assert env["TERM"] == "xterm-256color"
    assert env["COLORTERM"] == "truecolor"
