import subprocess
import sys
from pathlib import Path

from karakum import config


def ensure(repo: Path, agent: str, slug: str, role: str, repo_label: str) -> Path:
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

    `role` ("agent" or "project") and `repo_label` (the manifest's canonical
    `repository`, e.g. `github.com/owner/repo`) label log output — a session
    spans one clone per repo, so the two lines otherwise look like a duplicate.
    `repo_label` is used instead of the local directory name so the line doesn't
    depend on where the repo happens to be checked out.
    """
    repo = Path(repo).resolve()
    branch = f"{agent}/{slug}"
    label = "scratchpad" if role == "agent" else repo_label.rstrip("/").split("/")[-1]
    session = config.sessions_root() / agent / slug / label

    if session.exists():
        # Reuse only a real karakum clone (`.git` is a directory). A `.git` *file*
        # (a git worktree) or anything else means the path wasn't created by
        # karakum — fail loudly rather than mount an unusable dir.
        if (session / ".git").is_dir():
            print(f"karakum: reusing {role} session: {repo_label} @ {branch}", file=sys.stderr)
            return session
        print(
            f"karakum: {session} exists but is not a karakum clone (no .git directory) — "
            "refusing to use it. Remove it or pick another slug.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    print(f"karakum: creating {role} session: {repo_label} @ {branch}", file=sys.stderr)
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
    print("karakum: WARNING — no session slug given; running on main branch.", file=sys.stderr)
    print("karakum: Changes here affect the live repo. Use a session slug for isolated work.", file=sys.stderr)
