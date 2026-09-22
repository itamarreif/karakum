# Open WebUI setup

[Open WebUI](https://docs.openwebui.com) is karakum's first **service harness**. Unlike
`claude`, `codex`, `opencode` and `pi` — four CLIs on `PATH` in the one agent image, picked
inside the session shell — this one is a server in its own image, so it is a second compose
service started by `karakum serve <agent>` rather than a binary you run after `just shell`.

Everything below is verified against **v0.11.3**. Names and field placement drift between
releases; re-check against the tag actually pinned in
`containers/openwebui/compose.openwebui.yaml`.

```bash
karakum serve <agent>                          # detached, no published port
karakum serve <agent> --publish                # dev only: 127.0.0.1:3000
karakum serve <agent> --mock                   # bundled stub backend, no AWS
karakum serve <agent> --knowledge              # also sync the vault into a knowledge base
karakum serve <agent> --no-sync                # skip the post-start wiring
karakum serve <agent> --down                   # stop
```

It takes no `<project>` and no `<slug>`: a chat surface produces no commits, so there is no
branch to namespace.

## How Open WebUI is wired

- **Image** — `ghcr.io/open-webui/open-webui`, pinned. Not built here; the harness *is* the
  upstream image. **Never track `:main`** — Open WebUI has already migrated its config
  schema once, and a silent bump across a migration breaks a running box quietly. 0.9.6+ is
  required by oikb.
- **State** — `/app/backend/data`, bind-mounted from `<state_root>/<agent>-openwebui`, the
  same per-agent host-dir pattern as `~/.claude` and `~/.codex`. Container lifetime and
  state lifetime are independent; the container is as disposable as a session one.
- **Vault** — the agent's memory clone is mounted read-only at `/vault`, so the same
  scratchpad the CLIs see is available to the knowledge base.
- **Ingress** — no published port by default, per the rule in `AGENTS.md`. `--publish`
  chains a dev-only overlay binding `127.0.0.1` explicitly. The real ingress story is the
  Tailscale sidecar.
- **Secrets** — resolved into the compose *process* for `${VAR}` substitution. Unlike
  `launch`, nothing is injected with `-e`, so only what the compose `environment:` block
  names reaches a container that listens on a port. Preserve that property.

### What lives in the state dir

| Path | Holds |
|---|---|
| `webui.db` | SQLite: users, chats, messages, model definitions, **and persisted config** |
| `vector_db/` | ChromaDB (local mode) |
| `uploads/`, `cache/` | uploaded files |

That is the complete backup surface — nothing is held in process.

## Wiring the agent's master prompt (scratchpad)

The CLI harnesses get this through `memory.init`, which symlinks the vault's master prompt
into each one's instruction file (`~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`, …).

**Open WebUI reads no instruction file.** A workspace model's system prompt lives in the
database, so the equivalent is an API call, which `serve` makes after start-up:

```
POST /api/v1/models/sync     {"models": [ … ]}
```

That endpoint **reconciles** rather than appends — you send the full desired workspace-model
set. This is what gives it the same property as the symlinks: safe to re-run on every
launch, and an edit to `MASTER_PROMPT.md` propagates on the next `serve`. Removing an entry
removes it from the workspace.

Wiring is **non-fatal** throughout. A harness that is up and usable should not be torn down
because a workspace model could not be declared, so each failure path — no API key, no
published port, server not healthy, no `MASTER_PROMPT.md`, API error — logs what to do and
leaves the container running.

`serve` polls `GET /health` first: `docker compose up -d` returns once the container is
*created*, which is well before the backend accepts requests.

### Why the API, not `webui.db`

Seeding the database directly is tempting and wrong. That schema has already migrated once
— config went from a single JSON blob to one row per dot-notation key, and `api_key` moved
out of the `user` table in v0.6.41. A writer that reaches past the API breaks silently on
the next migration, and the symptom is an agent that quietly has no system prompt.

### Field placement

Open WebUI's docs do **not** specify where these live. Both were read from the v0.11.3
source:

| What | Where | Source |
|---|---|---|
| System prompt | `params.system` | `backend/open_webui/utils/payload.py`, `open_webui_params` |
| Knowledge | `meta.knowledge`, as `[{"type": "collection", "id": …}]` | `backend/open_webui/models/models.py`, `ModelMeta` |

`ModelForm` is `{id, base_model_id, name, meta, params, access_grants, is_active}`, with
`params` and `meta` both `extra='allow'`.

`tests/test_openwebui.py` asserts both placements. That is deliberate: without the
assertion, a version bump that moves either one produces a workspace model with no system
prompt and no error.

## Wiring the vault as knowledge

`--knowledge` creates a collection and runs
[oikb](https://docs.openwebui.com/ecosystem/knowledge-base-sync), Open WebUI's own sync
tool, in watch mode against `/vault/scratchpad`.

oikb checksums every file, posts the manifest to the server's `/sync/diff`, and uploads only
what changed — so re-syncing a vault where nothing moved costs nothing, and notes are never
needlessly re-embedded.

It starts in a **second pass**, not alongside `openwebui`: the knowledge-base id is a
server-assigned UUID, so it cannot exist until the server does. See `_wire_memory` in
`karakum/cli.py`.

Useful endpoints if you need to do this by hand:

| Endpoint | Purpose |
|---|---|
| `POST /api/v1/knowledge/create` | New collection; body `{name, description}` |
| `GET /api/v1/knowledge/` | List collections |
| `POST /api/v1/files/` | Upload (multipart, `file` field) |
| `POST /api/v1/knowledge/{id}/file/add` | Attach an uploaded file |

## Authentication — a one-time manual bootstrap

Both the master-prompt wiring and oikb authenticate with an **Open WebUI API key**
(`sk-` + 32 hex). The key acts as the user that created it, and can only be minted through
the UI: there is **no documented non-interactive admin token**, and the JWT shown in
settings is a browser session token that expires.

So, once per instance:

1. `karakum serve <agent> --publish` and open it.
2. Create the admin account — the first account created becomes admin, then `ENABLE_SIGNUP`
   keeps the door closed.
3. Settings → Account → API keys → Create new secret key.
4. Store it at the `op://` path named in `examples/secrets.yaml`
   (`OPENWEBUI_API_KEY_ADMIN`).

Every launch after that is declarative.

An account holds a **single unnamed key** — creating another silently replaces it. Keys are
stored in the clear and their `expires_at` column is never written, so expiry is not
enforced.

> We control `WEBUI_SECRET_KEY`, so a valid admin JWT *could* be signed offline using the
> admin user id from `webui.db`, removing the manual step. That reverse-engineers an
> internal token format — precisely the thing that breaks quietly on upgrade. The manual
> bootstrap is the better trade.

## Connecting a model backend

Open WebUI discovers models with `GET {base}/models`. If that 404s, the connection shows
**zero models even though chat completions work fine** — a failure indistinguishable from
several others, so check it first.

- **No trailing slash** on the base URL. It breaks `/models` specifically.
- **Bedrock needs no gateway.** `aws-samples/bedrock-access-gateway` is deprecated; Bedrock
  serves OpenAI-compatible APIs natively. Its base path is `/openai/v1`, **not** `/v1`.
- Prefer the `bedrock-mantle` endpoint over `bedrock-runtime`: it cannot serve cross-region
  inference profiles, which makes single-region processing structural rather than a config
  discipline a copied model ID can silently break.

`--mock` runs the whole path against `containers/openwebui/mock/fake_bedrock.py` — a stdlib
stub covering bearer auth, streaming and non-streaming completions, and model listing — so
none of this needs an AWS account to exercise. `MOCK_MODELS=0` reproduces the `/models` 404
deliberately.

## Gotchas

**The ConfigVar trap.** Many env vars — including `OPENAI_API_BASE_URLS` and
`ENABLE_SIGNUP` — are read from the environment *only on first boot*, then persisted into
`webui.db` and read from there forever after. Later compose edits are silently ignored.
`ENABLE_PERSISTENT_CONFIG=False` (set here) keeps env authoritative on every launch, at the
cost of Admin UI edits no longer persisting. **This is the first thing to check when a
setting won't take.**

**`ENABLE_API_KEYS` is plural.** `ENABLE_API_KEY` is silently ignored.

**`WEBUI_SECRET_KEY` must be stable.** It signs JWTs; change it and everyone is logged out
on the next deploy.

**Provider keys are stored in plaintext** in the `config` table, as are personal API keys in
`api_key`. Only `auth.password` is hashed. Treat any `webui.db` copy or volume snapshot as a
secret-bearing artifact — which is the argument for a gateway holding the real credential
and Open WebUI holding only a revocable scoped key.

## Backups

SQLite runs in WAL mode. `webui.db-wal` and `webui.db-shm` appear while the database is open
and **disappear on clean shutdown** — that is a checkpoint folding the WAL into the main
file, not data loss.

So never copy `webui.db` alone from a running instance; you would miss whatever is still in
the WAL. Stop the container, or take an online backup:

```bash
docker exec <container> python3 -c "
import sqlite3
src = sqlite3.connect('/app/backend/data/webui.db')
dst = sqlite3.connect('/tmp/backup.db')
src.backup(dst); dst.close(); src.close()"
docker cp <container>:/tmp/backup.db ./webui-backup.db
```

Back that up together with `uploads/` and `vector_db/`.

WAL/shm is single-host, single-writer machinery — that, not scale, is what would force
Postgres if this ever needed more than one replica.

## Verify

```bash
karakum serve <agent> --publish --mock

# state landed where it should
ls "$(karakum config state-root 2>/dev/null || echo ~/.karakum/state)/<agent>-openwebui"
#   webui.db  vector_db/  uploads/  cache/

# the vault is mounted read-only
docker compose exec openwebui ls /vault

# after the one-time key bootstrap: the workspace model carries the master prompt
curl -s -H "Authorization: Bearer $OPENWEBUI_API_KEY_ADMIN" \
  http://localhost:3000/api/v1/models/export | head -c 400
```
