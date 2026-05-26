# karakum

Container infra for AI agents. Child of scratchpad issue #10; MVP per #14.

## Three orthogonal axes

karakum decouples:

1. **Toolchain** (which container image runs) — `containers/<toolchain>/`. Selected at invocation.
2. **Agent** (identity: memory + secrets) — `agents/<name>.yaml`. Decoupled from toolchain and project.
3. **Project** (workspace the agent acts on) — `projects/<name>.yaml`. Optional per session.

```
just <toolchain> <agent> <session-slug> [<project>]
just claude takwin fix-egress-proxy karakum    # toolchain=claude, agent=ai, project=karakum
just claude takwin organize-notes              # toolchain=claude, agent=ai, no project (memory only)
```

## Layout

```
karakum/
  containers/<toolchain>/   Docker images. Toolchain-specific.
  agents/<name>.yaml        Agent identity: name + memory + optional secrets.
  projects/<name>.yaml      Workspace repos (path + repository).
  Justfile                  Host entry point — 1-line recipes dispatching to scripts/.
  scripts/                  Real logic.
  docker-compose.yml        One service per toolchain.
```

Adding a new agent / project / toolchain is a one-file change. The three are independent.

## Prereqs

- Docker (Docker Desktop / OrbStack on macOS).
- `yq` (`brew install yq`).
- `just` (`brew install just`).
- `op` (`brew install 1password-cli`) — only if any agent manifest uses `op://` secrets.
- `shellcheck` + `shfmt` (`brew install shellcheck shfmt`) — for editing the scripts.

## Quick start

```sh
just build                                       # build base + claude images (~5-10 min)
just shell takwin login-bootstrap                    # one-time: claude /login inside; auth persists in volume
just claude takwin <slug>                            # memory-only session (note-taking, organizing, etc.)
just claude takwin <slug> <project>                  # session that also has <project> mounted RW
```

`<slug>` is required and names what the session is about. The launcher creates (or reuses) a worktree at `<repo>/.worktrees/YYYYMMDD-<slug>/` on branch `<agent>/<slug>` in **both** the memory repo and the project repo (if specified).

`just` (no args) lists all recipes; `just agents` lists configured agents; `just projects` lists configured projects.

Invoke from anywhere with a shell alias:

```sh
alias karakum='just --justfile ~/code/ai/karakum/Justfile --working-directory ~/code/ai/karakum'
karakum claude takwin try-the-mvp karakum
```

## Mount contract

Container paths mirror host paths so absolute paths stay valid across the boundary.

- **Memory worktree** at `<memory>/.worktrees/<session>/` mounted **RW** at the same path inside.
- **Project worktree** (if a project is specified) at `<project>/.worktrees/<session>/` mounted **RW** at the same path inside.
- **CWD** inside the container = the project worktree if specified, else the memory worktree.
- **`~/.claude/`** inside the container is backed by the named volume `claude-auth` so login persists across runs.
- **Env vars**: `KARAKUM_MEMORY`, `KARAKUM_PROJECT` (when set), `KARAKUM_SESSION`, `KARAKUM_AGENT`.

The agent sees **only** its memory worktree and (if specified) project worktree — nothing else from the broader filesystem. Both repos must be git repos with `origin` remotes matching the manifest's `repository` field; the launcher fails loudly otherwise.

## Secrets

Per-agent manifests declare secrets as URI references; the launcher resolves them at session start via the registered provider and injects as env vars into the container.

```yaml
# agents/takwin.yaml
secrets:
  GH_TOKEN: op://Personal/GitHub/token          # 1Password (default)
  ANTHROPIC_API_KEY: env://ANTHROPIC_API_KEY    # passthrough from host shell env
```

**Registered providers** (`scripts/lib/secrets.sh`):
- `op://<vault>/<item>/<field>` — 1Password via `op read`.
- `env://<VAR>` — read `$VAR` from the caller's shell env. No external dep.

Adding a new provider (Vault, AWS Secrets Manager, macOS keychain, …) is a one-function registration. Provider-specific tool checks are lazy; only used providers require their CLI.

See `~/code/ai/.agents/skills/secrets/SKILL.md` for methodology, anti-patterns, and quality bar.

## Ingress

No service in karakum ever publishes ports to the host. When the first listener arrives, it routes through a Tailscale sidecar per `scratchpad/issues/16-tailscale-ingress.md`.

## Pending

See followups in `~/code/ai/.agents/scratchpad/issues/14-containerization-mvp.md`: tier-1 hardening, egress proxy, per-capability tool services, SSH agent socket forwarding, isolation upgrades.
