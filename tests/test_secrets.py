"""Tests for secret resolution — especially that all op:// refs resolve in a
SINGLE `op` process (one macOS auth prompt), not one per secret."""
import re
from types import SimpleNamespace

import pytest

from karakum import secrets as ksecrets


def _fake_op_inject(template: str, vault: dict) -> str:
    """Mimic `op inject`: replace each `{{ op://… }}` token with vault[ref].

    A missing ref raises KeyError, which the fake `run` turns into a non-zero
    exit (as the real `op inject` would)."""
    return re.sub(
        r"\{\{\s*(op://[^}]*?)\s*\}\}",
        lambda m: vault[m.group(1).strip()],
        template,
    )


@pytest.fixture
def op_env(monkeypatch):
    """Make `op` look installed and route `op inject` through an in-memory vault.

    Yields (vault, calls): mutate `vault` to set secret values; `calls` records
    every subprocess argv so a test can assert how many `op` processes ran."""
    vault: dict = {}
    calls: list = []

    monkeypatch.setattr(ksecrets.shutil, "which", lambda name: "/usr/bin/op" if name == "op" else None)

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[:2] == ["op", "inject"]:
            try:
                out = _fake_op_inject(kw["input"], vault)
            except KeyError:
                return SimpleNamespace(returncode=1, stdout="", stderr="item not found")
            return SimpleNamespace(returncode=0, stdout=out, stderr="")
        raise AssertionError(f"unexpected subprocess call: {cmd}")

    monkeypatch.setattr(ksecrets.subprocess, "run", fake_run)
    return vault, calls


def _op_calls(calls):
    return [c for c in calls if c[:2] == ["op", "inject"]]


# --- _resolve_op -----------------------------------------------------------

def test_resolve_op_batches_into_one_call(op_env):
    vault, calls = op_env
    vault.update({"op://V/a/t": "AAA", "op://V/b/t": "BBB", "op://V/c/t": "CCC"})
    out = ksecrets._resolve_op({"A": "op://V/a/t", "B": "op://V/b/t", "C": "op://V/c/t"})
    assert out == {"A": "AAA", "B": "BBB", "C": "CCC"}
    assert len(_op_calls(calls)) == 1  # the whole point: one process for N secrets


def test_resolve_op_handles_multiline_colons_and_braces(op_env):
    vault, _ = op_env
    vault.update({
        "op://V/key/priv": "-----BEGIN-----\nl2\nl3\n",  # multiline + trailing newline
        "op://V/w/v": "a:b {{ x }} c",                   # boundary char + literal braces
    })
    out = ksecrets._resolve_op({"K": "op://V/key/priv", "W": "op://V/w/v"})
    assert out["K"] == "-----BEGIN-----\nl2\nl3"  # trailing newline stripped, like `op read`
    assert out["W"] == "a:b {{ x }} c"


def test_resolve_op_missing_binary_exits(monkeypatch):
    monkeypatch.setattr(ksecrets.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit):
        ksecrets._resolve_op({"A": "op://V/a/t"})


def test_resolve_op_inject_failure_exits(op_env):
    _vault, _calls = op_env  # vault empty → fake returns non-zero
    with pytest.raises(SystemExit):
        ksecrets._resolve_op({"A": "op://V/missing/t"})


# --- load() ----------------------------------------------------------------

def _write_secrets(tmp_path, monkeypatch, mapping):
    body = "secrets:\n" + "".join(f"  {k}: {v}\n" for k, v in mapping.items())
    (tmp_path / "secrets.yaml").write_text(body)
    monkeypatch.setattr(ksecrets, "config_dir", lambda: tmp_path)


def test_load_no_secrets_never_calls_op(tmp_path, monkeypatch, op_env):
    _vault, calls = op_env
    monkeypatch.setattr(ksecrets, "config_dir", lambda: tmp_path)  # no secrets.yaml
    assert ksecrets.load() == ({}, [])
    assert _op_calls(calls) == []  # no secrets → op is never invoked (no prompt)


def test_load_env_scheme_does_not_call_op(tmp_path, monkeypatch, op_env):
    _vault, calls = op_env
    monkeypatch.setenv("MY_TOKEN", "xyz")
    _write_secrets(tmp_path, monkeypatch, {"TOK": "env://MY_TOKEN"})
    env, args = ksecrets.load()
    assert env == {"TOK": "xyz"}
    assert args == ["-e", "TOK"]
    assert _op_calls(calls) == []  # env:// must not spawn op


def test_load_batches_op_and_mixes_env(tmp_path, monkeypatch, op_env):
    vault, calls = op_env
    vault.update({"op://V/a/t": "AAA", "op://V/b/t": "BBB"})
    monkeypatch.setenv("E", "ev")
    _write_secrets(tmp_path, monkeypatch, {"A": "op://V/a/t", "B": "op://V/b/t", "EE": "env://E"})
    env, args = ksecrets.load()
    assert env == {"A": "AAA", "B": "BBB", "EE": "ev"}
    assert args == ["-e", "A", "-e", "B", "-e", "EE"]  # one -e per var, in file order
    assert len(_op_calls(calls)) == 1  # both op secrets resolved in ONE call


def test_load_unknown_scheme_exits(tmp_path, monkeypatch, op_env):
    _write_secrets(tmp_path, monkeypatch, {"X": "vault://secret/x"})
    with pytest.raises(SystemExit):
        ksecrets.load()


def test_load_malformed_ref_exits(tmp_path, monkeypatch, op_env):
    _write_secrets(tmp_path, monkeypatch, {"X": "no-scheme"})
    with pytest.raises(SystemExit):
        ksecrets.load()
