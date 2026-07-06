# karakum configuration

How karakum splits its files across three locations, how those locations are
resolved, and what lives in each. This is the source of truth for the config
model — keep it in sync with `karakum/manifest.py` (`config_dir`, `data_dir`,
`karakum_root`) and `karakum/config.py` when you change resolution or defaults.

> For *how the CLI is structured and what happens on each launch*, see
> [`architecture.md`](architecture.md). This doc is about *where configuration
> lives*, not the dispatch flow.

## The three locations

karakum keeps three kinds of files apart, split by lifecycle:

```
# REPO (the karakum checkout) — version-controlled, generic, shareable
karakum/  containers/  docker-compose.yaml  Justfile
examples/                # genericized sample config to copy into the config dir
pyproject.toml  docs/  README.md

# CONFIG ($KARAKUM_CONFIG_DIR, default ~/.config/karakum) — small, durable, lives in dotfiles
config.yaml              # global settings (sessions_root, state_root, cleanup)
toolchains.yaml          # toolchain versions + components (seed from examples/)
agents/<name>.yaml       # per-agent: memory repo path + canonical remote
projects/<name>.yaml     # per-project: repo path + canonical remote
secrets.yaml             # secret references only (op:// / env://), never values

# DATA ($KARAKUM_DATA_DIR, default ~/.karakum) — large, regenerable, NOT in dotfiles
sessions/<agent>/<slug>/<label>/   # isolated git clones, one tree per session
state/<agent>/                     # persistent ~/.claude per agent (auth/trust/caches)
```

**Rule of thumb:**

| Location | Holds | Lifecycle |
|----------|-------|-----------|
| **Repo** | code, container builds, defaults, examples | version-controlled; generic enough to share |
| **Config** | *your* agents, projects, secret refs, settings | small, durable; belongs in your dotfiles |
| **Data** | session clones, harness state | large, machine-local, regenerable, nuked often |

The split is what lets the repo be shared: nothing machine-specific (local paths,
1Password vault coordinates) is committed, and nothing large or auth-bearing ever
lands in version control.

## Resolution & precedence

Two locations are resolved at runtime; the repo root is derived from the code's
own path.

| Location | Env var | Default | Resolver |
|----------|---------|---------|----------|
| Config dir | `KARAKUM_CONFIG_DIR` | `~/.config/karakum` | `manifest.config_dir()` |
| Data dir | `KARAKUM_DATA_DIR` | `~/.karakum` | `manifest.data_dir()` |
| Repo root | — | the checkout | `manifest.karakum_root()` (`__file__/../..`) |

Within the data dir, the two subtrees can be redirected individually from
`config.yaml`. Precedence is **env var > `config.yaml` key > built-in default**:

- `sessions_root` — defaults to `<data_dir>/sessions`; override key `sessions_root`.
- `state_root` — defaults to `<data_dir>/state`; override key `state_root`.

So `KARAKUM_DATA_DIR=/tmp/kk` relocates both subtrees at once (handy for
ephemeral/CI runs), while `config.yaml` can pin just one of them somewhere
specific.

This is XDG-ish for config (`~/.config/karakum` follows the `~/.config/<app>`
convention) but **intentionally non-XDG for data**: session clones and harness
state live under a single, obvious top-level `~/.karakum` precisely because they
get deleted often and you want them easy to find and wipe — not buried under
`~/.local/share` / `~/.local/state`.

## What each file is

