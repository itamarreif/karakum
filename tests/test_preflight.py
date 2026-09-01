"""Launch-time guards: check_github_token and check_ssh_agent.

Both are best-effort — they must never raise or block a session, only print a
warning on a definitive verdict. Offline is never a verdict.
"""
import json
import subprocess
import urllib.error
from types import SimpleNamespace

from karakum import preflight


class _Resp:
    """Minimal stand-in for the urlopen context manager json.load() reads."""

    def __init__(self, payload):
        self._data = json.dumps(payload).encode()

    def read(self, *a):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_none_or_empty_token_is_a_noop(monkeypatch, capsys):
    called = []
    monkeypatch.setattr(preflight.urllib.request, "urlopen", lambda *a, **k: called.append(1))
    preflight.check_github_token(None)
    preflight.check_github_token("")
    assert not called                       # no network call when there's no token
    assert capsys.readouterr().err == ""


def test_valid_token_reports_login(monkeypatch, capsys):
    monkeypatch.setattr(preflight.urllib.request, "urlopen",
                        lambda *a, **k: _Resp({"login": "octocat"}))
    preflight.check_github_token("tok")
    err = capsys.readouterr().err
    assert "valid" in err and "octocat" in err


def test_401_warns_but_does_not_raise(monkeypatch, capsys):
    def boom(*a, **k):
        raise urllib.error.HTTPError("https://api.github.com/user", 401, "Unauthorized", {}, None)
    monkeypatch.setattr(preflight.urllib.request, "urlopen", boom)
    preflight.check_github_token("tok")     # must not raise
    err = capsys.readouterr().err
    assert "401" in err and "WARNING" in err


def test_network_error_is_silent(monkeypatch, capsys):
    def boom(*a, **k):
        raise urllib.error.URLError("offline")
    monkeypatch.setattr(preflight.urllib.request, "urlopen", boom)
    preflight.check_github_token("tok")     # offline must not gate launch
    assert capsys.readouterr().err == ""


# --- check_ssh_agent -------------------------------------------------------
#
# In-container git authenticates through the *forwarded host agent*, so these
# host-side conditions are the only warning the user ever gets before a session
# fails with a bare `Permission denied (publickey)`.

def _ssh_env(monkeypatch, *, have_ssh=True, list_rc=0, probe=None):
    """Stub `shutil.which` + `subprocess.run` for one check_ssh_agent scenario.

    `probe` is what the `ssh -T git@github.com` call returns (a completed process,
    or an exception instance to raise); `list_rc` is `ssh-add -l`'s exit status.
    """
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "/usr/bin/ssh" if have_ssh else None)
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[0] == "ssh-add":
            return SimpleNamespace(returncode=list_rc, stdout="", stderr="")
        if isinstance(probe, Exception):
            raise probe
        return probe

    monkeypatch.setattr(preflight.subprocess, "run", fake_run)
    return calls


def _ok(stderr):
    return SimpleNamespace(returncode=1, stdout="", stderr=stderr)


def test_no_openssh_on_host_is_a_noop(monkeypatch, capsys):
    calls = _ssh_env(monkeypatch, have_ssh=False)
    preflight.check_ssh_agent()
    assert not calls                        # nothing probed
    assert capsys.readouterr().err == ""


def test_unreachable_agent_warns(monkeypatch, capsys):
    _ssh_env(monkeypatch, list_rc=2)
    preflight.check_ssh_agent()
    err = capsys.readouterr().err
    assert "WARNING" in err and "can't reach the host SSH agent" in err


def test_agent_with_no_identities_warns(monkeypatch, capsys):
    _ssh_env(monkeypatch, list_rc=1)
    preflight.check_ssh_agent()
    err = capsys.readouterr().err
    assert "WARNING" in err and "no identities" in err


def test_no_github_probe_once_the_agent_is_already_disqualified(monkeypatch):
    calls = _ssh_env(monkeypatch, list_rc=1)
    preflight.check_ssh_agent()
    assert [c[0] for c in calls] == ["ssh-add"]   # never reaches out to GitHub


def test_successful_auth_is_silent(monkeypatch, capsys):
    # `ssh -T git@github.com` exits 1 on success — the greeting is the verdict.
    _ssh_env(monkeypatch, probe=_ok("Hi octocat! You've successfully authenticated, but "
                                    "GitHub does not provide shell access."))
    preflight.check_ssh_agent()
    assert capsys.readouterr().err == ""


def test_rejected_keys_warn(monkeypatch, capsys):
    _ssh_env(monkeypatch, probe=SimpleNamespace(
        returncode=255, stdout="", stderr="git@github.com: Permission denied (publickey)."))
    preflight.check_ssh_agent()
    err = capsys.readouterr().err
    assert "WARNING" in err and "GitHub rejected them" in err


def test_timeout_points_at_the_1password_prompt(monkeypatch, capsys):
    # The failure this check exists for: 1Password prompts on the *host*, and from
    # inside a container that prompt is invisible.
    _ssh_env(monkeypatch, probe=subprocess.TimeoutExpired(cmd="ssh", timeout=20))
    preflight.check_ssh_agent()
    err = capsys.readouterr().err
    assert "WARNING" in err and "1Password" in err


def test_offline_is_silent(monkeypatch, capsys):
    _ssh_env(monkeypatch, probe=SimpleNamespace(
        returncode=255, stdout="", stderr="ssh: Could not resolve hostname github.com"))
    preflight.check_ssh_agent()
    assert capsys.readouterr().err == ""     # a network blip never gates a launch


def test_never_raises_and_never_exits(monkeypatch):
    for rc in (0, 1, 2):
        _ssh_env(monkeypatch, list_rc=rc, probe=_ok("Permission denied"))
        preflight.check_ssh_agent()          # no SystemExit, no exception
