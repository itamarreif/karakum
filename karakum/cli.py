#!/usr/bin/env python3
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import click

from karakum import cleanup, config, manifest, preflight
from karakum import secrets as ksecrets
from karakum import session as ksession

# Container home. Every session repo mounts *under* this path so the container
# never sees host paths: scratchpad (agent memory) → ~/scratchpad, project →
# ~/<repo-name>. Matches the home of the baked `agent` account (renamed to the
# launching agent at runtime by the image entrypoint).
CONTAINER_HOME = "/home/agent"

# The bundled agent image (built by `karakum build` via `docker compose build`,
# tagged from docker-compose.yaml's `image:`). `session clean` runs over a
# session's clones inside this image so every toolchain's clean tool is present.
CLAUDE_IMAGE = "karakum-agent-claude:latest"


def _git_identity_args(agent: str) -> list[str]:
    """Return docker -e args for GIT_AUTHOR_*/GIT_COMMITTER_* scoped to the agent.

    Name  → agent name (e.g. "takwin")
    Email → user+agent in the *local part* (e.g. "itamar.reif+takwin@gmail.com")

    The `+agent` subaddress goes before the `@`, not in front of the whole address:
    plus-addressing routes on `localpart+tag@domain`, so the tag must follow the
    base username. (A leading `agent+user@host` would route to `agent@host`, an
    inbox the user doesn't own — and can't verify on GitHub.) For this address to
    link/verify a commit on GitHub, add it as a verified email there; the provider
    delivers it to the base inbox. See docs/ssh.md.
    """
    result = subprocess.run(
        ["git", "config", "--global", "user.email"],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not (email := result.stdout.strip()):
        return []
    if "@" in email:
        local, domain = email.split("@", 1)
        agent_email = f"{local}+{agent}@{domain}"
    else:
        agent_email = f"{email}+{agent}"
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
    # Mounts the project clone at ~/<repo-name>; the agent always lands in ~
    # itself (see -w below), with scratchpad + project as siblings under it.
    project_args: list = []
    if project not in ("-", ""):
        proj_data = manifest.load(manifest.project_path(project))
        project_path_ = manifest.expand_path(manifest.get(proj_data, "path"))
        project_repo = manifest.get(proj_data, "repository")
        preflight.check_repo(project_path_, project_repo, f"project '{project}'")

        project_session = project_path_ if no_session else ksession.ensure(project_path_, agent, slug, "project", project_repo)
        project_mount = f"{CONTAINER_HOME}/{Path(project_session).name}"
        project_args = [
            "-v", f"{project_session}:{project_mount}:rw",
            "-e", f"KARAKUM_PROJECT={project_mount}",
        ]

    # --- secrets ---
    env_dict, secret_docker_args = ksecrets.load()
    env = os.environ.copy()
    env.update(env_dict)
    env["MEMORY_SESSION"] = str(memory_session)
    # Where the scratchpad (memory clone) mounts inside the container (compose
    # reads MEMORY_MOUNT as the bind target; see docker-compose.yaml).
    env["MEMORY_MOUNT"] = f"{CONTAINER_HOME}/scratchpad"

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
        "-e", f"KARAKUM_MEMORY={CONTAINER_HOME}/scratchpad",
        *project_args,
        *_git_identity_args(agent),
        *_ssh_agent_args(),
        *_git_signing_args(),
        *_terminal_args(),
        "-w", CONTAINER_HOME,
        *secret_docker_args,
        f"agent-{toolchain}",
        cmd,
        *extra_args,
    ]

    os.chdir(manifest.karakum_root())
    os.execvpe(docker_cmd[0], docker_cmd, env)


@main.command("pngpaste")
@click.argument("agent")
@click.argument("slug")
@click.argument("name", default="clip.png")
def pngpaste(agent, slug, name):
    """Copy the macOS clipboard image into the <agent>/<slug> container's /tmp.

    Prints `/tmp/<name>` to hand to the agent — the supported way to get an image
    into containerized Claude, which can't read the host clipboard directly. Needs
    `pngpaste` on the host (`brew install pngpaste`).
    """
    if shutil.which("pngpaste") is None:
        raise click.ClickException("pngpaste not found — install it with `brew install pngpaste`")
    names = subprocess.run(
        ["docker", "ps", "--filter", f"name=agent-{agent}-{slug}-", "--format", "{{.Names}}"],
        capture_output=True, text=True,
    ).stdout.split()
    if not names:
        raise click.ClickException(f"no running container for {agent}/{slug}")
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        if subprocess.run(["pngpaste", f.name]).returncode != 0:
            raise click.ClickException("pngpaste failed — is there an image on the clipboard?")
        for c in names:  # copy to every matching container (e.g. multiple terminals)
            subprocess.run(["docker", "cp", f.name, f"{c}:/tmp/{name}"], check=True)
    click.echo(f"/tmp/{name}")


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


