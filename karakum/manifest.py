import sys
from pathlib import Path

import yaml


def karakum_root() -> Path:
    return Path(__file__).parent.parent


def agent_path(name: str) -> Path:
    return karakum_root() / "agents" / f"{name}.yaml"


def project_path(name: str) -> Path:
    return karakum_root() / "projects" / f"{name}.yaml"


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
