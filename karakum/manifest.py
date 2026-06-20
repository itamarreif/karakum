import os
import sys
from pathlib import Path

import yaml


def karakum_root() -> Path:
    """The karakum checkout: code, container builds, defaults, examples."""
    return Path(__file__).parent.parent


def config_dir() -> Path:
    """User config dir (agents/, projects/, config.yaml, secrets.yaml, ...).

    `$KARAKUM_CONFIG_DIR` if set, else `~/.config/karakum`.
    """
    if env := os.environ.get("KARAKUM_CONFIG_DIR"):
        return Path(env).expanduser()
    return Path("~/.config/karakum").expanduser()


def data_dir() -> Path:
    """Generated data dir (sessions/, state/).

    `$KARAKUM_DATA_DIR` if set, else `~/.karakum`.
    """
    if env := os.environ.get("KARAKUM_DATA_DIR"):
        return Path(env).expanduser()
    return Path("~/.karakum").expanduser()


def agent_path(name: str) -> Path:
    return config_dir() / "agents" / f"{name}.yaml"


def project_path(name: str) -> Path:
    return config_dir() / "projects" / f"{name}.yaml"


def require(path: Path) -> None:
    if not path.exists():
        print(f"karakum: no manifest at {path}", file=sys.stderr)
        raise SystemExit(2)


def load(path: Path) -> dict:
    require(path)
    with open(path) as f:
        return yaml.safe_load(f) or {}


def get(data: dict, key_path: str):
    """Traverse nested dict by dot-separated key path."""
    v = data
    for part in key_path.split("."):
        if not isinstance(v, dict):
            return None
        v = v.get(part)
    return v


def expand_path(s: str) -> Path:
    return Path(s).expanduser()
