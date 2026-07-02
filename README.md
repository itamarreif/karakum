![Darvaza gas crater](https://images.squarespace-cdn.com/content/v1/590dd4905016e187a50516d7/1565763159832-7E51CX2CI87SNL8NCFC7/View+of+Darvaza+Gas+Crater+at+Night?format=2500w)

# karakum

Container infra for AI agents.

## Three orthogonal axes

karakum decouples:

1. **Toolchain** (which container image runs) — `containers/<toolchain>/`. Selected at invocation.
2. **Agent** (identity: memory) — `<config-dir>/agents/<name>.yaml`. Decoupled from toolchain and project.
3. **Project** (workspace the agent acts on) — `<config-dir>/projects/<name>.yaml`. Optional per session.

```
just shell <agent> <project> <slug>
just shell alice webapp fix-login    # agent=alice on project=webapp, session slug=fix-login
just shell alice - organize-notes    # no project (memory only) — '-' skips the project
just shell alice - -                 # no slug → runs on main branch (with disclaimer)
```

## Three locations

karakum splits its files across three locations, by lifecycle. Full model in
[docs/configuration.md](docs/configuration.md).

```
# REPO (this checkout) — version-controlled, generic, shareable
karakum/  containers/  docker-compose.yaml  Justfile
examples/                Genericized sample config to copy into the config dir.

# CONFIG ($KARAKUM_CONFIG_DIR, default ~/.config/karakum) — your settings, dotfiles-friendly
config.yaml              Global settings (sessions_root, state_root, cleanup).
toolchains.yaml          Toolchain versions + components (seed from examples/).
agents/<name>.yaml       Agent identity: name + memory repo path + remote.
projects/<name>.yaml     Workspace repos (path + repository).
secrets.yaml             Host-wide secret references (op://…, env://…), never values.

# DATA ($KARAKUM_DATA_DIR, default ~/.karakum) — large, regenerable, NOT in dotfiles
sessions/<agent>/<slug>/<label>/   Isolated git clones, one tree per session.
state/<agent>/                     Persistent ~/.claude per agent (auth/trust/caches).
```

The split lets the repo be shared: nothing machine-specific (local paths,
1Password coordinates) is committed, and nothing large or auth-bearing lands in
version control. Within the data dir, `config.yaml` can redirect `sessions_root` /
`state_root` individually; precedence per subtree is
**env var (`$KARAKUM_DATA_DIR`) > `config.yaml` key > built-in default**.

### Repo layout

```
karakum/
  containers/<toolchain>/   Docker images. Toolchain-specific.
  examples/                 Sample agents/, projects/, secrets.yaml, toolchains.yaml — copy into config dir.
  Justfile                  Host entry point — thin recipes dispatching to the CLI.
  karakum/                  Python CLI package (install with `just install`).
    cli.py                  Click entry point: launch, build, agents, projects, session group.
    manifest.py             YAML manifest loading + location resolvers.
    config.py               Optional host settings (config.yaml) + session/state roots.
    preflight.py            Docker + git repo checks.
    secrets.py              Secret resolution (op://, env://).
    session.py              Per-session isolated clone lifecycle.
    cleanup.py              Session enumeration + remove logic.
  docker-compose.yaml       One service per toolchain.
  docs/architecture.md      CLI structure + per-command call graphs.
  docs/configuration.md     The three-location config model.
  pyproject.toml            Python package definition.
```

Adding a new agent / project / toolchain is a one-file change in the config dir.
The three are independent.

For how the CLI is wired together — modules, command dispatch, and the `launch` call graph — see [docs/architecture.md](docs/architecture.md).

## Prereqs

- Docker (Docker Desktop / OrbStack on macOS).
- `uv` (`brew install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`).
- `just` (`brew install just`).
- `op` (`brew install 1password-cli`) — only if any agent manifest uses `op://` secrets.
- `gh` (`brew install gh`) — only for `just sessions` pr-state column (it queries GitHub for PR status).

## First-run setup

karakum reads your agents, projects, and secret references from the **config dir**
(`$KARAKUM_CONFIG_DIR`, default `~/.config/karakum`). Seed it from the genericized
samples in `examples/`, then edit:

```sh
mkdir -p ~/.config/karakum
cp -r examples/agents examples/projects examples/secrets.yaml examples/toolchains.yaml ~/.config/karakum/
# then edit:
#   ~/.config/karakum/agents/*.yaml    → your memory repo path + remote
#   ~/.config/karakum/projects/*.yaml  → your project repo paths + remotes
#   ~/.config/karakum/secrets.yaml     → your secret references
#   ~/.config/karakum/toolchains.yaml  → toolchain versions + components (optional to edit)
```

The sample `secrets.yaml` uses `env://VAR` references (portable — reads a host env
var) so first run works without extra tooling. Switch any reference to
`op://Vault/Item/field` to pull from 1Password instead (requires the `op` CLI).

Override the default locations with env vars (e.g. in your shell profile):

```sh
export KARAKUM_CONFIG_DIR=~/dotfiles/karakum   # point config at your dotfiles
export KARAKUM_DATA_DIR=~/.karakum             # (default) session clones + harness state
```

## Quick start

```sh
just install                                         # install the karakum CLI (once, or after edits)
# or, to put `karakum` on PATH while keeping the repo as the home of the code:
uv tool install --editable .                         # editable install — still imports from this checkout
just build                                           # build base + claude images (~5-10 min)
claude setup-token                                   # one-time (host): make an OAuth token → reference as CLAUDE_CODE_OAUTH_TOKEN in secrets.yaml
just shell <agent> - <slug>                          # memory-only session (note-taking, organizing, etc.)
just shell <agent> <project> <slug>                  # session that also has <project> mounted RW
just shell <agent> - -                               # no slug: run on main branch (shows disclaimer)
```

The container is the `claude` toolchain image, so `claude` is on `PATH` — `just
shell` drops you at a prompt in the session home; run `claude` there to start the
agent, or work in the shell directly.

`uv tool install --editable .` keeps the installed `karakum` importing from this
checkout, so `karakum_root()` still resolves to the repo where
`docker-compose.yaml` and `containers/` live — `just build` and Dockerfile edits
keep working. Recommended for a personal, single-machine install.

`<slug>` names what the session is about. The launcher creates (or reuses) an **isolated clone** at `<sessions_root>/<agent>/<slug>/<label>` for **both** the memory repo (`label` = `scratchpad`) and the project repo (`label` = the project name), if specified. The two clones sit together under `<agent>/<slug>` but check out **differently namespaced branches**: the project clone is on `<agent>/<slug>` (whose changes are the agent's), while the memory clone is on `<project>/<slug>` — so a memory repo shared across projects keeps each project's session work on its own branch. (With no project, the memory branch is just `<slug>`.) Grouping by session keeps every repo a session touches together, and living outside the repos means it never collides with a manual `git worktree add`. Each clone is fully independent (its own `.git`, no shared objects), so the agent can never touch the host repo's git database; its `origin` points at GitHub, so commits reach the host via push + pull/PR, not a shared `.git`. The slug is the stable session identity — resuming the same slug (even on a later day) reuses the same clone and branch. Omitting the slug (`-`) skips cloning and mounts the live main branch directly — a warning is printed since changes affect the repo immediately.

> `<sessions_root>` defaults to `<data_dir>/sessions` (`$KARAKUM_DATA_DIR`, default `~/.karakum`). Override it by setting `sessions_root` in `<config-dir>/config.yaml`, or relocate the whole data dir with `$KARAKUM_DATA_DIR`. Because clones live there (not inside the repos), no per-repo `.gitignore` entry is needed.

Multiple terminals can open the **same slug** concurrently; each gets a unique container name so Docker doesn't conflict.

`just` (no args) lists all recipes; `just agents` lists configured agents; `just projects` lists configured projects.

### Listing & cleaning up sessions

Session clones persist after the container exits, so they accumulate. Four commands manage them:

```sh
just sessions [<agent>]                      # list session clones + status (one row per clone)
just session-rm <slug> [--dry-run] [--yes]   # delete a session directory
just session-clean <slug> [--dry-run]        # free build artifacts (target/, node_modules, …) without deleting the clone
just session-down <slug> [--yes]             # stop a stuck session's container(s) without deleting the clone
```

`just sessions` (alias: `karakum session ls`) prints one row per clone:

```
agent   label       slug       pr-state   branch
alice   scratchpad  fix-login   #12       webapp/fix-login*
alice   webapp      fix-login   #12       alice/fix-login↑2
```

The branch column folds in dirty (`*`) and unpushed (`↑N`) state; note the two clones of a session carry differently namespaced branches (memory on `<project>/<slug>`, project on `<agent>/<slug>`). The pr-state column queries GitHub via `gh` (`#N` for an open PR, else the state, `no-pr` if none); without `gh` it shows `?`.

`just session-rm <slug>` (alias: `karakum session rm <slug>`) deletes the entire session directory and reaps any exited `agent-<agent>-<slug>-*` containers. If the slug matches clones under multiple agents it prints them and asks you to disambiguate.

Flags: `--dry-run` (show what would be removed, delete nothing), `--yes` (skip the confirmation prompt).

`just session-clean <slug>` reclaims disk **without** deleting the clone — handy when several sessions fill the disk and `session-rm` is too destructive for in-progress work. It runs each toolchain's clean command inside the agent image over the session's clones (so `cargo`/`npm`/`uv` are available), removing only regenerable build artifacts — source and git state are untouched. For each clone, a project that declares a `clean:` list in its manifest runs exactly those commands; otherwise every toolchain in `toolchains.yaml` whose `detect` command succeeds (e.g. `test -f Cargo.toml`) runs its `clean` (e.g. `cargo clean`). Use a project `clean:` for monorepos whose build dirs are nested (e.g. `cd webapp && npm run clean …`), since autodetect only checks the clone root. `--dry-run` prints the generated script and docker command without running anything. (Requires `just build` first, so the `karakum-agent-claude` image exists.)

`just session-down <slug>` stops the running `agent-<agent>-<slug>-*` container(s) — for killing a stuck or runaway session (e.g. a type-checker spinning for too long) — without deleting the clone. Containers run with `--rm`, so stopping them removes them. Confirms first; `--yes` skips the prompt.

Invoke from anywhere with a shell alias:

```sh
alias karakum='just --justfile ~/path/to/karakum/Justfile --working-directory ~/path/to/karakum'
karakum shell alice webapp try-the-mvp
```

## Mount contract

Session clones mount **under the container home (`~`)**, never at their host paths, so the container is unaware of the external filesystem and the prompt stays clean (`alice:~ $`). The host path the clone lives at is an implementation detail the agent never sees.

- **Memory session clone** (the "scratchpad") at host `<sessions_root>/<agent>/<slug>/scratchpad/`, mounted **RW** at `~/scratchpad`.
- **Project session clone** (if a project is specified) at host `<sessions_root>/<agent>/<slug>/<project>/`, mounted **RW** at `~/<project>` (the repo name).
- **CWD** inside the container = `~` (home); scratchpad and project sit as siblings under it.
- **User**: the baked `agent` account is renamed at runtime to the launching agent (e.g. `alice`) by the image entrypoint, so `whoami`/`\u`/new-file ownership read the agent name. Home stays `/home/agent`.
- **`~/.claude/`** inside the container is bind-mounted from a per-agent host dir `<state_root>/<agent>` (default `<data_dir>/state`, i.e. `~/.karakum/state`), so settings/trust/history persist across runs and the dir stays host-owned (agent-writable, inspectable).
- **Env vars**: `KARAKUM_MEMORY` (`~/scratchpad`), `KARAKUM_PROJECT` (`~/<project>`, when set), `KARAKUM_SESSION`, `KARAKUM_AGENT`.

The agent sees **only** its memory clone and (if specified) project clone — nothing else from the broader filesystem. Crucially, the **host repos' `.git` directories are never mounted**: each session is a standalone clone, so the agent cannot read or rewrite the host's branches, refs, config, or hooks. Both source repos must be git repos with `origin` remotes matching the manifest's `repository` field; the launcher fails loudly otherwise, and repoints each clone's `origin` at that remote so the agent pushes to GitHub.

### Git auth

In-container `git push`/`pull` authenticate over SSH via your **forwarded host SSH agent** — no private keys enter the image, and it's automatic (no flag). The container uses whatever your host *default* agent holds; commits are attributed to the agent and, when your host SSH-signs commits, signed by that same forwarded key. Works on macOS (Docker Desktop and OrbStack, via the host-services bridge) and native Linux. To use 1Password keys, make the 1Password agent your default. Details + verification: [`docs/ssh.md`](docs/ssh.md).

## Secrets

Secrets are declared **host-wide** in `<config-dir>/secrets.yaml` as URI references, shared across all agents and toolchains. The launcher resolves each at session start via the registered provider and injects them as env vars (`-e VAR`, name only — the value never touches the command line) into the container.

```yaml
# <config-dir>/secrets.yaml
secrets:
  GH_TOKEN: env://GH_TOKEN                        # passthrough from host shell env (portable)
  CLAUDE_CODE_OAUTH_TOKEN: op://Personal/karakum claude code oauth token/credential  # 1Password
```

Claude Code authenticates from `CLAUDE_CODE_OAUTH_TOKEN` (above) — interactive `/login` doesn't work reliably in the container. The launcher also seeds `hasCompletedOnboarding` in the per-agent `~/.claude` state dir so claude skips the first-run wizard and starts straight in; settings, trusted folders, and history then persist in that host dir across runs.

**Registered providers** (`karakum/secrets.py`):
- `op://<vault>/<item>/<field>` — 1Password via `op read`.
- `env://<VAR>` — read `$VAR` from the caller's shell env. No external dep.

Adding a new provider (Vault, AWS Secrets Manager, macOS keychain, …) is a one-function registration. Provider-specific tool checks are lazy; only used providers require their CLI.

## Ingress

No service in karakum ever publishes ports to the host. When the first listener arrives, it will route through a Tailscale sidecar.

## Pending

Planned followups: tier-1 hardening (`cap_drop`, `no-new-privileges`, read-only rootfs + tmpfs, `pids_limit`/`mem_limit`), an egress proxy, per-capability tool services, and further isolation upgrades.
