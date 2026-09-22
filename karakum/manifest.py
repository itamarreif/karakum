import os
from pathlib import Path

import yaml

from karakum import console


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


def toolchains_path() -> Path:
    """toolchains.yaml from the config dir (host-owned; seed from examples/)."""
    return config_dir() / "toolchains.yaml"


def require(path: Path) -> None:
    if not path.exists():
        console.error(f"no manifest at {path}")
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


def project_repos(data: dict, name: str = "") -> list[dict]:
    """Every repo a project declares, normalized to `{path, repository, clean}`.

    A project is one *or more* repos — `platform` may be dewey + mundaneum, and a
    session mounts all of them. Two spellings, same result:

        path: ~/code/x            # shorthand: a one-repo project
        repository: github.com/o/x

        repos:                    # general form
          - {path: ~/code/a, repository: github.com/o/a}
          - {path: ~/code/b, repository: github.com/o/b, clean: [...]}

    `clean` stays attached to the repo it cleans, so a multi-repo project can
    override per repo. Declaring both spellings is an error rather than a merge —
    it reads as a half-finished edit, and guessing which one wins is worse than
    saying so.

    This normalizes *shape* only: a missing `path` or `repository` comes back as
    None. Requiring them is the launcher's job, because the other callers
    (`resume`'s label lookup, the `session clean` map) read every manifest on the
    host and must not die on one that is incomplete or not theirs.
    """
    label = f"project '{name}'" if name else "project"
    # `is not None` rather than truthiness: `repos: []` is a mistake worth
    # reporting, not a project that silently falls back to the shorthand form.
    has_repos = data.get("repos") is not None
    repos = data.get("repos")
    top = data.get("path") or data.get("repository")

    if has_repos and top:
        console.error(
            f"{label} declares both `repos:` and a top-level `path`/`repository` — "
            "use one. The top-level form is shorthand for a single-entry `repos:`."
        )
        raise SystemExit(2)

    if has_repos:
        if not isinstance(repos, list):
            console.error(f"{label}: `repos:` must be a list")
            raise SystemExit(2)
        if not repos:
            console.error(f"{label}: `repos:` is empty")
            raise SystemExit(2)
        out = []
        for i, entry in enumerate(repos):
            if not isinstance(entry, dict):
                console.error(f"{label}: repos[{i}] must be a mapping")
                raise SystemExit(2)
            out.append({"path": entry.get("path"),
                        "repository": entry.get("repository"),
                        "clean": entry.get("clean")})
        return out

    return [{"path": data.get("path"),
             "repository": data.get("repository"),
             "clean": data.get("clean")}]
