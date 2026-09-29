"""Where a new session branch starts.

`session.ensure` clones the *host* checkout (fast, offline, and the host's `.git`
never reaches the container) but must not let the host decide the branch point:
a host repo that is behind, or parked on an unmerged branch, would otherwise hand
the session the wrong commit silently. So the clone is fetched and the branch is
cut from the remote.

These drive `session.ensure` directly rather than through `launch` — the wiring
around it is covered in `test_launch_branches.py`, and one clone per test keeps
these quick. Origins are local bare repos, so the real fetch runs offline.
"""
import subprocess
from pathlib import Path

from karakum import session


def _run(*args, cwd=None):
    subprocess.run(args, check=True, capture_output=True, cwd=cwd)


def _git(path, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(path), *args], check=check, capture_output=True, text=True
    ).stdout.strip()


def _commit(path, text):
    (Path(path) / "README").write_text(text)
    _run("git", "-C", str(path), "add", "-A")
    _run("git", "-C", str(path), "-c", "user.email=a@b.c", "-c", "user.name=t",
         "-c", "commit.gpgsign=false", "commit", "-qm", text)
    return _git(path, "rev-parse", "HEAD")


def _host_repo(tmp_path, name="src"):
    """A host checkout with one commit, pushed to a local bare `origin`."""
    host, remote = tmp_path / name, tmp_path / f"{name}.git"
    host.mkdir(parents=True)
    _run("git", "init", "-q", "--bare", "-b", "main", str(remote))
    _run("git", "init", "-q", "-b", "main", str(host))
    sha = _commit(host, "A")
    _run("git", "-C", str(host), "remote", "add", "origin", str(remote))
    _run("git", "-C", str(host), "push", "-q", "origin", "main")
    return host, remote, sha


def _push_to_remote(tmp_path, remote, ref, text):
    """Advance the remote without touching the host checkout.

    A commit made in the host would also leave a *local* branch behind, which is
    a different case (the clone carries it over) — this keeps them separable.
    """
    work = tmp_path / f"push-{ref.replace('/', '-')}"
    _run("git", "clone", "-q", str(remote), str(work))
    sha = _commit(work, text)
    _run("git", "-C", str(work), "push", "-q", "origin", f"HEAD:refs/heads/{ref}")
    return sha


def _ensure(tmp_path, monkeypatch, host, branch="alice/fix-login"):
    monkeypatch.setenv("KARAKUM_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KARAKUM_CONFIG_DIR", str(tmp_path / "config"))
    return session.ensure(host, "alice", "fix-login", "project", "example.com/x.git", branch)


def _upstream(clone):
    r = subprocess.run(
        ["git", "-C", str(clone), "rev-parse", "--abbrev-ref", "@{u}"],
        capture_output=True, text=True,
    )
    return r.stdout.strip() if r.returncode == 0 else None


# --- the picker, on its own ---------------------------------------------------

def test_remote_default_is_the_start_point_for_a_new_branch():
    assert session._checkout_args(
        "a/b", remote_branch=False, local_branch=False, remote_head="origin/main"
    ) == ["--no-track", "-b", "a/b", "origin/main"]


def test_a_branch_the_remote_already_has_is_tracked():
    assert session._checkout_args(
        "a/b", remote_branch=True, local_branch=False, remote_head="origin/main"
    ) == ["-b", "a/b", "--track", "origin/a/b"]


def test_a_branch_carried_over_from_the_host_is_checked_out_as_it_stands():
    """`-b` would fail on it, and it may hold host-side work that was never pushed."""
    assert session._checkout_args(
        "a/b", remote_branch=True, local_branch=True, remote_head="origin/main"
    ) == ["a/b"]


def test_without_a_remote_head_the_clone_head_is_all_there_is():
    assert session._checkout_args(
        "a/b", remote_branch=False, local_branch=False, remote_head=None
    ) == ["-b", "a/b"]


# --- end to end, against real repos ------------------------------------------

def test_branch_starts_at_the_remote_tip_though_the_host_is_behind_and_parked(
        tmp_path, monkeypatch):
    """The bug this exists for: the host is stale *and* on an unmerged branch."""
    host, remote, _ = _host_repo(tmp_path)
    tip = _push_to_remote(tmp_path, remote, "main", "B")
    _run("git", "-C", str(host), "checkout", "-q", "-b", "parked")

    clone = _ensure(tmp_path, monkeypatch, host)

    assert _git(clone, "rev-parse", "HEAD") == tip
    assert _git(clone, "rev-parse", "--abbrev-ref", "HEAD") == "alice/fix-login"
    # Tracking origin/main would make a bare `git push` refuse under push.default=simple.
    assert _upstream(clone) is None


def test_a_session_branch_the_remote_has_is_resumed_and_tracked(tmp_path, monkeypatch):
    host, remote, _ = _host_repo(tmp_path)
    pushed = _push_to_remote(tmp_path, remote, "alice/fix-login", "session work")

    clone = _ensure(tmp_path, monkeypatch, host)

    assert _git(clone, "rev-parse", "HEAD") == pushed
    assert _upstream(clone) == "origin/alice/fix-login"


def test_an_unreachable_origin_falls_back_to_the_host_and_says_so(
        tmp_path, monkeypatch, capsys):
    host, _, sha = _host_repo(tmp_path)
    _run("git", "-C", str(host), "remote", "set-url", "origin",
         str(tmp_path / "gone.git"))          # instant failure, no DNS

    clone = _ensure(tmp_path, monkeypatch, host)

    assert _git(clone, "rev-parse", "HEAD") == sha
    assert _git(clone, "rev-parse", "--abbrev-ref", "HEAD") == "alice/fix-login"
    assert "could not reach origin" in capsys.readouterr().err


def test_a_host_side_branch_of_the_same_name_is_kept_and_flagged(
        tmp_path, monkeypatch, capsys):
    """Host-side work is never clobbered, but the launch must say it was used."""
    host, remote, _ = _host_repo(tmp_path)
    _push_to_remote(tmp_path, remote, "main", "B")
    _run("git", "-C", str(host), "checkout", "-q", "-b", "alice/fix-login")
    host_sha = _commit(host, "host-side work")

    clone = _ensure(tmp_path, monkeypatch, host)

    assert _git(clone, "rev-parse", "HEAD") == host_sha
    assert "already had alice/fix-login" in capsys.readouterr().err


def test_a_fetch_that_times_out_is_not_fatal(tmp_path, monkeypatch):
    host, remote, sha = _host_repo(tmp_path)
    _push_to_remote(tmp_path, remote, "main", "B")
    monkeypatch.setattr(session, "FETCH_TIMEOUT", 0.0)

    clone = _ensure(tmp_path, monkeypatch, host)

    assert _git(clone, "rev-parse", "HEAD") == sha          # the host's commit
