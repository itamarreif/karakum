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
