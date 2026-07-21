# Host-wide secrets live in `<config-dir>/secrets.yaml` under a top-level `secrets:` map
# (env-var name → reference), shared across all agents and toolchains.
#
# Each reference uses a URI scheme that selects a provider:
#
#   op://Personal/GitHub/token        → 1Password (op CLI)
#   env://ANTHROPIC_API_KEY           → host shell env var passthrough
#   <new-scheme>://<rest>             → register a new provider below
#
# To add a provider:
#   1. Define a function `_provider_<scheme>(ref: str) -> str` that accepts the
#      full URI and returns the resolved secret value. Check for its own CLI dep
#      (e.g. shutil.which("vault")) and raise SystemExit(2) with a clear message.
#   2. Add it to `_PROVIDERS` at the bottom of this file.
#
# Discipline:
#   - Resolved values are never printed or logged by this module.
#   - References (op://…, env://…) may appear in error messages.
#   - Any failure raises SystemExit(2); no silent partial state.

import os
import re
import shutil
import subprocess
import uuid
from typing import Tuple

import yaml

from karakum import console
from karakum.manifest import config_dir


def _resolve_op(refs: dict) -> dict:
    """Resolve every op:// reference in a SINGLE `op` process via `op inject`.

    One `op` process means one macOS authorization/unlock prompt, regardless of
    how many op:// secrets there are — calling `op read` once per secret prompts
    once each. Each value is fenced with a random boundary so arbitrary content
    (newlines included) round-trips unambiguously.
    """
    if not shutil.which("op"):
        console.error("'op' not on PATH (brew install 1password-cli; then 'op signin')")
        raise SystemExit(2)
    boundary = uuid.uuid4().hex
    template = "".join(
        f"{boundary}:{var}:{{{{ {ref} }}}}:{boundary}\n" for var, ref in refs.items()
    )
    result = subprocess.run(["op", "inject"], input=template, capture_output=True, text=True)
    if result.returncode != 0:
        console.error(f"op inject failed: {result.stderr.strip()}")
        raise SystemExit(2)
    resolved = {}
    for var in refs:
        m = re.search(rf"{boundary}:{re.escape(var)}:(.*?):{boundary}", result.stdout, re.DOTALL)
        if m is None:
            console.error(f"could not resolve op secret '{var}' from op inject output")
            raise SystemExit(2)
        resolved[var] = m.group(1).rstrip("\n")
    return resolved


def _provider_env(ref: str) -> str:
    var = ref[len("env://"):]
    value = os.environ.get(var, "")
    if not value:
        console.error(f"env var '{var}' is unset or empty (ref: {ref})")
        raise SystemExit(2)
    return value


# Per-reference providers for schemes resolved one at a time. op:// is handled
# separately (batched in a single `op` call — see `_resolve_op`).
_PROVIDERS = {
    "env": _provider_env,
}


def _load_refs() -> dict:
    """Read the `secrets:` map from the host-wide `<config-dir>/secrets.yaml`.

    Missing file → empty map (no secrets), same as an agent with no secrets.
    """
    path = config_dir() / "secrets.yaml"
    if not path.exists():
        return {}
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    return data.get("secrets") or {}


def load() -> Tuple[dict, list]:
    """Resolve host-wide secrets from `<config-dir>/secrets.yaml`.

    Returns (env_dict, docker_args_list): resolved values keyed by env-var name,
    and `-e VAR` (name only) flags for `docker compose run`.
    """
    env_dict: dict = {}
    docker_args: list = []
    op_refs: dict = {}
    for var, ref in _load_refs().items():
        if "://" not in ref:
            console.error(f"malformed secret reference (no scheme): {ref}")
            raise SystemExit(2)
        scheme = ref.split("://", 1)[0]
        if scheme == "op":
            op_refs[var] = ref  # resolved together below, in one op call
        else:
            provider = _PROVIDERS.get(scheme)
            if provider is None:
                console.error(f"no provider registered for scheme '{scheme}' (ref: {ref})")
                console.detail(f"registered schemes: {', '.join(['op', *_PROVIDERS])}")
                raise SystemExit(2)
            env_dict[var] = provider(ref)
        docker_args.extend(["-e", var])
    if op_refs:
        env_dict.update(_resolve_op(op_refs))
    return env_dict, docker_args
