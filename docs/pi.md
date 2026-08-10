# pi (pi.dev) setup

[`pi`](https://pi.dev) is one of the four agent CLIs on `PATH` in the agent image
(alongside `claude`, `codex`, and `opencode`). You pick it inside the session shell
by running `pi` — it is not a launch argument. This page covers how pi is wired and
how to authenticate it for the two common cases.

## How pi is wired

- **Install** — `@earendil-works/pi-coding-agent` (CLI: `pi`), on its own Dockerfile
  layer because its install uses the global `--ignore-scripts` npm flag.
- **State** — pi consolidates everything (config, sessions, and, if you ever run
  interactive `/login`, `auth.json`) under `~/.pi/agent` — it does **not** follow the
  XDG split like opencode. So it's a single per-agent host mount:
  `<state_root>/<agent>-pi` → `~/.pi`. karakum enforces **no** settings on pi (unlike
  opencode, which is seeded a default model): you pick a model with `/model`, and pi
  writes that choice — plus sessions, trust, etc. — to `~/.pi/agent/settings.json`,
  which persists in the mount across sessions for that agent.
- **Auth** — entirely **env-injected via the secrets pipeline**. Nothing interactive,
  nothing written to disk. pi reads (see its `packages/ai` provider code):

  | Env var | How pi sends it | Use |
  |---|---|---|
  | `ANTHROPIC_OAUTH_TOKEN` | `authToken` + `anthropic-beta: oauth-2025-04-20` | **Claude Pro/Max subscription** |
  | `ANTHROPIC_API_KEY` | `x-api-key` | Anthropic API billing |
  | `OPENAI_API_KEY` | OpenAI key | OpenAI API billing |

  Because the launcher forwards secrets as `-e VAR` (name only on argv, value from the
  launcher's own env) and pi never persists an env-provided token, **no credential
  lands on the host disk** — the same no-secrets-at-rest model as claude's
  `CLAUDE_CODE_OAUTH_TOKEN`. The `~/.pi` mount holds only your own state (settings,
  sessions, trust) — nothing karakum writes.

Auth is selected per host in `<config-dir>/secrets.yaml` (see
[configuration](configuration.md) and [`examples/secrets.yaml`](../examples/secrets.yaml)).

## Wiring the agent's master prompt (scratchpad)

pi reads a global context file from its agent dir — the candidates are
`AGENTS.override.md`, then `AGENTS.md` (see pi's `resource-loader`). So pi's slot in
the master-prompt convention is **`~/.pi/agent/AGENTS.md`**, the exact analog of
claude's `~/.claude/CLAUDE.md`.

Link it from the agent's `memory.init` hook in `agents/<name>.yaml`, alongside the
other CLIs (karakum runs the hook verbatim after mounts land; it stays
framework-agnostic, so the link lives in the manifest, not in code):

```yaml
memory:
  path: ~/code/you/your-memory-repo
  repository: github.com/you/your-memory-repo
  init: |
    mkdir -p "$HOME/.pi/agent"
    ln -sfn "$KARAKUM_MEMORY/MASTER_PROMPT.md" "$HOME/.claude/CLAUDE.md"
    ln -sfn "$KARAKUM_MEMORY/MASTER_PROMPT.md" "$HOME/.config/opencode/AGENTS.md"
    ln -sfn "$KARAKUM_MEMORY/MASTER_PROMPT.md" "$HOME/.codex/AGENTS.md"
    ln -sfn "$KARAKUM_MEMORY/MASTER_PROMPT.md" "$HOME/.pi/agent/AGENTS.md"
    ln -sfn "$KARAKUM_MEMORY/skills"           "$HOME/.pi/agent/skills"
```

The launcher also creates `~/.pi/agent` on every run, but the `mkdir -p` keeps the hook
**self-contained** — it works even on a karakum that predates that change, and doesn't
rely on launcher internals. Without it, on a missing `~/.pi/agent` both pi links fail
silently (`memory.init` is non-fatal) and you get the confusing half-state where only
claude is wired. The links are re-made each session (`ln -sfn` is idempotent), so
rebuilding re-wires them.

### Why `AGENTS.md`, not `SYSTEM.md`

pi has three injection points (see `resource-loader.ts`):

| File (global / project) | Effect on the prompt |
|---|---|
| `AGENTS.md` / `.pi/AGENTS.md` | Loaded as a **context file** — layered on top of pi's built-in system prompt. |
| `APPEND_SYSTEM.md` / `.pi/APPEND_SYSTEM.md` | **Appended** to pi's built-in system prompt (built-ins kept). |
| `SYSTEM.md` / `.pi/SYSTEM.md` | **Replaces** pi's built-in system prompt entirely. |

We link the master prompt as `AGENTS.md` because it's a persona/workflow layer meant to
sit *on top of* the harness, and it's the **only cross-CLI convention** (claude
`CLAUDE.md`, opencode/codex `AGENTS.md`) — one file links identically for all four.
`SYSTEM.md` would throw away pi's built-in tool/format/behavior instructions (breaking
the agent unless you reproduce them) and is pi-only. `APPEND_SYSTEM.md` is a reasonable
pi-only escalation if you ever need the master prompt enforced at system-prompt strength
rather than as context — but it doesn't replace the `AGENTS.md` link, it's additive.
(Project-scoped `SYSTEM.md`/`APPEND_SYSTEM.md` also require the project to be trusted.)

## Wiring skills

pi discovers skills from `~/.pi/agent/skills/` (global) and `.pi/skills/` (project) —
any subdirectory containing a `SKILL.md` is a skill (see pi's
[skills.md](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/skills.md)).
The vault's `skills/<name>/SKILL.md` layout is already exactly this format (required
frontmatter: `name`, `description`), so no conversion is needed — just point pi's
skills dir at the vault with the symlink shown in the `memory.init` block above:

