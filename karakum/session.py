import subprocess
from pathlib import Path

from karakum import config, console


def current_branch(path: Path) -> str | None:
    """The branch a clone is actually on, or None if it can't be read.

    None covers a detached HEAD, a dir that isn't a repo, and git failing — every
    caller has its own fallback for that, so this reports rather than guesses.
    """
    r = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None
    return r.stdout.strip() or None


def clone_label(role: str, repo_label: str) -> str:
    """The clone's directory name inside a session, and its mount basename.

    `scratchpad` for the agent memory repo; otherwise the repository's last path
    segment, so the name doesn't depend on where the repo happens to be checked
    out on the host. Callers need this rule before `ensure` runs — a session
    mounting several projects has to detect two repositories sharing a basename
    *before* creating clones, since they would land on the same path.
    """
    return "scratchpad" if role == "agent" else repo_label.rstrip("/").split("/")[-1]


def ensure(repo: Path, agent: str, slug: str, role: str, repo_label: str, branch: str) -> Path:
    """Create (or reuse) an isolated clone of `repo` for this session.

    Clones live under a central, configurable root grouped by session:
    `<sessions_root>/<agent>/<slug>/<label>` (label is `scratchpad` for the agent
    memory repo, or the project's name). This keeps every repo a session touches
    together and out of `<repo>/.worktrees/`, so it never collides with a manual
    `git worktree add`.

    Each session gets its own independent clone with its own `.git` (copied
    objects, no shared inodes). The container mounts only this clone, so the host
    repo's `.git` is never reachable and cannot be touched. The clone's `origin`
    is repointed at the host repo's GitHub remote so the agent pushes there;
    session branches reach the host via push + pull.

    `branch` is the branch to check out — the caller namespaces it: `<agent>/<slug>`
    for a project clone, `<project>/<slug>` for the memory clone (or a bare
    `<slug>` for a memory-only session). The directory tree stays keyed by
    `<agent>/<slug>` regardless, so a session's clones sit together even when
    their branches differ.

    `role` ("agent" or "project") and `repo_label` (the manifest's canonical
    `repository`, e.g. `github.com/owner/repo`) label log output — a session
    spans one clone per repo and may mount several projects, so the lines
    otherwise look like duplicates.
    `repo_label` is used instead of the local directory name so the line doesn't
    depend on where the repo happens to be checked out.
    """
    repo = Path(repo).resolve()
    session = config.sessions_root() / agent / slug / clone_label(role, repo_label)

    if session.exists():
        # Reuse only a real karakum clone (`.git` is a directory). A `.git` *file*
        # (a git worktree) or anything else means the path wasn't created by
        # karakum — fail loudly rather than mount an unusable dir.
        if (session / ".git").is_dir():
            # Reuse is deliberately non-destructive: an existing clone is left on
            # whatever branch it is on, and is NOT switched to `branch`. So report
            # what is actually checked out, not what was asked for — they diverge
            # when a project is renamed, or when someone checked out another branch
            # inside the clone mid-session.
            actual = current_branch(session)
            if actual and actual != branch:
                console.warn(
                    f"reusing {role} session: {repo_label} @ {actual} "
                    f"— NOT {branch}, which this launch asked for. The clone already "
                    "existed and reuse never switches branches; check it out yourself "
                    "if that is what you meant."
                )
            else:
                console.info(f"reusing {role} session: {repo_label} @ {actual or branch}")
            return session
        console.error(
            f"{session} exists but is not a karakum clone (no .git directory) — "
            "refusing to use it. Remove it or pick another slug."
        )
        raise SystemExit(2)

    console.info(f"creating {role} session: {repo_label} @ {branch}")
    session.parent.mkdir(parents=True, exist_ok=True)

    # The host repo's GitHub remote — set on the clone so the agent pushes to
    # GitHub rather than back into the local checkout.
    origin_url = subprocess.run(
        ["git", "-C", str(repo), "remote", "get-url", "origin"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    # `file://` forces the git transport (no daemon, works offline) and produces
    # a fully independent object store — no hardlinks, only reachable objects.
    subprocess.run(
        ["git", "clone", "--no-local", f"file://{repo}", str(session)],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(session), "remote", "set-url", "origin", origin_url],
        check=True,
    )

    # Check out the session branch — reuse it if the clone already carried it
    # over, otherwise create it off the clone's default HEAD.
    exists = subprocess.run(
        ["git", "-C", str(session), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
    ).returncode == 0
    checkout = [branch] if exists else ["-b", branch]
    subprocess.run(
        ["git", "-C", str(session), "checkout", *checkout],
        check=True,
    )

    return session


def no_session_warning() -> None:
    console.warn("WARNING — no session slug given; running on main branch.")
    console.warn("Changes here affect the live repo. Use a session slug for isolated work.")
