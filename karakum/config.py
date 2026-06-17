"""Global karakum settings, read from an optional `~/.karakum/config.yaml`.

Unlike `manifest.load` (which requires the file and exits on a miss), this config
is optional: a missing file or key falls back to documented defaults.
"""
import os
from pathlib import Path

import yaml

from karakum.manifest import expand_path

CONFIG_PATH = Path("~/.karakum/config.yaml").expanduser()
DEFAULT_SESSIONS_ROOT = "~/.karakum/sessions"
DEFAULT_STATE_ROOT = "~/.karakum/state"

# 1Password's SSH agent socket (the team-id segment is AgileBits', stable across installs).
ONEPASSWORD_SSH_SOCK = "~/Library/Group Containers/2BUA8C4S2C.com.1password/t/agent.sock"

# Provider → host SSH-agent socket, for forwarding into the container. See docs/ssh.md.
SSH_AGENT_PROVIDERS = ("system", "1password", "none")


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


def ssh_agent_socket(provider: str) -> str | None:
    """Resolve the host SSH-agent socket to forward for a provider.

    - `system`    → the caller's `$SSH_AUTH_SOCK` (whatever the host default agent is)
    - `1password` → 1Password's fixed agent socket (`ONEPASSWORD_SSH_SOCK`)
    - `none`      → no forwarding

    Returns `None` when forwarding is disabled or the socket can't be resolved.
    Selection is an arg today (`--ssh-agent`); this is the seam a future
    `~/.karakum/config.yaml` `ssh_agent:` block plugs into (see #30).
    """
    if provider == "1password":
        return str(Path(ONEPASSWORD_SSH_SOCK).expanduser())
    if provider == "system":
        return os.environ.get("SSH_AUTH_SOCK") or None
    return None  # "none" or unknown