@main.command("build")
def build():
    """Build base + toolchain + agent images in tiered order.

    Tiers: base → toolchain-<lang> (thin wrappers over the canonical upstream
    images) → agent images (via `docker compose build`, which COPY --from each
    toolchain). Versions and per-toolchain tool lists come from the config dir's
    toolchains.yaml (host-owned; seed it from examples/toolchains.yaml).
    """
    preflight.check_tools()
    root = manifest.karakum_root()
    tc = manifest.load(manifest.toolchains_path())

    node_version   = manifest.get(tc, "node.version")
    node_tools     = " ".join(manifest.get(tc, "node.tools") or [])
    python_version = manifest.get(tc, "python.version")
    uv_version     = manifest.get(tc, "python.uv_version")
    rust_version    = manifest.get(tc, "rust.version")
    rust_tools      = " ".join(manifest.get(tc, "rust.tools") or [])
    rust_components = " ".join(manifest.get(tc, "rust.components") or [])
    protoc_version  = manifest.get(tc, "proto.protoc.version")
    buf_version     = manifest.get(tc, "proto.buf.version")

    def run(cmd: list[str]) -> None:
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            print(f"karakum: build failed ({' '.join(cmd)})", file=sys.stderr)
            raise SystemExit(e.returncode)

    print("karakum: building base image", file=sys.stderr)
    run(["docker", "build", "-t", "karakum-base:latest",
         "--build-arg", f"HOST_UID={os.getuid()}",
         "--build-arg", f"HOST_GID={os.getgid()}",
         str(root / "containers/base")])

    print("karakum: building toolchain images", file=sys.stderr)
    run(["docker", "build",
         "--build-arg", f"NODE_VERSION={node_version}",
         "--build-arg", f"NODE_TOOLS={node_tools}",
         "-t", "karakum-toolchain-node:latest", str(root / "containers/toolchain-node")])
    run(["docker", "build",
         "--build-arg", f"PYTHON_VERSION={python_version}",
         "--build-arg", f"UV_VERSION={uv_version}",
         "-t", "karakum-toolchain-python:latest", str(root / "containers/toolchain-python")])
    run(["docker", "build",
         "--build-arg", f"RUST_VERSION={rust_version}",
         "--build-arg", f"RUST_TOOLS={rust_tools}",
         "--build-arg", f"RUST_COMPONENTS={rust_components}",
         "-t", "karakum-toolchain-rust:latest", str(root / "containers/toolchain-rust")])
    run(["docker", "build",
         "--build-arg", f"PROTOC_VERSION={protoc_version}",
         "--build-arg", f"BUF_VERSION={buf_version}",
         "-t", "karakum-toolchain-proto:latest", str(root / "containers/toolchain-proto")])

    print("karakum: building agent images via compose", file=sys.stderr)
    os.chdir(root)
    run(["docker", "compose", "build"])


# ---------------------------------------------------------------------------
# session command group
# ---------------------------------------------------------------------------

@main.group("session")
def session_group():
    """Manage session clones."""
    pass


def _resolve_session(slug: str) -> "cleanup.Session":
    """Find the one session matching `slug`, or raise a click error.

    Shared by rm/clean/down so they have identical no-match / multi-agent
    semantics (a slug can collide across agents).
    """
    matches = [s for s in cleanup.iter_sessions() if s.slug == slug]
    if not matches:
        raise click.ClickException(f"no session with slug '{slug}'")
    if len(matches) > 1:
        lines = "\n".join(f"  {s.agent}/{s.slug}  {s.path}" for s in matches)
        raise click.ClickException(
            f"slug '{slug}' matches sessions under multiple agents:\n{lines}\n"
            f"Use 'karakum session ls' to review, then remove the specific clone directory manually."
        )
    return matches[0]


def _as_list(v) -> list[str]:
    """Normalize a YAML scalar-or-list into a list of strings."""
    return [v] if isinstance(v, str) else list(v)


def _clean_builtins(tc: dict) -> list[tuple[str, str]]:
    """(detect, clean) pairs from toolchains.yaml for entries that define `clean`.

    `detect` defaults to `true` (run unconditionally) when absent. Non-dict
    top-level values and entries without `clean` are skipped.
    """
    out: list[tuple[str, str]] = []
    for spec in tc.values():
        if isinstance(spec, dict) and (clean := spec.get("clean")):
            detect = (spec.get("detect") or "true").strip()
            out.append((detect, clean.strip()))
    return out


