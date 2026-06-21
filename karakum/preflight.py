import shutil
import subprocess
import sys
from pathlib import Path


def check_tools() -> None:
    if not shutil.which("docker"):
        print("karakum: 'docker' not on PATH (install Docker Desktop or OrbStack)", file=sys.stderr)
        raise SystemExit(2)


def check_gh() -> None:
    if not shutil.which("gh"):
        print("karakum: 'gh' not on PATH (install GitHub CLI: brew install gh)", file=sys.stderr)
        raise SystemExit(2)


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
