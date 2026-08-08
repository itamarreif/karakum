"""Tests for cleanup.pr_states — the branch→PR-state mapping behind `just sessions`.

The regression these guard: a failed PR lookup (unauthenticated host, offline)
used to be swallowed and reported as "no-pr", indistinguishable from a branch that
genuinely has no PR — so every row read "no-pr". A lookup failure must now surface
as "?" (unknown), separate from a confirmed "no-pr".

The lookup shells out to `gh api .../pulls` (REST), emitting one compact JSON object
per PR (JSON Lines) — the fixtures below mirror that `--jq` output shape.
"""
import json
from pathlib import Path
from types import SimpleNamespace

from karakum import cleanup


def _fake_run(origins: dict, gh: dict):
    """Fake subprocess.run answering the two calls pr_states makes.

    origins: {clone-path str -> origin url}; a missing path => `git remote get-url`
             fails (rc 1), modeling a clone with no resolvable origin.
    gh:      {cwd str -> (returncode, stdout)} for the `gh api .../pulls` call.
    """
    def run(cmd, **kw):
        if cmd[:2] == ["git", "-C"]:  # ["git","-C",<path>,"remote","get-url","origin"]
            url = origins.get(cmd[2])
            return SimpleNamespace(returncode=0 if url else 1,
                                   stdout=(url + "\n") if url else "", stderr="")
        if cmd[0] == "gh":
            rc, out = gh.get(kw.get("cwd"), (1, ""))
            return SimpleNamespace(returncode=rc, stdout=out, stderr="")
        raise AssertionError(cmd)
    return run


def _clone(label, path, branch):
    return cleanup.Clone(label=label, path=Path(path), branch=branch)


def test_pr_states_maps_open_and_no_pr(monkeypatch):
    opened = _clone("karakum", "/s/a/o/karakum", "takwin/opencode")
    empty = _clone("karakum", "/s/a/n/karakum", "takwin/nothing")
    origins = {str(opened.path): "git@github.com:o/karakum.git",
               str(empty.path): "git@github.com:o/karakum.git"}
    prs = "\n".join(json.dumps(pr) for pr in [
        {"number": 18, "ref": "takwin/opencode", "merged": False, "state": "open"},
        {"number": 9, "ref": "takwin/old", "merged": True, "state": "closed"},
    ])
    # Both clones share an origin → one gh call, from the first clone's path.
    gh = {str(opened.path): (0, prs)}
    monkeypatch.setattr(cleanup.subprocess, "run", _fake_run(origins, gh))

    res = cleanup.pr_states([opened, empty])
    assert res["takwin/opencode"] == "#18"      # open PR → its number
    assert res["takwin/nothing"] == "no-pr"     # repo queried OK, branch has none


def test_pr_states_gh_failure_is_unknown_not_no_pr(monkeypatch):
    c = _clone("karakum", "/s/a/x/karakum", "takwin/opencode")
    origins = {str(c.path): "git@github.com:o/karakum.git"}
    gh = {str(c.path): (1, "")}  # e.g. gh not authenticated on the host
    monkeypatch.setattr(cleanup.subprocess, "run", _fake_run(origins, gh))

    assert cleanup.pr_states([c]) == {"takwin/opencode": "?"}


def test_pr_states_no_origin_is_unknown(monkeypatch):
    c = _clone("scratchpad", "/s/a/x/scratchpad", "bare-slug")
    monkeypatch.setattr(cleanup.subprocess, "run", _fake_run({}, {}))  # origin unresolvable
    assert cleanup.pr_states([c]) == {"bare-slug": "?"}


def test_pr_states_one_failing_repo_does_not_taint_another(monkeypatch):
    ok = _clone("karakum", "/s/a/ok/karakum", "takwin/opencode")
    bad = _clone("scratchpad", "/s/a/bad/scratch", "karakum/opencode")
    origins = {str(ok.path): "git@github.com:o/karakum.git",
               str(bad.path): "git@github.com:o/scratch.git"}
    gh = {
        str(ok.path): (0, json.dumps({"number": 18, "ref": "takwin/opencode", "merged": False, "state": "open"})),
        str(bad.path): (1, ""),  # this repo's lookup fails
    }
    monkeypatch.setattr(cleanup.subprocess, "run", _fake_run(origins, gh))

    res = cleanup.pr_states([ok, bad])
    assert res["takwin/opencode"] == "#18"   # healthy repo unaffected
    assert res["karakum/opencode"] == "?"    # failing repo → unknown


def test_pr_states_forwards_env_to_gh(monkeypatch):
    """The injected GH_TOKEN env (see cli._gh_env) reaches the `gh` subprocess."""
    c = _clone("karakum", "/s/a/x/karakum", "takwin/opencode")
    origins = {str(c.path): "git@github.com:o/karakum.git"}
    seen_env = {}

    def run(cmd, **kw):
        if cmd[:2] == ["git", "-C"]:
            return SimpleNamespace(returncode=0, stdout=origins[cmd[2]] + "\n", stderr="")
        if cmd[0] == "gh":
            seen_env["env"] = kw.get("env")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(cmd)

    monkeypatch.setattr(cleanup.subprocess, "run", run)
    cleanup.pr_states([c], env={"GH_TOKEN": "tok-123"})
    assert seen_env["env"] == {"GH_TOKEN": "tok-123"}  # threaded through to gh, not the git call
