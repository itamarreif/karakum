import os
import subprocess
from pathlib import Path

from karakum import config, console

# A launch waits this long for the one network call it makes (the fetch below).
# Past it the session is created from the host checkout instead — a slow origin
# must not hold up a shell.
FETCH_TIMEOUT = 20


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


def _fetch(session: Path) -> bool:
    """Refresh `origin/*` from the real origin. True if it worked.

    The clone is made from the host checkout, so every `origin/*` ref in it
    describes the *host's* branches, not the remote's — `--prune` is what drops
    the ones the remote does not have. Failure is a normal outcome (no network,
    no credentials, a slow host) and is never fatal: the caller falls back to the
    host checkout, which is all a launch could do before this existed.
    """
    r = subprocess.run(
        ["git", "-C", str(session), "fetch", "--quiet", "--prune", "origin"],
        capture_output=True, text=True, timeout=FETCH_TIMEOUT,
        # No credential prompt: an origin needing one would hang the launch.
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    return r.returncode == 0


def _remote_head(session: Path) -> str | None:
    """The remote's default branch as a ref (`origin/main`), or None.

    `set-head -a` is what asks the remote which branch that is; the clone's own
    `origin/HEAD` points at whatever the host had checked out.
    """
    subprocess.run(
        ["git", "-C", str(session), "remote", "set-head", "origin", "-a"],
        capture_output=True,
    )
    r = subprocess.run(
        ["git", "-C", str(session), "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None
    return r.stdout.strip() or None


def _has_ref(session: Path, ref: str) -> bool:
    return subprocess.run(
        ["git", "-C", str(session), "rev-parse", "--verify", "--quiet", ref],
        capture_output=True,
    ).returncode == 0


def _checkout_args(
    branch: str, *, remote_branch: bool, local_branch: bool, remote_head: str | None
) -> list[str]:
    """`git checkout` arguments for a new session branch, in preference order.

    1. The clone carried a local branch of that name over from the host. Check it
       out as it is — it may hold host-side work that was never pushed, and `-b`
       would fail on it anyway.
    2. The remote has the branch — a session resumed after its work was pushed.
       Track it, so `git push` and `git pull` need no arguments.
    3. Cut it from the remote's default branch. This is the point of fetching: the
       host checkout can be stale, or parked on an unmerged branch, and a session
       branch cut from it silently starts from the wrong place. `--no-track`
       because tracking `origin/main` from a session branch makes a bare
       `git push` refuse under `push.default=simple`.
    4. Nothing to go on (the fetch failed) — the clone's HEAD, i.e. the host's.
    """
    if local_branch:
        return [branch]
    if remote_branch:
        return ["-b", branch, "--track", f"origin/{branch}"]
    if remote_head:
        return ["--no-track", "-b", branch, remote_head]
    return ["-b", branch]


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

    The bytes come from the host, but the **branch point comes from origin**: the
    clone is fetched and the session branch is cut from the remote's default
    branch (`_checkout_args`). Cloning locally keeps a launch fast and offline-
    capable; fetching keeps a host checkout that is behind, or parked on an
    unmerged branch, from deciding where the session starts.

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

    # The clone came from the host, so its refs are the host's. Fetch before
    # branching: a host repo that is behind, or parked on an unmerged branch,
    # would otherwise decide where this session starts.
    try:
        fetched = _fetch(session)
    except subprocess.TimeoutExpired:
        fetched = False
    remote_head = _remote_head(session) if fetched else None
    local_branch = _has_ref(session, f"refs/heads/{branch}")
    remote_branch = fetched and _has_ref(session, f"refs/remotes/origin/{branch}")
    args = _checkout_args(
        branch,
        remote_branch=remote_branch,
        local_branch=local_branch,
        remote_head=remote_head,
    )
    subprocess.run(["git", "-C", str(session), "checkout", *args], check=True)

    if local_branch:
        start = "the host checkout, which already had it"
    elif remote_branch:
        start = f"origin/{branch}"
    else:
        start = remote_head or "the host checkout"
    console.detail(f"cut from {start}")

    if not fetched:
        console.warn(
            f"could not reach origin for {repo_label} — cut {branch} from the host "
            "checkout, which may be behind or on another branch. Pull the host repo, "
            "or rebase this session once you are online."
        )
    elif local_branch:
        console.warn(
            f"the host checkout already had {branch}, so the clone carried it over and "
            f"it is checked out as it stands — not cut from {remote_head or 'origin'}. "
            "Rebase it yourself if it is behind."
        )

    return session


def no_session_warning() -> None:
    console.warn("WARNING — no session slug given; running on main branch.")
    console.warn("Changes here affect the live repo. Use a session slug for isolated work.")
