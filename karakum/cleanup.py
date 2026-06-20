"""Session cleanup: enumerate session clones, compute their status, and remove
the ones a configurable safe-delete predicate marks reapable.

A *session* is `<sessions_root>/<agent>/<slug>/` and may hold **several label
clones** (e.g. `scratchpad` + a project), each a full `git clone` on branch
`<agent>/<slug>` whose `origin` points at GitHub (see `session.ensure`). Cleanup
works at the **(agent, slug)** granularity: it removes the whole `<agent>/<slug>`
dir, and only when the predicate holds for *every* clone inside it.

Predicates are keyed by name and selected via `config.cleanup_predicate()`
(`~/.karakum/config.yaml` → `cleanup.predicate`, default `merged`):

- ``merged`` — the session branch's PR is merged on GitHub (`gh`). The default:
  the work is on GitHub, so removing the local clone is non-destructive.
- ``pushed`` — the clone has a clean working tree and no unpushed commits
  (needs only git; no `gh`).
"""
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from karakum import config


@dataclass
class Clone:
    """One label clone inside a session (e.g. the `scratchpad` or project repo)."""
    label: str
    path: Path
    branch: str


@dataclass
class Session:
    """A `<sessions_root>/<agent>/<slug>/` dir and the clones it groups."""
    agent: str
    slug: str
    path: Path
    clones: list[Clone]


def iter_sessions(agent: str | None = None) -> list[Session]:
    """List sessions under `config.sessions_root()`, optionally filtered by agent.

    Only label subdirs whose `.git` is a real *directory* count as clones — the
    same "real karakum clone" guard `session.ensure` uses, so a stray file or a
    linked worktree's `.git` file is ignored rather than treated as a session.
    """
    root = config.sessions_root()
    if not root.is_dir():
        return []

    sessions: list[Session] = []
    for agent_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        if agent is not None and agent_dir.name != agent:
            continue
        for slug_dir in sorted(p for p in agent_dir.iterdir() if p.is_dir()):
            branch = f"{agent_dir.name}/{slug_dir.name}"
            clones = [
                Clone(label=label_dir.name, path=label_dir, branch=branch)
                for label_dir in sorted(p for p in slug_dir.iterdir() if p.is_dir())
                if (label_dir / ".git").is_dir()
            ]
            if clones:
                sessions.append(
                    Session(agent=agent_dir.name, slug=slug_dir.name, path=slug_dir, clones=clones)
                )
    return sessions


def _git(clone: Clone, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(clone.path), *args],
        capture_output=True, text=True,
    )


def dirty(clone: Clone) -> bool:
    """True if the clone has uncommitted changes or untracked files."""
    return bool(_git(clone, "status", "--porcelain").stdout.strip())


def unpushed(clone: Clone) -> int:
    """Count commits on the session branch not present on any `origin` remote ref.

    `--not --remotes=origin` covers the never-pushed case (no `origin/<branch>`
    upstream yet) without erroring.
    """
    out = _git(clone, "rev-list", "--count", clone.branch, "--not", "--remotes=origin")
    return int(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else 0


def pr_merged(clone: Clone) -> bool:
    """True if a PR with this branch as head is merged on GitHub.

    `gh` resolves the repo from the clone's `origin` remote (we run it with
    `cwd=clone.path`); a non-empty merged-PR list means merged.
    """
    result = subprocess.run(
        ["gh", "pr", "list", "--head", clone.branch, "--state", "merged", "--json", "number"],
        capture_output=True, text=True, cwd=str(clone.path),
    )
    if result.returncode != 0:
        return False
    return result.stdout.strip() not in ("", "[]")


def pr_state(clone: Clone) -> str:
    """Human-readable PR state for listings: #N (open) / merged / none / unknown."""
    result = subprocess.run(
        ["gh", "pr", "list", "--head", clone.branch, "--state", "all",
         "--json", "number,state",
         "--jq", r'.[0] | if . == null then "no pr" elif .state == "OPEN" then "#\(.number)" else (.state | ascii_downcase) end'],
        capture_output=True, text=True, cwd=str(clone.path),
    )
    if result.returncode != 0:
        return "unknown"
    return result.stdout.strip() or "no pr"


# --- safe-delete predicates -------------------------------------------------

def _safe_merged(clone: Clone) -> tuple[bool, str]:
    return (True, "merged") if pr_merged(clone) else (False, "PR not merged")


def _safe_pushed(clone: Clone) -> tuple[bool, str]:
    if dirty(clone):
        return False, "dirty working tree"
    n = unpushed(clone)
    if n:
        return False, f"{n} unpushed commit(s)"
    return True, "clean + fully pushed"


_PREDICATES = {
    "merged": _safe_merged,
    "pushed": _safe_pushed,
}


def predicates() -> list[str]:
    return list(_PREDICATES)


def session_safe(session: Session, predicate: str) -> tuple[bool, str]:
    """A session is safe to delete iff the predicate holds for *all* its clones.

    Returns (safe, reason); on an unsafe session the reason names the first
    blocking clone so the user knows what to push/merge.
    """
    try:
        check = _PREDICATES[predicate]
    except KeyError:
        raise SystemExit(
            f"karakum: unknown cleanup predicate '{predicate}' "
            f"(known: {', '.join(predicates())})"
        )
    for clone in session.clones:
        ok, reason = check(clone)
        if not ok:
            return False, f"{clone.label}: {reason}"
    return True, "all clones " + predicate


def remove(session: Session) -> None:
    """Delete the session dir, then reap any exited containers it left behind."""
    shutil.rmtree(session.path)
    _reap_containers(session)


def _reap_containers(session: Session) -> None:
    """Best-effort removal of exited `agent-<agent>-<slug>-*` containers.

    The launch path uses `docker compose run --rm`, so this only catches runs
    that crashed before the auto-remove. Failures are non-fatal — the clone is
    already gone, which is the point of cleanup.
    """
    name_prefix = f"agent-{session.agent}-{session.slug}-"
    listed = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"name={name_prefix}", "--filter", "status=exited"],
        capture_output=True, text=True,
    )
    ids = listed.stdout.split()
    if ids:
        subprocess.run(["docker", "rm", *ids], capture_output=True, text=True)
        print(f"karakum: reaped {len(ids)} exited container(s) for {session.agent}/{session.slug}",
              file=sys.stderr)
