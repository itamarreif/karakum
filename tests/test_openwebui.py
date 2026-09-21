"""Tests for the Open WebUI wiring — the master-prompt/knowledge equivalent of
the `memory.init` symlinks the CLI harnesses get.

Field placement (`params.system`, `meta.knowledge`) is asserted here because it
comes from reading Open WebUI's source rather than its docs, so a version bump
that moves it should fail loudly instead of producing a model with no prompt.
"""
import json
from types import SimpleNamespace

import pytest

from karakum import openwebui


class _Resp:
    def __init__(self, payload, status=200):
        self._body = json.dumps(payload).encode() if payload is not None else b""
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def calls(monkeypatch):
    """Capture urlopen calls; queue responses by pushing onto `replies`."""
    seen, replies = [], []

    def fake_urlopen(req, timeout=None):
        seen.append(req)
        return _Resp(replies.pop(0) if replies else {})

    monkeypatch.setattr(openwebui.urllib.request, "urlopen", fake_urlopen)
    return SimpleNamespace(seen=seen, replies=replies)


# --- build_model: field placement -----------------------------------------

def test_system_prompt_goes_in_params_not_meta():
    m = openwebui.build_model("takwin", "takwin", "gpt", "MASTER PROMPT")
    assert m["params"]["system"] == "MASTER PROMPT"
    assert "system" not in m["meta"]


def test_knowledge_goes_in_meta_as_collection_refs():
    m = openwebui.build_model("takwin", "takwin", "gpt", "p", knowledge_ids=["kb-1"])
    assert m["meta"]["knowledge"] == [{"type": "collection", "id": "kb-1"}]


def test_no_knowledge_key_when_none_given():
    """An empty list would read as 'detach everything' on a reconciling endpoint."""
    assert "knowledge" not in openwebui.build_model("t", "t", "gpt", "p")["meta"]


# --- ensure_knowledge: idempotence ----------------------------------------

def test_existing_collection_is_reused_not_recreated(calls):
    calls.replies.append({"items": [{"name": "takwin-vault", "id": "kb-existing"}]})
    assert openwebui.ensure_knowledge("http://x", "sk-1", "takwin-vault") == "kb-existing"
    assert len(calls.seen) == 1                      # listed, never created
    assert calls.seen[0].get_method() == "GET"


def test_missing_collection_is_created(calls):
    calls.replies += [{"items": []}, {"id": "kb-new"}]
    assert openwebui.ensure_knowledge("http://x", "sk-1", "takwin-vault") == "kb-new"
    assert calls.seen[-1].full_url.endswith("/api/v1/knowledge/create")


def test_bare_list_response_is_accepted(calls):
    """The list endpoint has returned both a bare array and {items: [...]}."""
    calls.replies.append([{"name": "takwin-vault", "id": "kb-bare"}])
    assert openwebui.ensure_knowledge("http://x", "sk-1", "takwin-vault") == "kb-bare"


def test_create_without_id_raises(calls):
    calls.replies += [{"items": []}, {"detail": "nope"}]
    with pytest.raises(openwebui.OpenWebUIError):
        openwebui.ensure_knowledge("http://x", "sk-1", "takwin-vault")


# --- transport -------------------------------------------------------------

def test_requests_carry_the_bearer_token(calls):
    calls.replies.append({})
    openwebui.sync_models("http://x", "sk-secret", [])
    assert calls.seen[0].headers["Authorization"] == "Bearer sk-secret"


def test_sync_posts_models_envelope(calls):
    calls.replies.append({})
    openwebui.sync_models("http://x", "sk-1", [{"id": "takwin"}])
    req = calls.seen[0]
    assert req.full_url.endswith("/api/v1/models/sync")
    assert json.loads(req.data) == {"models": [{"id": "takwin"}]}


def test_http_error_becomes_openwebui_error(monkeypatch):
    import urllib.error

    def boom(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(openwebui.urllib.request, "urlopen", boom)
    with pytest.raises(openwebui.OpenWebUIError, match="401"):
        openwebui.sync_models("http://x", "bad", [])
