"""Global karakum settings, read from an optional `~/.karakum/config.yaml`.

Unlike `manifest.load` (which requires the file and exits on a miss), this config
is optional: a missing file or key falls back to documented defaults.
"""
from pathlib import Path

import yaml

from karakum.manifest import expand_path

CONFIG_PATH = Path("~/.karakum/config.yaml").expanduser()
DEFAULT_SESSIONS_ROOT = "~/.karakum/sessions"
DEFAULT_STATE_ROOT = "~/.karakum/state"
DEFAULT_CLEANUP_PREDICATE = "merged"


def _load() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f) or {}


def sessions_root() -> Path:
    """Root under which per-session clones live: `<root>/<agent>/<slug>/<label>`."""
    return expand_path(_load().get("sessions_root", DEFAULT_SESSIONS_ROOT))


def state_root() -> Path:
    """Root under which per-agent harness state lives: `<root>/<agent>` → ~/.claude."""
    return expand_path(_load().get("state_root", DEFAULT_STATE_ROOT))


def cleanup_predicate() -> str:
    """Safe-delete predicate name for `karakum clean` (`cleanup.predicate`).

    Default `merged` (a session is reapable once its branch's PR is merged on
    GitHub). See `cleanup._PREDICATES` for the available names.
    """
    cleanup = _load().get("cleanup") or {}
    return cleanup.get("predicate", DEFAULT_CLEANUP_PREDICATE)
