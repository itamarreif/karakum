# karakum — agent instructions (AGENTS.md, open standard)

Container infra for AI agents. Child of scratchpad issue #10; MVP per #14.

This file uses [AGENTS.md](https://agents.md/) — the harness-agnostic convention so Claude Code, Codex, OpenCode, Cursor, Aider, Cline, etc. all read the same source of truth. No tool-specific `CLAUDE.md` / `.cursorrules` / etc. in this repo.

## Three orthogonal axes

karakum decouples three things that older agent systems conflate:

1. **Toolchain** = which container image runs (`claude`, future `codex`, `opencode`, `pi`, `secret-manager`, …). Toolchain-specific, **not** agent-specific. Selected at invocation: `just claude takwin <slug>` runs on claude; `just codex takwin <slug>` runs the same agent on codex.
2. **Agent** = identity. Has a name + memory (the persistent self: skills, scratchpad, master prompt) + state (persistent `~/.claude`). Declared in `agents/<name>.yaml`. **No** toolchain field, **no** project field — agents are portable across both. Secrets are host-wide, not per-agent (see `secrets.yaml`).
3. **Project** = the workspace the agent acts on for this session. Declared in `projects/<name>.yaml`. Optional per session. Same agent can work on different projects across sessions.

A session = (toolchain × agent × project? × session-slug). The launcher mounts the agent's memory worktree and (if specified) the project worktree, both at session branch `<agent>/<slug>` in their respective repos.

## Layout

```
karakum/
  containers/<toolchain>/   Docker images. Toolchain-specific, not agent-specific.
  agents/<name>.yaml        Agent identity: name + memory + state.
  projects/<name>.yaml      Workspace repos the agent can act on (path + repository).
  secrets.yaml              Host-wide secret references (op://…), shared by all agents.
  Justfile                  Host entry point: 1-line recipes dispatching to scripts/.
  scripts/                  Real logic (orchestrate / build / list / lib helpers).
  docker-compose.yaml       One service per toolchain.
```

## Scope of this layer (the guiding list)

**In scope:**
- Building toolchain images.
- Per-session lifecycle: start (create worktree+branch in memory and project repos), resume, end (push/PR helper), sweep (cleanup merged).
- Manifest schemas + parsing (agent, project).
- Secret injection on session start via pluggable provider registry.
- Preflight checks (yq, docker, manifest exists, repo state matches manifest).
- Multi-container orchestration when tool services land (`docker compose up -d`).
- Tailscale auth-key injection for ingress (#16).
- Per-container env setup (UID/GID matching, git identity, KARAKUM_* metadata).
- Debugging helpers (`just logs`, `just status`, `just doctor`).

**Out of scope (lives elsewhere):**
- The agent's own workflow → skills (`agent-session`, `issue`, `doc`, etc.) + scratchpad.
- The privileged services' internals → each is its own concern.
- Workspace-specific tooling (dewey's RAG indexer, palimpsest's memory framework, etc.) → those repos.
- The scratchpads themselves → individual scratchpad repos (e.g. takwin).
- Container hardening config → the Dockerfile + compose service definition.

## Code conventions

Three skills guide work in this repo:

- **`makefile`** — Justfile structure. Targets are 1-liners; logic delegates to `scripts/`.
- **`shell-script`** — bash with strict mode, shellcheck-clean. Hard ceiling: ~200 LOC per script; escalate to Python/Rust beyond.
- **`secrets`** — credential methodology. Manifests use URI references (`op://…`, `env://…`); resolution dispatches by scheme through a pluggable registry. 1P is the default but not hardcoded.

## Conventions specific to karakum

- Mount paths inside the container mirror host paths exactly.
- Worktrees (memory + project) are bind-mounted at runtime; the underlying repos are never bind-mounted directly.
- New toolchain = new `containers/<name>/Dockerfile` + new compose service + new Justfile recipe. No agent or project changes.
- New agent = new `agents/<name>.yaml`. No code changes.
- New project = new `projects/<name>.yaml`. No code changes.
- **No service ever publishes ports to the host.** All ingress flows through a Tailscale sidecar (#16).
- Tier-1 hardening (`cap_drop: ALL`, `no-new-privileges`, `read_only: true` + tmpfs, `pids_limit`, `mem_limit`) lands as a follow-up commit on the toolchain image / compose service.

## Don't

- Don't mount `/var/run/docker.sock`, use `network_mode: host`, or set `privileged: true`.
- Don't bake credentials into images or volumes — see the `secrets` skill.
- Don't add agent-specific logic to images. Agent-specificity lives in `agents/<name>.yaml`.
- Don't add project-specific logic to images either. Project-specificity is in `projects/<name>.yaml` plus what's in the project's own repo.
- Don't put multi-line shell into the Justfile. Extract to `scripts/`.
- Don't let a shell script exceed ~200 LOC. Escalate per the `shell-script` skill.

## Planning context

- `~/code/ai/.agents/scratchpad/issues/14-containerization-mvp.md` — MVP build order, deferred hardening, followups.
- `~/code/ai/.agents/scratchpad/issues/16-tailscale-ingress.md` — ingress architecture.
- `~/code/ai/.agents/scratchpad/docs/4-agent-session-workflow.md` — per-session PR workflow.