```sh
ln -sfn "$KARAKUM_MEMORY/skills" "$HOME/.pi/agent/skills"
```

pi then surfaces each skill's `description` in the system prompt (auto-loaded when a
task matches) and exposes `/skill:<name>` for direct invocation. Extra frontmatter the
vault uses (e.g. `user-invocable`) is ignored by pi; only `name`/`description` are
required. This is the pi analog of claude's `~/.claude/skills/`.

## Personal machine — Claude subscription

`~/.config/karakum/secrets.yaml`:

```yaml
secrets:
  CLAUDE_CODE_OAUTH_TOKEN: op://Personal/karakum claude code oauth token/credential  # claude
  ANTHROPIC_OAUTH_TOKEN:   op://Personal/karakum claude code oauth token/credential  # pi (same token)
  # deliberately NO ANTHROPIC_API_KEY — so both claude and pi bill the subscription
  GH_TOKEN: op://Personal/GitHub/token
```

Both point at the **same** 1Password item — one `sk-ant-oat` token (the kind
`claude setup-token` mints) drives claude *and* pi on your Pro/Max plan. pi sends the
`anthropic-beta: oauth-2025-04-20` header; nothing hits disk.

## Work machine — API keys for both

`~/.config/karakum/secrets.yaml`:

```yaml
secrets:
  ANTHROPIC_API_KEY: op://Work/Anthropic/key   # claude, opencode, pi
  OPENAI_API_KEY:    op://Work/OpenAI/key       # codex, opencode, pi
  GH_TOKEN: op://Work/GitHub/token
```

pi reads these as `x-api-key`. No extra pi-specific entry needed — it's covered by the
same keys the other CLIs use.

## Verify

After `just build`, drop into a session and confirm pi is authenticated:

```bash
just shell <agent> - pi-smoke
# inside the container:
command -v pi                 # -> /usr/local/bin/pi
pi --version                  # CLI is on PATH
# personal: confirm the subscription token is present (do NOT print its value)
[ -n "$ANTHROPIC_OAUTH_TOKEN" ] && echo "oauth token injected"
# work: confirm the API keys are present
[ -n "$ANTHROPIC_API_KEY" ] && [ -n "$OPENAI_API_KEY" ] && echo "api keys injected"
pi "reply with the single word: ok"   # end-to-end: a real model round-trip
```

A successful `pi` round-trip on the personal config proves the subscription path
(`ANTHROPIC_OAUTH_TOKEN` → `oauth-2025-04-20` header) works end-to-end. If it fails
with an auth error, re-mint the token on the host with `claude setup-token` and update
the 1Password item.
