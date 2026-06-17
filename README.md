# karakum

Container infra for AI agents. Child of scratchpad issue #10; MVP per #14.

## Three orthogonal axes

karakum decouples:

1. **Toolchain** (which container image runs) — `containers/<toolchain>/`. Selected at invocation.
2. **Agent** (identity: memory) — `agents/<name>.yaml`. Decoupled from toolchain and project.
3. **Project** (workspace the agent acts on) — `projects/<name>.yaml`. Optional per session.

```
just <toolchain> <agent> [<session-slug>] [<project>]
just claude takwin fix-egress-proxy karakum    # toolchain=claude, agent=takwin, project=karakum
just claude takwin organize-notes              # toolchain=claude, agent=takwin, no project (memory only)
just claude takwin                             # no slug → runs on main branch (with disclaimer)
```

## Layout

```
karakum/
  containers/<toolchain>/   Docker images. Toolchain-specific.
  agents/<name>.yaml        Agent identity: name + memory.
  projects/<name>.yaml      Workspace repos (path + repository).
  secrets.yaml              Host-wide secret references (op://…), shared by all agents.
  Justfile                  Host entry point — thin recipes dispatching to the CLI.
  karakum/                  Python CLI package (install with `just install`).
    cli.py                  Click entry point: launch, agents, projects.
    manifest.py             YAML manifest loading.
    preflight.py            Docker + git repo checks.
    secrets.py              Secret resolution (op://, env://).
    session.py              Per-session isolated clone lifecycle.
  scripts/build.sh          Docker image build script.
  docker-compose.yaml       One service per toolchain.
  docs/architecture.md      CLI structure + per-command call graphs.
  pyproject.toml            Python package definition.
```

Adding a new agent / project / toolchain is a one-file change. The three are independent.

For how the CLI is wired together — modules, command dispatch, and the `launch` call graph — see [docs/architecture.md](docs/architecture.md).

## Prereqs

- Docker (Docker Desktop / OrbStack on macOS).
- `uv` (`brew install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`).
- `just` (`brew install just`).
- `op` (`brew install 1password-cli`) — only if any agent manifest uses `op://` secrets.

## Quick start

```sh
just install                                         # install the karakum CLI (once, or after edits)
just build                                           # build base + claude images (~5-10 min)
claude setup-token                                   # one-time (host): make an OAuth token → store in 1Password → reference as CLAUDE_CODE_OAUTH_TOKEN in secrets.yaml
just claude takwin <slug>                            # memory-only session (note-taking, organizing, etc.)
just claude takwin <slug> <project>                  # session that also has <project> mounted RW
just claude takwin                                   # no slug: run on main branch (shows disclaimer)
```

`<slug>` names what the session is about. The launcher creates (or reuses) an **isolated clone** at `<sessions_root>/<agent>/<slug>/<label>` on branch `<agent>/<slug>` for **both** the memory repo (`label` = `scratchpad`) and the project repo (`label` = the project name), if specified. Grouping by session keeps every repo a session touches together, and living outside the repos means it never collides with a manual `git worktree add`. Each clone is fully independent (its own `.git`, no shared objects), so the agent can never touch the host repo's git database; its `origin` points at GitHub, so commits reach the host via push + pull/PR, not a shared `.git`. The slug is the stable session identity — resuming the same slug (even on a later day) reuses the same clone and branch. Omitting the slug skips cloning and mounts the live main branch directly — a warning is printed since changes affect the repo immediately.

> `<sessions_root>` defaults to `~/.karakum/sessions`. Override it by setting `sessions_root` in `~/.karakum/config.yaml`. Because clones live there (not inside the repos), no per-repo `.gitignore` entry is needed.

Multiple terminals can open the **same slug** concurrently; each gets a unique container name so Docker doesn't conflict.

`just` (no args) lists all recipes; `just agents` lists configured agents; `just projects` lists configured projects.

Invoke from anywhere with a shell alias:

```sh
alias karakum='just --justfile ~/code/ai/karakum/Justfile --working-directory ~/code/ai/karakum'
karakum claude takwin try-the-mvp karakum
```

## Mount contract

Container paths mirror host paths so absolute paths stay valid across the boundary.

- **Memory session clone** at `<sessions_root>/<agent>/<slug>/scratchpad/` mounted **RW** at the same path inside.
- **Project session clone** (if a project is specified) at `<sessions_root>/<agent>/<slug>/<project>/` mounted **RW** at the same path inside.
- **CWD** inside the container = the project clone if specified, else the memory clone.
- **`~/.claude/`** inside the container is bind-mounted from a per-agent host dir `<state_root>/<agent>` (default `~/.karakum/state`), so settings/trust/history persist across runs and the dir stays host-owned (agent-writable, inspectable).
- **Env vars**: `KARAKUM_MEMORY`, `KARAKUM_PROJECT` (when set), `KARAKUM_SESSION`, `KARAKUM_AGENT`.

The agent sees **only** its memory clone and (if specified) project clone — nothing else from the broader filesystem. Crucially, the **host repos' `.git` directories are never mounted**: each session is a standalone clone, so the agent cannot read or rewrite the host's branches, refs, config, or hooks. Both source repos must be git repos with `origin` remotes matching the manifest's `repository` field; the launcher fails loudly otherwise, and repoints each clone's `origin` at that remote so the agent pushes to GitHub.

`git push`/`pull` authenticate over SSH using the **host's SSH agent**, forwarded into the container — Docker Desktop exposes it at `/run/host-services/ssh-auth.sock`, which compose bind-mounts and points `SSH_AUTH_SOCK` at. The image installs `openssh-client` and pins GitHub's host key; **no private keys are ever copied in**, only the agent socket is forwarded. Author/committer identity is independently set to the agent (`GIT_AUTHOR_*`/`GIT_COMMITTER_*`), so commits are attributed to the agent while the push is signed by the host key.

## Secrets

Secrets are declared **host-wide** in `secrets.yaml` as URI references, shared across all agents and toolchains. The launcher resolves each at session start via the registered provider and injects them as env vars (`-e VAR`, name only — the value never touches the command line) into the container.

```yaml
# secrets.yaml
secrets:
  GH_TOKEN: op://Personal/karakum gh pat/token   # 1Password (default)
  ANTHROPIC_API_KEY: env://ANTHROPIC_API_KEY      # passthrough from host shell env
```

Claude Code authenticates from `CLAUDE_CODE_OAUTH_TOKEN` (above) — interactive `/login` doesn't work reliably in the container. The launcher also seeds `hasCompletedOnboarding` in the per-agent `~/.claude` state dir so claude skips the first-run wizard and starts straight in; settings, trusted folders, and history then persist in that host dir across runs.

**Registered providers** (`karakum/secrets.py`):
- `op://<vault>/<item>/<field>` — 1Password via `op read`.
- `env://<VAR>` — read `$VAR` from the caller's shell env. No external dep.

Adding a new provider (Vault, AWS Secrets Manager, macOS keychain, …) is a one-function registration. Provider-specific tool checks are lazy; only used providers require their CLI.

See `~/code/ai/.agents/skills/secrets/SKILL.md` for methodology, anti-patterns, and quality bar.

## Ingress

No service in karakum ever publishes ports to the host. When the first listener arrives, it routes through a Tailscale sidecar per `scratchpad/issues/16-tailscale-ingress.md`.

## Pending

See followups in `~/code/ai/.agents/scratchpad/issues/14-containerization-mvp.md`: tier-1 hardening, egress proxy, per-capability tool services, isolation upgrades.
