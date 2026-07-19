import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


def check_tools() -> None:
    if not shutil.which("docker"):
        print("karakum: 'docker' not on PATH (install Docker Desktop or OrbStack)", file=sys.stderr)
        raise SystemExit(2)


def check_gh() -> None:
    if not shutil.which("gh"):
        print("karakum: 'gh' not on PATH (install GitHub CLI: brew install gh)", file=sys.stderr)
        raise SystemExit(2)


def check_github_token(token: str) -> None:
    """Warn (non-fatal) if a resolved GH_TOKEN is present but GitHub rejects it.

    In the container `gh` authenticates solely from `GH_TOKEN`; git runs over SSH
    on a separate path. A stale token therefore doesn't block the session — but a
    401 at launch is far clearer than a mystery `Bad credentials` on the first
    `gh` call. We hit `GET /user` with a short timeout and only warn on a
    definitive auth rejection; unreachable-GitHub / offline is ignored so a network
    blip never gates a launch.
    """
    if not token:
        return
    req = urllib.request.Request(
        "https://api.github.com/user",
        headers={"Authorization": f"Bearer {token}", "User-Agent": "karakum-preflight"},
    )
    try:
        with urllib.request.urlopen(req, timeout=4) as resp:
            login = json.load(resp).get("login")
    except urllib.error.HTTPError as e:
        if e.code == 401:
            print(
                "karakum: WARNING — GH_TOKEN is set but GitHub rejected it (401 Bad credentials). "
                "`gh` will fail in-container (git over SSH still works). Refresh the token at its "
                "source in secrets.yaml and relaunch.",
                file=sys.stderr,
            )
        elif e.code == 403:
            print(
                "karakum: WARNING — GH_TOKEN is set but GitHub returned 403 (missing scopes, SSO, "
                "or rate limit). `gh` may fail in-container.",
                file=sys.stderr,
            )
        # Any other HTTP status: not an auth verdict — stay quiet.
    except (urllib.error.URLError, TimeoutError, OSError):
        # Offline / GitHub unreachable — not the token's fault; don't gate launch.
        pass
    else:
        if login:
            print(f"karakum: GH_TOKEN valid (gh authenticates as {login}).", file=sys.stderr)


def _canonicalize(repo: str) -> str:
    r = repo
    for prefix in ("https://", "http://", "git@"):
        if r.startswith(prefix):
            r = r[len(prefix):]
    r = r.replace(":", "/", 1)  # git@host:owner/repo → host/owner/repo
    if r.endswith(".git"):
        r = r[:-4]
    return r.rstrip("/")


def check_repo(path: Path, expected_repo: str, label: str = "repo") -> None:
    path = Path(path)
    if not (path / ".git").exists():
        print(f"karakum: {label} at {path} is not a git repo", file=sys.stderr)
        print(f"        init it first: (cd {path} && git init && add an 'origin' remote)", file=sys.stderr)
        raise SystemExit(2)

    result = subprocess.run(
        ["git", "-C", str(path), "remote", "get-url", "origin"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"karakum: {label} at {path} has no 'origin' remote", file=sys.stderr)
        print(f"        PRs need a remote: git -C {path} remote add origin <url>", file=sys.stderr)
        raise SystemExit(2)

    actual_norm = _canonicalize(result.stdout.strip())
    expected_norm = _canonicalize(expected_repo)
    if actual_norm != expected_norm:
        print(f"karakum: {label} at {path} has unexpected origin", file=sys.stderr)
        print(f"        expected (from manifest): {expected_norm}", file=sys.stderr)
        print(f"        actual   (from origin)  : {actual_norm}", file=sys.stderr)
        raise SystemExit(2)
