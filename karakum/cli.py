#!/usr/bin/env python3
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import click

from karakum import cleanup, config, manifest, preflight
from karakum import secrets as ksecrets
from karakum import session as ksession


def _git_identity_args(agent: str) -> list[str]:
    """Return docker -e args for GIT_AUTHOR_*/GIT_COMMITTER_* scoped to the agent.

    Name  → agent name (e.g. "takwin")
    Email → agent+user@host (e.g. "takwin+itamar.reif@gmail.com")
    """
    result = subprocess.run(
        ["git", "config", "--global", "user.email"],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not (email := result.stdout.strip()):
        return []
    agent_email = f"{agent}+{email}"
    args = []
    for var, val in (
        ("GIT_AUTHOR_NAME", agent),
        ("GIT_COMMITTER_NAME", agent),
        ("GIT_AUTHOR_EMAIL", agent_email),
        ("GIT_COMMITTER_EMAIL", agent_email),
    ):
        args += ["-e", f"{var}={val}"]
    return args


def _git_signing_args() -> list[str]:
    """Return docker `-e` args that make in-container git SSH-sign commits.

    Mirrors the host's SSH-signing setup using Git's env-var config mechanism
    (`GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_n`/`GIT_CONFIG_VALUE_n`), which every git
    subprocess the agent spawns inherits — same approach as `_git_identity_args`.

    Only the SSH-signing path is supported (`gpg.format=ssh`); GPG hosts are a
    no-op (they'd need a keyring mounted in). We deliberately do NOT propagate the
    host's `gpg.ssh.program` (e.g. 1Password's `op-ssh-sign`, which doesn't exist
    in the image): leaving it unset makes git fall back to `ssh-keygen -Y sign`,
    which signs via the already-forwarded agent at `$SSH_AUTH_SOCK`. The signing
    key is held there, so no key material enters the image. We also skip
    `gpg.ssh.allowedSignersFile` — it's only needed to *verify* signatures locally,
    not to create them, and GitHub verifies server-side.
    """
    def _cfg(key: str) -> str:
        r = subprocess.run(
            ["git", "config", "--global", key],
            capture_output=True, text=True,
        )
        return r.stdout.strip() if r.returncode == 0 else ""

    if _cfg("commit.gpgsign").lower() != "true":
        return []
    if _cfg("gpg.format") != "ssh":
        return []
    if not (signingkey := _cfg("user.signingkey")):
        return []

    pairs = (
        ("commit.gpgsign", "true"),
        ("gpg.format", "ssh"),
        ("user.signingkey", signingkey),
    )
    args = ["-e", f"GIT_CONFIG_COUNT={len(pairs)}"]
    for i, (key, val) in enumerate(pairs):
        args += ["-e", f"GIT_CONFIG_KEY_{i}={key}", "-e", f"GIT_CONFIG_VALUE_{i}={val}"]
    return args


def _ssh_agent_args() -> list[str]:
    """Return docker `-v`/`-e` args that forward the host SSH agent for in-container git.

    Forwards the host's *default* agent — no private keys enter the image. On macOS,
    Docker Desktop and OrbStack both expose that agent inside the VM at the fixed
    `/run/host-services/ssh-auth.sock` bridge (a host socket can't be bind-mounted
    directly); on Linux `$SSH_AUTH_SOCK` is bind-mounted directly. To use a specific
    key set (e.g. 1Password's), make it your host default agent — the bridge can't
    cherry-pick a non-default agent. See docs/ssh.md.
    """
    if sys.platform == "darwin":
        src = "/run/host-services/ssh-auth.sock"  # Docker Desktop / OrbStack bridge
    else:
        src = os.environ.get("SSH_AUTH_SOCK")
        if not src:
            return []
    return ["-v", f"{src}:/ssh-agent.sock", "-e", "SSH_AUTH_SOCK=/ssh-agent.sock"]


def _terminal_args() -> list[str]:
    """Return docker `-e` args so the in-container TUI renders truecolor + events.

    `COLORTERM=truecolor` (not TERM) is what drives 24-bit color, so it's set
    unconditionally. `TERM` is forwarded from the host — inside tmux that's
    `tmux-256color` — falling back to `xterm-256color`; the matching terminfo ships
    via `ncurses-term` in the base image. Mouse/focus/bracketed-paste events are
    carried by those terminfo entries plus the host tmux's passthrough config.
    """
    term = os.environ.get("TERM") or "xterm-256color"
    return ["-e", f"TERM={term}", "-e", "COLORTERM=truecolor"]


@click.group()
def main():
    pass


@main.command("launch", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
@click.argument("toolchain")
@click.argument("agent")
@click.argument("slug")
@click.argument("project", default="-")
@click.argument("cmd_args", nargs=-1, type=click.UNPROCESSED)
def launch(toolchain, agent, slug, project, cmd_args):
    """Launch an agent session in the given toolchain container."""
    if not cmd_args:
        raise click.UsageError("cmd is required")
    cmd, *extra_args = cmd_args

    preflight.check_tools()

    no_session = slug in ("-", "")

    # --- memory (always present) ---
    agent_data = manifest.load(manifest.agent_path(agent))
    memory_path = manifest.expand_path(manifest.get(agent_data, "memory.path"))
    memory_repo = manifest.get(agent_data, "memory.repository")
    preflight.check_repo(memory_path, memory_repo, "memory")

    if no_session:
        ksession.no_session_warning()
        memory_session = memory_path
        session_name = "main"
    else:
        memory_session = ksession.ensure(memory_path, agent, slug, "agent", memory_repo)
        session_name = slug

    # --- project (optional) ---
    project_args: list = []
    cwd = str(memory_session)
    if project not in ("-", ""):
        proj_data = manifest.load(manifest.project_path(project))
        project_path_ = manifest.expand_path(manifest.get(proj_data, "path"))
        project_repo = manifest.get(proj_data, "repository")
        preflight.check_repo(project_path_, project_repo, f"project '{project}'")

        project_session = project_path_ if no_session else ksession.ensure(project_path_, agent, slug, "project", project_repo)
        project_args = [
            "-v", f"{project_session}:{project_session}:rw",
            "-e", f"KARAKUM_PROJECT={project_session}",
        ]
        cwd = str(project_session)

    # --- secrets ---
    env_dict, secret_docker_args = ksecrets.load()
    env = os.environ.copy()
    env.update(env_dict)
    env["MEMORY_SESSION"] = str(memory_session)

    # Per-agent harness state (~/.claude): a host dir, bind-mounted by compose via
    # ${CLAUDE_STATE_DIR}. Host-owned (created as the launching user == container
    # `agent` uid), so it's writable — unlike a root-owned named volume — and
    # persists across runs. We only create it and export the path.
    state_dir = config.state_root() / agent
    state_dir.mkdir(parents=True, exist_ok=True)
    env["CLAUDE_STATE_DIR"] = str(state_dir)

    # Mark onboarding complete so claude skips the first-run wizard and launches
    # straight in (auth comes from CLAUDE_CODE_OAUTH_TOKEN, not /login). Read-
    # modify-write so claude's own edits to the file are preserved.
    claude_cfg = state_dir / ".claude.json"
    try:
        cfg_data = json.loads(claude_cfg.read_text()) if claude_cfg.exists() else {}
    except (json.JSONDecodeError, OSError):
        cfg_data = {}
    if cfg_data.get("hasCompletedOnboarding") is not True:
        cfg_data["hasCompletedOnboarding"] = True
        claude_cfg.write_text(json.dumps(cfg_data, indent=2))

    # --- container name (unique per invocation to allow multiple terminals) ---
    slug_label = slug if not no_session else "main"
    container_name = f"agent-{agent}-{slug_label}-{uuid.uuid4().hex[:6]}"

    docker_cmd = [
        "docker", "compose", "run", "--rm",
        "--name", container_name,
        "-e", f"KARAKUM_SESSION={session_name}",
        "-e", f"KARAKUM_AGENT={agent}",
        "-e", f"KARAKUM_MEMORY={memory_session}",
        *project_args,
        *_git_identity_args(agent),
        *_ssh_agent_args(),
        *_git_signing_args(),
        *_terminal_args(),
        "-w", cwd,
        *secret_docker_args,
        f"agent-{toolchain}",
        cmd,
        *extra_args,
    ]

    os.chdir(manifest.karakum_root())
    os.execvpe(docker_cmd[0], docker_cmd, env)


@main.command("agents")
def agents():
    """List configured agents."""
    agents_dir = manifest.config_dir() / "agents"
    if not agents_dir.exists():
        return
    for path in sorted(agents_dir.glob("*.yaml")):
        data = manifest.load(path)
        name = manifest.get(data, "name") or path.stem
        mem_path = manifest.get(data, "memory.path") or ""
        mem_repo = manifest.get(data, "memory.repository") or ""
        print(f"{name}\t{mem_path}\t{mem_repo}")


@main.command("projects")
def projects():
    """List configured projects."""
    projects_dir = manifest.config_dir() / "projects"
    if not projects_dir.exists():
        return
    for path in sorted(projects_dir.glob("*.yaml")):
        data = manifest.load(path)
        name = manifest.get(data, "name") or path.stem
        proj_path = manifest.get(data, "path") or ""
        repo = manifest.get(data, "repository") or ""
        print(f"{name}\t{proj_path}\t{repo}")


# ---------------------------------------------------------------------------
# session command group
# ---------------------------------------------------------------------------

@main.group("session")
def session_group():
    """Manage session clones."""
    pass


@session_group.command("ls")
@click.argument("agent", required=False)
def session_ls(agent):
    """List session clones and their status (one row per clone).

    Columns: agent  label  slug  pr-state  branch
    Branch is decorated: * = dirty, ↑N = N unpushed commits.
    """
    from concurrent.futures import ThreadPoolExecutor

    found = cleanup.iter_sessions(agent)
    if not found:
        where = f" for agent '{agent}'" if agent else ""
        print(f"karakum: no sessions{where} under {config.sessions_root()}", file=sys.stderr)
        return

    all_clones = [c for s in found for c in s.clones]
    have_gh = bool(shutil.which("gh"))

    # Fetch git status for all clones in parallel, and gh PR states in one call per repo.
    with ThreadPoolExecutor(max_workers=8) as pool:
        git_futures = {pool.submit(cleanup.clone_status, c): c for c in all_clones}
        pr_future = pool.submit(cleanup.pr_states, all_clones) if have_gh else None

        git_results: dict[cleanup.Clone, tuple[bool, int]] = {}
        for fut in git_futures:
            git_results[git_futures[fut]] = fut.result()

        pr_map: dict[str, str] = pr_future.result() if pr_future else {}

    for s in found:
        for c in s.clones:
            is_dirty, ahead = git_results[c]
            branch = c.branch + ("*" if is_dirty else "") + (f"↑{ahead}" if ahead else "")
            pr = pr_map.get(c.branch, "no-pr") if have_gh else "?"
            print(f"{s.agent}\t{c.label}\t{s.slug}\t{pr}\t{branch}")


@session_group.command("rm")
@click.argument("slug")
@click.option("--dry-run", is_flag=True, help="Show what would be removed; delete nothing.")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
def session_rm(slug, dry_run, yes):
    """Delete a session directory and reap its containers."""
    all_sessions = cleanup.iter_sessions()
    matches = [s for s in all_sessions if s.slug == slug]

    if not matches:
        raise click.ClickException(f"no session with slug '{slug}'")

    if len(matches) > 1:
        lines = "\n".join(f"  {s.agent}/{s.slug}  {s.path}" for s in matches)
        raise click.ClickException(
            f"slug '{slug}' matches sessions under multiple agents:\n{lines}\n"
            f"Use 'karakum session ls' to review, then remove the specific clone directory manually."
        )

    session = matches[0]
    target = f"{session.agent}/{session.slug}  ({session.path})"

    if dry_run:
        print(f"would remove {target}")
        return

    if not yes and not click.confirm(f"Remove {target}?"):
        print("karakum: aborted", file=sys.stderr)
        return

    cleanup.remove(session)
    print(f"removed {session.agent}/{session.slug}")


# backward-compat alias: `karakum sessions` still works
main.add_command(session_ls, name="sessions")
