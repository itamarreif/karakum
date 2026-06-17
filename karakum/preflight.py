import os
import shutil
import subprocess
import sys
from pathlib import Path


def check_tools() -> None:
    if not shutil.which("docker"):
        print("karakum: 'docker' not on PATH (install Docker Desktop or OrbStack)", file=sys.stderr)
        raise SystemExit(2)


def check_ssh_agent(socket: str) -> None:
    """Warn (don't fail) if the host SSH agent we'll forward holds no keys.

    The container can only use keys present in the forwarded agent, so an empty
    agent means in-container `git push`/`pull` fail with `Permission denied` even
    though host git may work via a different agent. A warning, not a hard error:
    plenty of sessions never push. See docs/ssh.md.
    """
    if not shutil.which("ssh-add"):
        return
    result = subprocess.run(
        ["ssh-add", "-l"],
        capture_output=True, text=True,
        env={**os.environ, "SSH_AUTH_SOCK": socket},
    )
    if result.returncode == 0:
        return  # agent reachable and has identities
    if result.returncode == 1:
        print(f"karakum: WARNING — SSH agent at {socket} has no keys loaded.", file=sys.stderr)
        print("        in-container git over SSH will fail with 'Permission denied'.", file=sys.stderr)
        print("        load one (`ssh-add <key>`), or for 1Password enable its agent + run `just ssh-setup`.", file=sys.stderr)
    else:
        print(f"karakum: WARNING — can't reach SSH agent at {socket}: {result.stderr.strip()}", file=sys.stderr)
        print("        in-container git over SSH may fail. See docs/ssh.md.", file=sys.stderr)


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
