"""Session enumeration, status helpers, and removal.

A *session* is `<sessions_root>/<agent>/<slug>/` and may hold several label
clones (e.g. `scratchpad` + a project), each a full `git clone` on branch
`<agent>/<slug>`.  Removal operates at the (agent, slug) granularity.
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
    same guard `session.ensure` uses, so a stray file or a linked worktree's
    `.git` file is ignored.
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
    """Count commits on the session branch not present on any `origin` remote ref."""
    out = _git(clone, "rev-list", "--count", clone.branch, "--not", "--remotes=origin")
    return int(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else 0


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


def remove(session: Session) -> None:
    """Delete the session dir, then reap any exited containers it left behind."""
    shutil.rmtree(session.path)
    _reap_containers(session)


def _reap_containers(session: Session) -> None:
    """Best-effort removal of exited `agent-<agent>-<slug>-*` containers."""
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
