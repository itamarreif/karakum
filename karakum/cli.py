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

from karakum import cleanup, config, console, manifest, preflight
from karakum import secrets as ksecrets
from karakum import session as ksession

# Container home. Every session repo mounts *under* this path so the container
# never sees host paths: the memory clone (vault) → ~/<agent>, project →
# ~/<repo-name>. Matches the home of the baked `agent` account (renamed to the
# launching agent at runtime by the image entrypoint).
CONTAINER_HOME = "/home/agent"

# The bundled agent image (built by `karakum build` via `docker compose build`,
# tagged from docker-compose.yaml's `image:`). It carries three interchangeable
# agent CLIs (claude / codex / opencode) plus every toolchain, so `session clean`
# runs over a session's clones inside it with all clean tools present.
AGENT_IMAGE = "karakum-agent:latest"


def _git_identity_args(agent: str) -> list[str]:
    """Return docker -e args for GIT_AUTHOR_*/GIT_COMMITTER_* scoped to the agent.

    Name  → agent name (e.g. "alice")
    Email → user+agent in the *local part* (e.g. "dev+alice@example.com")

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


@main.command("launch")
@click.argument("agent")
@click.argument("project")
@click.argument("slug")
def launch(agent, project, slug):
    """Drop into a session shell (in ~); run claude/codex/opencode from there."""
    _do_launch(agent, project, slug)


def _do_launch(agent, project, slug):
    """Host-side prep + exec of a session container (shared by `launch`/`resume`).

    Ensures the memory clone (branch `<project>/<slug>`, or a bare `<slug>` with no
    project) and, if given, the project clone (branch `<agent>/<slug>`), wires up
    secrets / git identity / per-CLI state, then execs `docker compose run` into a
    shell. There is one agent image carrying claude/codex/opencode; the user runs
    whichever CLI they want once inside. Existing clones are reused in place, so
    this doubles as the `resume` path.
    """
    cmd = "bash"
    preflight.check_tools()

    no_session = slug in ("-", "")
    has_project = project not in ("-", "")

    # --- memory (always present) ---
    # The memory branch is namespaced by the project it serves (<project>/<slug>),
    # falling back to a bare <slug> for a memory-only session (no project).
    agent_data = manifest.load(manifest.agent_path(agent))
    memory_path = manifest.expand_path(manifest.get(agent_data, "memory.path"))
    memory_repo = manifest.get(agent_data, "memory.repository")
    # Optional per-agent setup hook: a shell command run inside the container
    # after mounts land (see entrypoint.sh + KARAKUM_MEMORY_INIT below). karakum
    # stays framework-agnostic — the manifest decides what it does (e.g. link the
    # memory framework's master prompt into ~/.claude/CLAUDE.md).
    memory_init = manifest.get(agent_data, "memory.init")
    preflight.check_repo(memory_path, memory_repo, "memory")

    if no_session:
        ksession.no_session_warning()
        memory_session = memory_path
        session_name = "main"
    else:
        memory_branch = f"{project}/{slug}" if has_project else slug
        memory_session = ksession.ensure(memory_path, agent, slug, "agent", memory_repo, memory_branch)
        session_name = slug

    # --- project (optional) ---
    # Mounts the project clone at ~/<repo-name>; the agent always lands in ~
    # itself (see -w below), with the memory clone (~/<agent>) + project as
    # siblings under it. The project branch is namespaced by the agent (<agent>/<slug>).
    project_args: list = []
    if has_project:
        proj_data = manifest.load(manifest.project_path(project))
        project_path_ = manifest.expand_path(manifest.get(proj_data, "path"))
        project_repo = manifest.get(proj_data, "repository")
        preflight.check_repo(project_path_, project_repo, f"project '{project}'")

        project_session = project_path_ if no_session else ksession.ensure(project_path_, agent, slug, "project", project_repo, f"{agent}/{slug}")
        project_mount = f"{CONTAINER_HOME}/{Path(project_session).name}"
        project_args = [
            "-v", f"{project_session}:{project_mount}:rw",
            "-e", f"KARAKUM_PROJECT={project_mount}",
        ]

    # --- secrets ---
    env_dict, secret_docker_args = ksecrets.load()
    env = os.environ.copy()
    env.update(env_dict)
    # Surface a stale GitHub token at launch rather than on the first in-container
    # `gh` call (gh authenticates solely from GH_TOKEN; git runs over SSH). Warns,
    # never blocks.
    preflight.check_github_token(env.get("GH_TOKEN"))
    env["MEMORY_SESSION"] = str(memory_session)
    # Where the memory clone (vault) mounts inside the container (compose reads
    # MEMORY_MOUNT as the bind target; see docker-compose.yaml). Mounted at
    # ~/<agent> — the clone *is* the vault root, so its own `scratchpad/` lands at
    # ~/<agent>/scratchpad (not ~/scratchpad/scratchpad).
    env["MEMORY_MOUNT"] = f"{CONTAINER_HOME}/{agent}"

    # Per-CLI persistent state. Each is a host dir under <state_root>/ (host-owned,
    # created as the launching user == container `agent` uid, so it's writable
    # unlike a root-owned named volume) bind-mounted by compose into the CLI's
    # expected location, so config/auth/history survive across --rm runs. claude
    # keeps the bare <state_root>/<agent> path (its ~/.claude); the others hang off
    # it with suffixes so nothing nests inside the claude mount.
    state_root = config.state_root()
    state_dir = state_root / agent
    for d, var in (
        (state_dir,                          "CLAUDE_STATE_DIR"),     # → ~/.claude
        (state_root / f"{agent}-opencode",   "OPENCODE_CONFIG_DIR"),  # → ~/.config/opencode
        (state_root / f"{agent}-opencode-data", "OPENCODE_DATA_DIR"), # → ~/.local/share/opencode
        (state_root / f"{agent}-codex",      "CODEX_STATE_DIR"),      # → ~/.codex
    ):
        d.mkdir(parents=True, exist_ok=True)
        env[var] = str(d)

    # Mark onboarding complete so claude skips the first-run wizard and launches
    # straight in (auth comes from CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY,
    # not /login). Read-modify-write so claude's own edits are preserved.
    claude_cfg = state_dir / ".claude.json"
    try:
        cfg_data = json.loads(claude_cfg.read_text()) if claude_cfg.exists() else {}
    except (json.JSONDecodeError, OSError):
        cfg_data = {}
    if cfg_data.get("hasCompletedOnboarding") is not True:
        cfg_data["hasCompletedOnboarding"] = True
        claude_cfg.write_text(json.dumps(cfg_data, indent=2))

    # Seed opencode's global config once so it launches straight into a usable
    # model instead of the first-run picker; opencode auto-detects the Anthropic /
    # OpenAI providers from ANTHROPIC_API_KEY / OPENAI_API_KEY in the env. Only
    # written if absent, so the user's own edits (model switches, etc.) persist.
    opencode_cfg = state_root / f"{agent}-opencode" / "opencode.json"
    if not opencode_cfg.exists():
        opencode_cfg.write_text(json.dumps({
            "$schema": "https://opencode.ai/config.json",
            "model": "anthropic/claude-sonnet-4-5",
            "autoupdate": False,
        }, indent=2))

    # --- container name (unique per invocation to allow multiple terminals) ---
    slug_label = slug if not no_session else "main"
    container_name = f"agent-{agent}-{slug_label}-{uuid.uuid4().hex[:6]}"

    # The setup hook (if any) is passed verbatim; entrypoint.sh runs it via `sh -c`
    # as the agent user after mounts land. A single image carries every CLI, so the
    # hook can wire the memory framework into all of them (see examples/agents).
    init_args = ["-e", f"KARAKUM_MEMORY_INIT={memory_init}"] if memory_init else []

    docker_cmd = [
        "docker", "compose", "run", "--rm",
        "--name", container_name,
        "-e", f"KARAKUM_SESSION={session_name}",
        "-e", f"KARAKUM_AGENT={agent}",
        "-e", f"KARAKUM_MEMORY={CONTAINER_HOME}/{agent}",
        *init_args,
        *project_args,
        *_git_identity_args(agent),
        *_ssh_agent_args(),
        *_git_signing_args(),
        *_terminal_args(),
        "-w", CONTAINER_HOME,
        *secret_docker_args,
        "agent",
        cmd,
    ]

    os.chdir(manifest.karakum_root())
    os.execvpe(docker_cmd[0], docker_cmd, env)


def _project_for_label(label: str) -> "str | None":
    """Map a session clone label (a repo basename) back to a project manifest name.

    `session.ensure` labels a project clone by its repository's last path segment;
    this reverses that through the projects manifests so `resume` can hand a name
    back to the launch path. Returns None if no manifest's repository matches.
    """
    projects_dir = manifest.config_dir() / "projects"
    if not projects_dir.is_dir():
        return None
    for path in sorted(projects_dir.glob("*.yaml")):
        repo = (manifest.get(manifest.load(path), "repository") or "").rstrip("/")
        if repo and repo.split("/")[-1] == label:
            return path.stem
    return None


@main.command("resume")
@click.argument("spec")
def resume(spec):
    """Reopen an existing session: `karakum resume <slug>` (or `<agent>/<slug>`).

    Resolves the session on disk — erroring if a bare slug exists under multiple
    agents — recovers its agent + project from the clones already there, and
    relaunches a shell into the same branches. Use `just shell <agent> <project>
    <slug>` to create a new session, or to pick one project when a session spans
    several.
    """
    session = _resolve_session(spec)

    proj_labels = [c.label for c in session.clones if c.label != "scratchpad"]
    if len(proj_labels) > 1:
        listed = ", ".join(sorted(proj_labels))
        raise click.ClickException(
            f"session {session.agent}/{session.slug} spans multiple projects ({listed}); "
            f"reopen one with 'just shell {session.agent} <project> {session.slug}'."
        )

    project = "-"
    if proj_labels:
        project = _project_for_label(proj_labels[0])
        if project is None:
            raise click.ClickException(
                f"can't map clone '{proj_labels[0]}' back to a project manifest; "
                f"reopen explicitly with 'just shell {session.agent} <project> {session.slug}'."
            )

    _do_launch(session.agent, project, session.slug)


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
@click.option("--plain", is_flag=True, default=None, help="Force plain TSV output.")
def agents(plain):
    """List configured agents."""
    agents_dir = manifest.config_dir() / "agents"
    rows = []
    if agents_dir.exists():
        for path in sorted(agents_dir.glob("*.yaml")):
            data = manifest.load(path)
            name = manifest.get(data, "name") or path.stem
            mem_path = manifest.get(data, "memory.path") or ""
            mem_repo = manifest.get(data, "memory.repository") or ""
            rows.append((name, mem_path, mem_repo))
    console.render_table(["name", "memory.path", "memory.repository"], rows, plain=plain)


@main.command("projects")
@click.option("--plain", is_flag=True, default=None, help="Force plain TSV output.")
def projects(plain):
    """List configured projects."""
    projects_dir = manifest.config_dir() / "projects"
    rows = []
    if projects_dir.exists():
        for path in sorted(projects_dir.glob("*.yaml")):
            data = manifest.load(path)
            name = manifest.get(data, "name") or path.stem
            proj_path = manifest.get(data, "path") or ""
            repo = manifest.get(data, "repository") or ""
            rows.append((name, proj_path, repo))
    console.render_table(["name", "path", "repository"], rows, plain=plain)


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
            console.error(f"build failed ({' '.join(cmd)})")
            raise SystemExit(e.returncode)

    console.info("building base image")
    run(["docker", "build", "-t", "karakum-base:latest",
         "--build-arg", f"HOST_UID={os.getuid()}",
         "--build-arg", f"HOST_GID={os.getgid()}",
         str(root / "containers/base")])

    console.info("building toolchain images")
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

    console.info("building agent images via compose")
    os.chdir(root)
    run(["docker", "compose", "build"])


# ---------------------------------------------------------------------------
# session command group
# ---------------------------------------------------------------------------

@main.group("session")
def session_group():
    """Manage session clones."""
    pass


def _resolve_session(spec: str) -> "cleanup.Session":
    """Find the one session matching `spec`, or raise a click error.

    `spec` is a bare `<slug>` or an `<agent>/<slug>` qualifier. Shared by
    resume/rm/clean/down so they have identical no-match / multi-agent semantics:
    a bare slug can collide across agents, so qualify it as `<agent>/<slug>` to
    pick one.
    """
    agent, _, slug = spec.rpartition("/")
    matches = [s for s in cleanup.iter_sessions(agent or None) if s.slug == slug]
    if not matches:
        where = f" under agent '{agent}'" if agent else ""
        raise click.ClickException(f"no session with slug '{slug}'{where}")
    if len(matches) > 1:
        lines = "\n".join(f"  {s.agent}/{s.slug}  {s.path}" for s in matches)
        raise click.ClickException(
            f"slug '{slug}' matches sessions under multiple agents:\n{lines}\n"
            f"Re-run qualified as '<agent>/{slug}' to pick one."
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


def _pr_style(pr: str) -> "str | None":
    """Color a PR-state cell (human mode only): open green, merged magenta, none dim."""
    if pr.startswith("#"):
        return "green"
    if pr == "merged":
        return "magenta"
    if pr in ("no-pr", "?"):
        return "dim"
    return None  # closed / other states keep the default color


def _branch_style(branch: str) -> "str | None":
    """Highlight a branch cell that carries a dirty (*) or unpushed (↑) marker."""
    return "yellow" if ("*" in branch or "↑" in branch) else None


@session_group.command("ls")
@click.argument("agent", required=False)
@click.option("--plain", is_flag=True, default=None, help="Force plain TSV output.")
def session_ls(agent, plain):
    """List session clones and their status (one row per clone).

    Columns: agent  label  slug  pr-state  branch
    Branch is decorated: * = dirty, ↑N = N unpushed commits.
    """
    from concurrent.futures import ThreadPoolExecutor

    found = cleanup.iter_sessions(agent)
    if not found:
        where = f" for agent '{agent}'" if agent else ""
        console.warn(f"no sessions{where} under {config.sessions_root()}")
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

    rows = []
    for s in found:
        for c in s.clones:
            is_dirty, ahead = git_results[c]
            branch = c.branch + ("*" if is_dirty else "") + (f"↑{ahead}" if ahead else "")
            pr = pr_map.get(c.branch, "no-pr") if have_gh else "?"
            rows.append((s.agent, c.label, s.slug, pr, branch))

    console.render_table(
        ["agent", "label", "slug", "pr", "branch"],
        rows,
        styles={"pr": _pr_style, "branch": _branch_style},
        plain=plain,
    )


@session_group.command("rm")
@click.argument("slug")
@click.option("--dry-run", is_flag=True, help="Show what would be removed; delete nothing.")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
def session_rm(slug, dry_run, yes):
    """Delete a session directory and reap its containers."""
    session = _resolve_session(slug)
    target = f"{session.agent}/{session.slug}  ({session.path})"

    if dry_run:
        console.info(f"would remove {target}")
        return

    if not yes and not console.confirm(f"Remove {target}?"):
        console.warn("aborted")
        return

    cleanup.remove(session)
    console.done(f"removed {session.agent}/{session.slug}")


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
        console.warn("no clean commands configured")
        return

    script = _clean_script(session.clones, builtins, custom_by_label)
    docker_cmd = [
        "docker", "run", "--rm",
        "-v", f"{session.path}:/work",
        "-w", "/work",
        AGENT_IMAGE,
        "bash", "-c", script,
    ]

    if dry_run:
        print(" ".join(shlex.quote(a) for a in docker_cmd))
        return

    # The image is built by `karakum build`; `docker run` can't build it on demand.
    if subprocess.run(["docker", "image", "inspect", AGENT_IMAGE],
                      capture_output=True, text=True).returncode != 0:
        raise click.ClickException(f"image {AGENT_IMAGE} not found — run `karakum build` first")

    subprocess.run(docker_cmd)
    console.done(f"cleaned {session.agent}/{session.slug}")


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
        console.warn(f"no running containers for {session.agent}/{session.slug}")
        return

    listed = "\n".join(f"  {n}" for n in names)
    console.info(f"running containers for {session.agent}/{session.slug}:\n{listed}")
    if not yes and not console.confirm(f"Stop {len(names)} container(s)?"):
        console.warn("aborted")
        return

    cleanup.stop_containers(names)
    console.done(f"stopped {len(names)} container(s)")


# backward-compat alias: `karakum sessions` still works
main.add_command(session_ls, name="sessions")
