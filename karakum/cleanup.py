"""Session enumeration, status helpers, and removal.

A *session* is `<sessions_root>/<agent>/<slug>/` and may hold several label
clones (e.g. `scratchpad` + a project), each a full `git clone`. The project
clone is on branch `<agent>/<slug>`; the memory (`scratchpad`) clone is on
`<project>/<slug>` (or a bare `<slug>` when there's no project). Removal
operates at the (agent, slug) granularity.
"""
import json
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from karakum import config


@dataclass(frozen=True)
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
            inferred_branch = f"{agent_dir.name}/{slug_dir.name}"
            clones = []
            for label_dir in sorted(p for p in slug_dir.iterdir() if p.is_dir()):
                if not (label_dir / ".git").is_dir():
                    continue
                r = subprocess.run(
                    ["git", "-C", str(label_dir), "rev-parse", "--abbrev-ref", "HEAD"],
                    capture_output=True, text=True,
                )
                branch = r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else inferred_branch
                clones.append(Clone(label=label_dir.name, path=label_dir, branch=branch))
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


def clone_status(clone: Clone) -> tuple[bool, int]:
    """Return (dirty, unpushed) for a clone in parallel-safe fashion."""
    return dirty(clone), unpushed(clone)


def pr_states(clones: list[Clone]) -> dict[str, str]:
    """Fetch PR states for all clones in one gh call per unique remote repo.

    Returns a dict mapping clone.branch → state string:
      - "#5"      an open PR (its number)
      - "merged" / "closed"   a resolved PR
      - "no-pr"   the repo was queried successfully and has no PR for that branch
      - "?"       the state is UNKNOWN — the `gh` call failed (not authenticated,
                  offline, …) or the clone has no resolvable `origin`. This is kept
                  distinct from "no-pr" so a gh/auth failure never masquerades as a
                  confirmed absence of PR (every row reading "no-pr" is the tell).

    Groups clones by origin URL so there's one API call per repo, not per clone.
    """
    # Group by origin remote URL
    by_origin: dict[str, list[Clone]] = {}
    for clone in clones:
        r = subprocess.run(
            ["git", "-C", str(clone.path), "remote", "get-url", "origin"],
            capture_output=True, text=True,
        )
        url = r.stdout.strip() if r.returncode == 0 else ""
        by_origin.setdefault(url, []).append(clone)

    def _fetch(repo_clones: list[Clone]) -> "dict[str, str] | None":
        """Branch→state for one repo, or None if the `gh` call failed."""
        result = subprocess.run(
            ["gh", "pr", "list", "--state", "all",
             "--json", "number,state,headRefName", "--limit", "200"],
            capture_output=True, text=True, cwd=str(repo_clones[0].path),
        )
        if result.returncode != 0:
            return None
        out: dict[str, str] = {}
        for pr in json.loads(result.stdout or "[]"):
            branch = pr["headRefName"]
            out[branch] = f"#{pr['number']}" if pr["state"] == "OPEN" else pr["state"].lower()
        return out

    branch_to_state: dict[str, str] = {}
    failed_origins: set[str] = set()  # repos whose gh lookup errored → state unknown
    repos = [(url, rc) for url, rc in by_origin.items() if url]
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_fetch, rc): url for url, rc in repos}
        for fut in as_completed(futures):
            mp = fut.result()
            if mp is None:
                failed_origins.add(futures[fut])
            else:
                branch_to_state.update(mp)

    # A clone with no resolvable origin ("") or in a repo we couldn't query is
    # UNKNOWN ("?"), not confirmed PR-less ("no-pr").
    result: dict[str, str] = {}
    for url, repo_clones in by_origin.items():
        unknown = url == "" or url in failed_origins
        for clone in repo_clones:
            result[clone.branch] = "?" if unknown else branch_to_state.get(clone.branch, "no-pr")
    return result


def remove(session: Session) -> None:
    """Delete the session dir, then reap any exited containers it left behind."""
    shutil.rmtree(session.path)
    _reap_containers(session)


def running_containers(agent: str, slug: str) -> list[str]:
    """Names of *running* `agent-<agent>-<slug>-*` containers for a session.

    Distinct from `_reap_containers`, which targets `status=exited` leftovers:
    this finds the live containers a stuck session is holding, for `session down`
    to `docker stop`. Same name-prefix filter used by `pngpaste`/`_reap_containers`.
    """
    r = subprocess.run(
        ["docker", "ps", "--filter", f"name=agent-{agent}-{slug}-", "--format", "{{.Names}}"],
        capture_output=True, text=True,
    )
    return r.stdout.split()


def stop_containers(names: list[str]) -> None:
    """`docker stop` the given containers (compose `--rm` auto-removes them)."""
    if names:
        subprocess.run(["docker", "stop", *names], capture_output=True, text=True)


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
