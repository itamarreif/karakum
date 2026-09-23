"""Declarative wiring of an agent's memory into a running Open WebUI.

The CLI harnesses get the vault's master prompt via `memory.init`, which
symlinks it into each one's instruction file (~/.claude/CLAUDE.md and friends).
Open WebUI reads no instruction file — a workspace model's system prompt lives
in the database — so the equivalent is an API call rather than a symlink.

Two endpoints do the work, both reconciling rather than appending, so this is
safe to re-run on every `karakum serve`:

  POST /api/v1/knowledge/create   -> a collection to sync the vault into
  POST /api/v1/models/sync        -> the full desired workspace-model set

Deliberately not seeding `webui.db` directly: Open WebUI has already migrated
that schema once (config went from a single JSON blob to one row per key), and
a writer that reaches past the API breaks silently on the next migration.

Field placement is taken from the v0.11.3 source, not the docs, which don't
specify it:
  - system prompt -> `params.system` (utils/payload.py open_webui_params)
  - knowledge     -> `meta.knowledge` (models/models.py ModelMeta)

Auth is an Open WebUI API key (`sk-` + 32 hex) acting as its creating user.
There is no documented way to mint one non-interactively, so it is a one-time
manual bootstrap per instance; see docs/configuration.md.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

TIMEOUT = 10


class OpenWebUIError(RuntimeError):
    pass


def _request(base: str, path: str, token: str, payload=None, method=None):
    url = f"{base.rstrip('/')}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method or ("POST" if data is not None else "GET"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:200]
        raise OpenWebUIError(f"{method or 'GET'} {path} -> {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise OpenWebUIError(f"{method or 'GET'} {path} -> unreachable: {e.reason}") from e
    return json.loads(body) if body else None


def wait_healthy(base: str, attempts: int = 30, delay: float = 1.0) -> bool:
    """Poll /health until the server answers.

    `docker compose up -d` returns once the container is created, which is well
    before the backend is accepting requests — syncing immediately gets a
    connection refused on a cold start.
    """
    url = f"{base.rstrip('/')}/health"
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(delay)
    return False


def ensure_knowledge(base: str, token: str, name: str, description: str = "") -> str:
    """Return the id of the knowledge collection called `name`, creating it once.

    Matched by name because the id is a server-assigned UUID — we have nothing
    stable to key on until the collection exists.
    """
    existing = _request(base, "/api/v1/knowledge/", token) or []
    items = existing.get("items", existing) if isinstance(existing, dict) else existing
    for item in items:
        if item.get("name") == name:
            return item["id"]
    created = _request(base, "/api/v1/knowledge/create", token,
                       {"name": name, "description": description})
    if not created or "id" not in created:
        raise OpenWebUIError(f"knowledge/create returned no id: {created!r}")
    return created["id"]


def build_model(model_id: str, name: str, base_model_id: str, system: str,
                knowledge_ids: list[str] | None = None, description: str = "") -> dict:
    """A ModelForm for /models/sync (see the field-placement note in the docstring)."""
    meta: dict = {"description": description}
    if knowledge_ids:
        meta["knowledge"] = [{"type": "collection", "id": kid} for kid in knowledge_ids]
    return {
        "id": model_id,
        "base_model_id": base_model_id,
        "name": name,
        "meta": meta,
        "params": {"system": system},
        "is_active": True,
    }


def sync_models(base: str, token: str, models: list[dict]):
    """Reconcile the workspace model set. Declarative: this is the desired state,
    not an append, so removing an entry here removes it from the workspace."""
    return _request(base, "/api/v1/models/sync", token, {"models": models})
