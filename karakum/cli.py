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
    agents_dir = manifest.karakum_root() / "agents"
    for path in sorted(agents_dir.glob("*.yaml")):
        data = manifest.load(path)
        name = manifest.get(data, "name") or path.stem
        mem_path = manifest.get(data, "memory.path") or ""
        mem_repo = manifest.get(data, "memory.repository") or ""
        print(f"{name}\t{mem_path}\t{mem_repo}")


@main.command("projects")
def projects():
    """List configured projects."""
    projects_dir = manifest.karakum_root() / "projects"
    for path in sorted(projects_dir.glob("*.yaml")):
        data = manifest.load(path)
        name = manifest.get(data, "name") or path.stem
        proj_path = manifest.get(data, "path") or ""
        repo = manifest.get(data, "repository") or ""
        print(f"{name}\t{proj_path}\t{repo}")


@main.command("sessions")
@click.argument("agent", required=False)
def sessions(agent):
    """List session clones and their status (one row per clone).

    Columns: agent  label  pr-state  slug  branch
    Branch is decorated: * = dirty, ↑N = N unpushed commits.
    """
    found = cleanup.iter_sessions(agent)
    if not found:
        where = f" for agent '{agent}'" if agent else ""
        print(f"karakum: no sessions{where} under {config.sessions_root()}", file=sys.stderr)
        return
    have_gh = bool(shutil.which("gh"))
    rows = []
    for s in found:
        for c in s.clones:
            branch = c.branch
            if cleanup.dirty(c):
                branch += "*"
            ahead = cleanup.unpushed(c)
            if ahead:
                branch += f"↑{ahead}"
            pr = cleanup.pr_state(c) if have_gh else "?"
            rows.append((s.agent, c.label, pr, s.slug, branch))
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    for row in rows:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())


@main.command("clean")
@click.argument("agent", required=False)
@click.argument("slug", required=False)
@click.option("--dry-run", is_flag=True, help="Show what would be removed; delete nothing.")
@click.option("--force", is_flag=True, help="Bypass the safe-delete predicate (requires agent+slug).")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
def clean(agent, slug, dry_run, force, yes):
    """Remove session clones whose safe-delete predicate holds.

    With no args, sweeps every safe session; pass AGENT (and optionally SLUG) to
    scope. The predicate is read from ~/.karakum/config.yaml (cleanup.predicate,
    default 'merged'). --force deletes a named session regardless of predicate.
    """
    if force and not (agent and slug):
        raise click.UsageError("--force requires an explicit AGENT and SLUG")

    candidates = cleanup.iter_sessions(agent)
    if slug is not None:
        candidates = [s for s in candidates if s.slug == slug]
    if not candidates:
        print("karakum: no matching sessions", file=sys.stderr)
        return

    predicate = config.cleanup_predicate()
    if not force and predicate == "merged":
        preflight.check_gh()

    to_remove: list = []
    for s in candidates:
        if force:
            to_remove.append((s, "forced"))
            continue
        safe, reason = cleanup.session_safe(s, predicate)
        if safe:
            to_remove.append((s, reason))
        else:
            print(f"skip   {s.agent}/{s.slug}\t({reason})")

    if not to_remove:
        print("karakum: nothing safe to remove", file=sys.stderr)
        return

    verb = "would remove" if dry_run else "remove"
    for s, reason in to_remove:
        print(f"{verb} {s.agent}/{s.slug}\t({reason})\t{s.path}")
    if dry_run:
        return

    if not yes and not click.confirm(f"Delete {len(to_remove)} session(s)?"):
        print("karakum: aborted", file=sys.stderr)
        return

    for s, _ in to_remove:
        cleanup.remove(s)
        print(f"removed {s.agent}/{s.slug}")