| File | Purpose |
|------|---------|
| `config.yaml` | Global settings: `sessions_root`, `state_root`. All keys optional — a missing file or key falls back to defaults (`config.py`). |
| `agents/<name>.yaml` | An agent identity: `name`, `memory.path` (local memory repo), `memory.repository` (canonical remote; preflight verifies the local `origin` matches). Loaded by `manifest.load`. |
| `projects/<name>.yaml` | A project the agent acts on: `name`, `path`, `repository`. Same preflight check. Optional `clean:` (a command or list) overrides toolchain autodetect for this project's clone in `session clean` — use it for monorepos with nested packages. |
| `secrets.yaml` | A `secrets:` map of env-var name → URI reference (`op://…`, `env://…`). References only — the launcher resolves each at session start and injects `-e VAR` (name only) into the container; values never touch argv or disk. See `secrets.py` for providers. |
| `toolchains.yaml` | Toolchain versions + per-ecosystem tools and components (read by `karakum build`) plus a `detect`/`clean` command pair per toolchain (read by `session clean` to free build artifacts). Host-owned (config-dir-only, like agents/projects); seed it from `examples/toolchains.yaml`. |

Inside the **data dir**:

- `sessions/<agent>/<slug>/<label>/` — one independent `git clone` per repo a
  session touches (`label` = `scratchpad` for the agent memory repo, else the
  project name), on branch `<agent>/<slug>`. See `session.py` / `architecture.md`.
- `state/<agent>/` — the persistent `~/.claude` for the Claude Code harness, one
  per agent: OAuth/auth session, `.claude.json` (project trust, allowed tools,
  onboarding flag), and caches. Bind-mounted to `/home/agent/.claude` so it
  survives the `docker compose run --rm` container teardown. Keyed by agent so
  each agent keeps its own harness identity across sessions.

## First-run setup

karakum ships sample config under `examples/`. To set up a host:

```bash
mkdir -p ~/.config/karakum
cp -r examples/agents examples/projects examples/secrets.yaml examples/toolchains.yaml ~/.config/karakum/
# then edit:
#   agents/*.yaml    → your memory repo path + remote
#   projects/*.yaml  → your project repo paths + remotes
#   secrets.yaml     → your secret references
#   toolchains.yaml  → toolchain versions + components (optional to edit)
```

`secrets.yaml` examples default to `env://VAR` (portable — reads a host env var)
so first run works without extra tooling. Switch a reference to
`op://Vault/Item/field` to pull from 1Password instead (requires the `op` CLI).

Override behavior with env vars (e.g. in your shell profile / dotfiles):

```bash
export KARAKUM_CONFIG_DIR=~/dotfiles/karakum   # point config at your dotfiles
export KARAKUM_DATA_DIR=~/.karakum             # (default) session clones + state
```

## Migration (existing installs)

If `agents/`, `projects/`, or `secrets.yaml` lived in the repo root, or `config.yaml`
lived at `~/.karakum/config.yaml`, move them into the config dir:

```bash
mkdir -p ~/.config/karakum
mv agents projects secrets.yaml ~/.config/karakum/
[ -f ~/.karakum/config.yaml ] && mv ~/.karakum/config.yaml ~/.config/karakum/
# data (~/.karakum/sessions, ~/.karakum/state) stays put — nothing to move
```

## Install & sharing roadmap

**Now — editable install.** Put `karakum` on PATH while keeping the repo as the
home of the code and container build assets:

```bash
uv tool install --editable .
```

`--editable` means the installed `karakum` still imports from the checkout, so
`karakum_root()` continues to resolve to the repo where `docker-compose.yaml` and
`containers/` live, and `just build` / Dockerfile edits keep working. This is the
recommended setup for a personal, single-machine install.

**Future (not yet implemented):**

1. **Publish images to a registry** (e.g. GHCR) and have `docker-compose.yaml`
   `pull` instead of `build`. This is the real unlock for deployability and
   sharing: a consumer needs neither the `containers/` build contexts nor a local
   build, just `docker compose pull`. The `containers/` tree becomes a CI-only
   build input.
2. **Full wheel packaging** for `uv tool install karakum` from anywhere (no
   checkout). This is cheap *after* step 1 — once images come from a registry, the
   wheel only needs to ship the tiny `docker-compose.yaml`, not the heavy build
   contexts. Doing it before step 1 would mean bundling the build contexts into
   the wheel, which is the part that makes packaging painful, so the order
   matters: **registry first, packaging second.**
