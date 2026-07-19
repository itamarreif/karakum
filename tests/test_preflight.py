"""check_github_token: warn on a rejected GH_TOKEN, stay quiet otherwise.

The check is a best-effort launch-time guard — it must never raise or block a
session, only print a warning on a definitive auth rejection.
"""
import json
import urllib.error

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
