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
import shutil
import subprocess
import sys
from typing import Tuple

import yaml

from karakum.manifest import config_dir


def _provider_op(ref: str) -> str:
    if not shutil.which("op"):
        print("karakum: 'op' not on PATH (brew install 1password-cli; then 'op signin')", file=sys.stderr)
        raise SystemExit(2)
    result = subprocess.run(["op", "read", ref], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"karakum: op read failed for {ref}: {result.stderr.strip()}", file=sys.stderr)
        raise SystemExit(2)
    return result.stdout.rstrip("\n")


def _provider_env(ref: str) -> str:
    var = ref[len("env://"):]
    value = os.environ.get(var, "")
    if not value:
        print(f"karakum: env var '{var}' is unset or empty (ref: {ref})", file=sys.stderr)
        raise SystemExit(2)
    return value


_PROVIDERS = {
    "op": _provider_op,
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
    for var, ref in _load_refs().items():
        if "://" not in ref:
            print(f"karakum: malformed secret reference (no scheme): {ref}", file=sys.stderr)
            raise SystemExit(2)
        scheme = ref.split("://", 1)[0]
        provider = _PROVIDERS.get(scheme)
        if provider is None:
            print(f"karakum: no provider registered for scheme '{scheme}' (ref: {ref})", file=sys.stderr)
            print(f"        registered schemes: {', '.join(_PROVIDERS)}", file=sys.stderr)
            raise SystemExit(2)
        value = provider(ref)
        env_dict[var] = value
        docker_args.extend(["-e", var])
    return env_dict, docker_args
