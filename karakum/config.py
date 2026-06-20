"""Global karakum settings, read from an optional `config.yaml` in the config dir.

The config dir is `manifest.config_dir()` (`$KARAKUM_CONFIG_DIR`, default
`~/.config/karakum`). Unlike `manifest.load` (which requires the file and exits on
a miss), this config is optional: a missing file or key falls back to documented
defaults.

The session/state roots default to subdirs of the data dir (`manifest.data_dir()`,
`$KARAKUM_DATA_DIR`, default `~/.karakum`), so pointing `$KARAKUM_DATA_DIR`
elsewhere relocates both at once. Precedence per subtree:
explicit `config.yaml` key > `data_dir()`-derived default (which honors the env var).
"""
from pathlib import Path

import yaml

from karakum import manifest
from karakum.manifest import expand_path


def _config_path() -> Path:
    """Path to the optional `config.yaml` (resolved at call time so the env var
    `$KARAKUM_CONFIG_DIR` is honored even if set after import)."""
    return manifest.config_dir() / "config.yaml"


def _load() -> dict:
    path = _config_path()
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def sessions_root() -> Path:
    """Root under which per-session clones live: `<root>/<agent>/<slug>/<label>`.

    `config.yaml` key `sessions_root` if set, else `<data_dir>/sessions`.
    """
    default = str(manifest.data_dir() / "sessions")
    return expand_path(_load().get("sessions_root", default))


def state_root() -> Path:
    """Root under which per-agent harness state lives: `<root>/<agent>` → ~/.claude.

    `config.yaml` key `state_root` if set, else `<data_dir>/state`.
    """
    default = str(manifest.data_dir() / "state")
    return expand_path(_load().get("state_root", default))
