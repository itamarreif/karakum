# karakum — agent instructions (AGENTS.md, open standard)

Container infra for AI agents.

This file uses [AGENTS.md](https://agents.md/) — the harness-agnostic convention so Claude Code, Codex, OpenCode, Cursor, Aider, Cline, etc. all read the same source of truth. No tool-specific `CLAUDE.md` / `.cursorrules` / etc. in this repo.

## Three orthogonal axes

karakum decouples three things that older agent systems conflate:

1. **CLI** = which agent you drive. The single agent image carries `claude`, `codex`, and `opencode` on `PATH`; you pick one **inside** the session shell — it is not a launch argument. (The image's build toolchains — node/python/rust/proto — are pinned in `toolchains.yaml`.)
2. **Agent** = identity. Has a name + memory (the persistent self: skills, scratchpad, master prompt). Declared in `agents/<name>.yaml`. **No** CLI field, **no** project field — agents are portable across both. Secrets are host-wide, not per-agent (declared once in `secrets.yaml`). Each CLI's state persists in a per-agent host dir under `<state_root>` (default `~/.karakum/state`): claude `~/.claude`, opencode `~/.config/opencode` + `~/.local/share/opencode`, codex `~/.codex`.
3. **Project** = the workspace the agent acts on for this session. Declared in `projects/<name>.yaml`. Optional per session. Same agent can work on different projects across sessions.

A session = (agent × project? × session-slug), with the CLI chosen at the shell. The launcher mounts the agent's memory clone and (if specified) the project clone in independent clones of their respective repos. Branches are namespaced per role: the project clone is on `<agent>/<slug>`, the memory clone on `<project>/<slug>` (or a bare `<slug>` when there's no project).

## Layout

```
karakum/                    # THIS REPO — version-controlled, generic
  containers/               Docker images: base, toolchain-* layers, and agent/ (base + toolchains + claude/codex/opencode).
  Justfile                  Host entry point: thin recipes dispatching to the CLI.
  karakum/                  Python CLI package (uv pip install -e . or uv run karakum).
    cli.py                  Entry point: launch, resume, pngpaste, build, agents, projects, session group (ls / rm / clean / down).
    manifest.py             YAML manifest loading.
    preflight.py            Docker + git repo checks.
    secrets.py              Secret resolution (op://, env://); pluggable providers.
    session.py              Per-session isolated clone lifecycle.
    cleanup.py              Session listing (iter_sessions, pr_states) + remove.
  examples/                 Genericized seed config (agents/, projects/, secrets.yaml, toolchains.yaml) → copy into the config dir.
  docker-compose.yaml       The single `agent` service (mount + env contract).
  pyproject.toml            Python package definition; deps: click, pyyaml.

$KARAKUM_CONFIG_DIR/        # YOUR CONFIG (default ~/.config/karakum) — NOT in this repo
  agents/<name>.yaml        Agent identity: name + memory.
  projects/<name>.yaml      Workspace repos the agent can act on (path + repository).
  secrets.yaml              Host-wide secret references (op://…), shared by all agents.
  toolchains.yaml           Toolchain versions + components, read by `karakum build`.
```

## Scope of this layer (the guiding list)

**In scope:**
- Building toolchain images.
- Per-session lifecycle: start (create an isolated clone + branch from the memory and project repos), resume, end (push/PR helper), sweep — realized as `just sessions` (list with git/gh status) + `just session-rm <slug>` (delete a named session).
- Manifest schemas + parsing (agent, project).
- Secret injection on session start via pluggable provider registry.
- Preflight checks (docker, manifest exists, repo state matches manifest).
- Multi-container orchestration when tool services land (`docker compose up -d`).
- Tailscale auth-key injection for ingress (#16).
- Per-container env setup (UID/GID matching, git identity, KARAKUM_* metadata).
- Debugging helpers (`just logs`, `just status`, `just doctor`).

**Out of scope (lives elsewhere):**
- The agent's own workflow → skills (`agent-session`, `issue`, `doc`, etc.) + scratchpad.
- The privileged services' internals → each is its own concern.
- Workspace-specific tooling (dewey's RAG indexer, palimpsest's memory framework, etc.) → those repos.
- The scratchpads themselves → each agent's own memory repo.
- Container hardening config → the Dockerfile + compose service definition.

## Code conventions

Two skills guide work in this repo:

- **`makefile`** — Justfile structure. Targets are 1-liners; logic delegates to `karakum/` (Python). No multi-line shell in the Justfile.
- **`secrets`** — credential methodology. `secrets.yaml` uses URI references (`op://…`, `env://…`); resolution dispatches by scheme through a pluggable registry in `karakum/secrets.py`. 1P is the default but not hardcoded.

Orchestration logic lives in the Python package (`karakum/`) — including Docker image builds (`karakum build`). There are no shell scripts. Run the CLI via `uv run karakum` or `just <recipe>`.

## Conventions specific to karakum

- Mount paths inside the container mirror host paths exactly.
- Session clones (memory + project) are bind-mounted at runtime; the host repos' `.git` is never mounted, so a session can't reach the host's branches/refs/config.
- New agent CLI = add it to `containers/agent/Dockerfile` (on `PATH`) + persist its state dir in `_do_launch` + mount it in `docker-compose.yaml`. No new service/recipe — it's one image.
- New build toolchain = new `containers/toolchain-<name>/Dockerfile` + entry in `toolchains.yaml` + COPY into `containers/agent/Dockerfile` + build step in `cli.build`.
- New agent = new `agents/<name>.yaml`. No code changes.
- New project = new `projects/<name>.yaml`. No code changes.
- New secret provider = one function + one dict entry in `karakum/secrets.py`. See the comment block there.
- **No service ever publishes ports to the host.** All ingress flows through a Tailscale sidecar.
- Tier-1 hardening (`cap_drop: ALL`, `no-new-privileges`, `read_only: true` + tmpfs, `pids_limit`, `mem_limit`) lands as a follow-up commit on the `agent` compose service.

## Don't

- Don't mount `/var/run/docker.sock`, use `network_mode: host`, or set `privileged: true`.
- Don't bake credentials into images or volumes — see the `secrets` skill.
- Don't add agent-specific logic to images. Agent-specificity lives in `agents/<name>.yaml`.
- Don't add project-specific logic to images either. Project-specificity is in `projects/<name>.yaml` plus what's in the project's own repo.
- Don't put logic in the Justfile. Extract to `karakum/` (Python), including Docker builds (`karakum build`).
- Don't add new shell scripts. Orchestration logic belongs in the Python package.
