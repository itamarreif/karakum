#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import click

from karakum import config, manifest, preflight
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


def _is_docker_desktop() -> bool:
    """True when the active Docker context is Docker Desktop (vs OrbStack / native)."""
    result = subprocess.run(["docker", "context", "show"], capture_output=True, text=True)
    return "desktop" in result.stdout.strip().lower()


def _ssh_agent_args(provider: str) -> list[str]:
    """Return docker `-v`/`-e` args that forward a host SSH agent for in-container git.

    `provider` (`--ssh-agent`) selects which host agent to forward; see
    `config.ssh_agent_socket`. Docker Desktop can't bind-mount a host socket, so it
    forwards the agent through its host-services bridge — which proxies the host's
    *default* agent (point that at the right agent with `just ssh-setup`). OrbStack
    and native Linux bind-mount the resolved socket directly. See docs/ssh.md.
    """
    if provider == "none":
        return []
    socket = config.ssh_agent_socket(provider)
    if not socket:
        print(f"karakum: WARNING — no SSH agent socket for provider '{provider}'; in-container git over SSH disabled.", file=sys.stderr)
        return []
    preflight.check_ssh_agent(socket)
    src = "/run/host-services/ssh-auth.sock" if _is_docker_desktop() else socket
    return ["-v", f"{src}:/ssh-agent.sock", "-e", "SSH_AUTH_SOCK=/ssh-agent.sock"]


@click.group()
def main():
    pass


@main.command("launch", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
@click.option("--ssh-agent", "ssh_agent", default="system", type=click.Choice(config.SSH_AGENT_PROVIDERS),
              help="Host SSH agent to forward for in-container git (system=$SSH_AUTH_SOCK; see docs/ssh.md).")
@click.argument("toolchain")
@click.argument("agent")
@click.argument("slug")
@click.argument("project", default="-")
@click.argument("cmd_args", nargs=-1, type=click.UNPROCESSED)
def launch(ssh_agent, toolchain, agent, slug, project, cmd_args):
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
        *_ssh_agent_args(ssh_agent),
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