def _clean_map_from_projects(projects: list[dict]) -> dict[str, list[str]]:
    """Map clone label (repo basename) -> custom clean commands, from project dicts.

    The key matches the clone label `session.ensure` derives from the manifest's
    `repository` (its last path segment), so a session's project clone can look
    up its override. Projects without `clean` are omitted.
    """
    out: dict[str, list[str]] = {}
    for data in projects:
        if not (clean := data.get("clean")):
            continue
        repo = (data.get("repository") or "").rstrip("/")
        label = repo.split("/")[-1] if repo else (data.get("name") or "")
        if label:
            out[label] = _as_list(clean)
    return out


def _project_clean_map() -> dict[str, list[str]]:
    """Load every projects/*.yaml and build the label -> custom-clean map."""
    projects_dir = manifest.config_dir() / "projects"
    if not projects_dir.is_dir():
        return {}
    return _clean_map_from_projects([manifest.load(p) for p in sorted(projects_dir.glob("*.yaml"))])


def _clean_script(clones, builtins: list[tuple[str, str]],
                  custom_by_label: dict[str, list[str]]) -> str:
    """Build the tolerant bash script that cleans each clone under /work/<label>.

    A clone whose label has custom commands runs only those; otherwise the
    autodetect `builtins` run, each guarded by its detect command. Everything is
    in a subshell under `set +e`, so a failing detect / missing dir / missing npm
    script never aborts the rest of the run.
    """
    lines = ["set +e"]
    for clone in clones:
        q = shlex.quote(f"/work/{clone.label}")
        lines.append(f"echo {shlex.quote('karakum: cleaning ' + clone.label)} >&2")
        custom = custom_by_label.get(clone.label)
        if custom:
            for cmd in custom:
                lines.append(f"( cd {q} && ( {cmd} ) )")
        else:
            for detect, clean in builtins:
                lines.append(f"( cd {q} && if {detect}; then ( {clean} ); fi )")
    return "\n".join(lines)


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
    session = _resolve_session(slug)
    target = f"{session.agent}/{session.slug}  ({session.path})"

    if dry_run:
        print(f"would remove {target}")
        return

    if not yes and not click.confirm(f"Remove {target}?"):
        print("karakum: aborted", file=sys.stderr)
        return

    cleanup.remove(session)
    print(f"removed {session.agent}/{session.slug}")


@session_group.command("clean")
@click.argument("slug")
@click.option("--dry-run", is_flag=True, help="Print the clean script and docker command; run nothing.")
def session_clean(slug, dry_run):
    """Free disk by running each toolchain's clean over a session's clones.

    For every clone: a project that declares `clean:` in its manifest runs those
    commands; otherwise each toolchain in toolchains.yaml whose `detect` succeeds
    runs its `clean`. Runs inside the agent image (so cargo/npm/uv are present),
    over the host-mounted clones. Only build/dependency artifacts are removed —
    source and git state are untouched.
    """
    session = _resolve_session(slug)
    builtins = _clean_builtins(manifest.load(manifest.toolchains_path()))
    custom_by_label = _project_clean_map()

    if not builtins and not any(c.label in custom_by_label for c in session.clones):
        print("karakum: no clean commands configured", file=sys.stderr)
        return

    script = _clean_script(session.clones, builtins, custom_by_label)
    docker_cmd = [
        "docker", "run", "--rm",
        "-v", f"{session.path}:/work",
        "-w", "/work",
        CLAUDE_IMAGE,
        "bash", "-c", script,
    ]

    if dry_run:
        print(" ".join(shlex.quote(a) for a in docker_cmd))
        return

    # The image is built by `karakum build`; `docker run` can't build it on demand.
    if subprocess.run(["docker", "image", "inspect", CLAUDE_IMAGE],
                      capture_output=True, text=True).returncode != 0:
        raise click.ClickException(f"image {CLAUDE_IMAGE} not found — run `karakum build` first")

    subprocess.run(docker_cmd)
    print(f"cleaned {session.agent}/{session.slug}")


@session_group.command("down")
@click.argument("slug")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
def session_down(slug, yes):
    """Stop the running container(s) for a session (to kill a stuck one).

    Does not remove the session clone — use `session rm` for that. Containers run
    with `--rm`, so stopping them removes them.
    """
    session = _resolve_session(slug)
    names = cleanup.running_containers(session.agent, session.slug)
    if not names:
        print(f"karakum: no running containers for {session.agent}/{session.slug}", file=sys.stderr)
        return

    listed = "\n".join(f"  {n}" for n in names)
    print(f"running containers for {session.agent}/{session.slug}:\n{listed}")
    if not yes and not click.confirm(f"Stop {len(names)} container(s)?"):
        print("karakum: aborted", file=sys.stderr)
        return

    cleanup.stop_containers(names)
    print(f"stopped {len(names)} container(s)")


# backward-compat alias: `karakum sessions` still works
main.add_command(session_ls, name="sessions")
