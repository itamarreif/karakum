import json
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from karakum import console


def check_tools() -> None:
    if not shutil.which("docker"):
        console.error("'docker' not on PATH (install Docker Desktop or OrbStack)")
        raise SystemExit(2)


def check_gh() -> None:
    if not shutil.which("gh"):
        console.error("'gh' not on PATH (install GitHub CLI: brew install gh)")
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
            console.warn(
                "WARNING — GH_TOKEN is set but GitHub rejected it (401 Bad credentials). "
                "`gh` will fail in-container (git over SSH still works). Refresh the token at its "
                "source in secrets.yaml and relaunch."
            )
        elif e.code == 403:
            console.warn(
                "WARNING — GH_TOKEN is set but GitHub returned 403 (missing scopes, SSO, "
                "or rate limit). `gh` may fail in-container."
            )
        # Any other HTTP status: not an auth verdict — stay quiet.
    except (urllib.error.URLError, TimeoutError, OSError):
        # Offline / GitHub unreachable — not the token's fault; don't gate launch.
        pass
    else:
        if login:
            console.info(f"GH_TOKEN valid (gh authenticates as {login}).")


def check_ssh_agent() -> None:
    """Warn (non-fatal) if the host SSH agent won't authenticate to GitHub.

    In-container git runs over SSH against the *forwarded host agent* — no key
    material is in the image, so every push depends on a host-side condition the
    container can't see. Two of them surface inside as a bare
    `Permission denied (publickey)` with nothing to diagnose from:

    - the default agent is unreachable, or holds no identities;
    - the agent is 1Password's, which prompts for approval **on the host** the
      first time a key is used. From inside a container that prompt is invisible:
      the push just fails, and the same command works a minute later once someone
      happens to approve it.

    So do the first GitHub handshake here, at launch, while the user is looking at
    this terminal — it turns both cases into an actionable message and gets any
    approval prompt out of the way before the session starts. Offline / GitHub
    unreachable never gates a launch, and neither does this check: it only warns.
    """
    if not shutil.which("ssh-add") or not shutil.which("ssh"):
        return  # no openssh on the host: nothing to probe, and nothing we can advise

    listing = subprocess.run(["ssh-add", "-l"], capture_output=True, text=True)
    if listing.returncode == 2:
        console.warn(
            "WARNING — can't reach the host SSH agent. In-container `git push` will fail with "
            "'Permission denied (publickey)'."
        )
        console.detail("start an agent, or make 1Password's your default — see docs/ssh.md")
        return
    if listing.returncode == 1:
        console.warn(
            "WARNING — the host SSH agent holds no identities. In-container `git push` will fail "
            "with 'Permission denied (publickey)'."
        )
        console.detail("`ssh-add <key>`, or make 1Password's agent your default — see docs/ssh.md")
        return

    # `ssh -T git@github.com` exits 1 on *success* (GitHub refuses shell access), so the
    # greeting is the verdict, not the status. BatchMode suppresses ssh's own passphrase
    # prompts without touching 1Password's out-of-band approval, which is the one we want
    # to trigger; StrictHostKeyChecking=yes fails fast instead of hanging on an invisible
    # known-hosts prompt (and never writes to the user's known_hosts on our behalf).
    try:
        probe = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
             "-T", "git@github.com"],
            capture_output=True, text=True, timeout=20,
        )
    except subprocess.TimeoutExpired:
        console.warn(
            "WARNING — GitHub SSH auth didn't finish in 20s. If your default agent is "
            "1Password's, approve the prompt on the host, then relaunch."
        )
        return
    except OSError:
        return  # couldn't even exec ssh — not a verdict about the agent

    output = probe.stdout + probe.stderr
    if "successfully authenticated" in output:
        return
    if "Host key verification failed" in output:
        console.warn(
            "WARNING — github.com isn't in the host's known_hosts, so this check couldn't run. "
            "The container pins GitHub's keys itself, so the session is probably fine."
        )
        return
    if "Could not resolve hostname" in output or "Connection timed out" in output:
        return  # offline / GitHub unreachable — not the agent's fault
    console.warn(
        "WARNING — the host SSH agent has keys but GitHub rejected them. In-container "
        "`git push` will fail with 'Permission denied (publickey)'."
    )
    console.detail("reproduce on the host with `ssh -T git@github.com` — see docs/ssh.md")


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
        console.error(f"{label} at {path} is not a git repo")
        console.detail(f"init it first: (cd {path} && git init && add an 'origin' remote)")
        raise SystemExit(2)

    result = subprocess.run(
        ["git", "-C", str(path), "remote", "get-url", "origin"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        console.error(f"{label} at {path} has no 'origin' remote")
        console.detail(f"PRs need a remote: git -C {path} remote add origin <url>")
        raise SystemExit(2)

    actual_norm = _canonicalize(result.stdout.strip())
    expected_norm = _canonicalize(expected_repo)
    if actual_norm != expected_norm:
        console.error(f"{label} at {path} has unexpected origin")
        console.detail(f"expected (from manifest): {expected_norm}")
        console.detail(f"actual   (from origin)  : {actual_norm}")
        raise SystemExit(2)
