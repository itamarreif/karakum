#!/bin/sh
# karakum container entrypoint — runs as root, then drops to the agent account.
#
# The base image bakes a single `agent` account whose uid/gid match the host so
# bind-mounted sessions stay writable. Here we rename that account to the
# launching agent (KARAKUM_AGENT) so `whoami`, the shell prompt (\u) and the
# ownership of newly created files all read e.g. `alice`, then exec the
# requested command as that user. Only the *login name* changes — home stays
# /home/agent (where the scratchpad/project/.claude mounts land), so mount
# targets never depend on the agent name.
set -e

target="${KARAKUM_AGENT:-agent}"
if [ "$target" != "agent" ]; then
    usermod  -l "$target" agent 2>/dev/null || true
    groupmod -n "$target" agent 2>/dev/null || true
fi

# Run as the agent account with the launcher-injected env (KARAKUM_*, GIT_*,
# SSH_AUTH_SOCK, TERM, COLORTERM, …) and HOME forced back to the account's home
# (we entered as root, so HOME is /root) for ~/.bashrc + ~/.claude. Not `exec`ed
# below — `exec` needs a real program, not a shell function — so the final
# handoff spells the same runuser call out.
as_agent() { runuser -u "$target" -- env "HOME=/home/agent" "$@"; }

# Optional per-agent setup hook: a shell command the launcher passed for the
# agent's memory (KARAKUM_MEMORY_INIT). Runs once here, after the mounts are in
# place, from the vault root. Expected to be idempotent; a failure is
# non-fatal so a broken hook never blocks the session.
if [ -n "${KARAKUM_MEMORY_INIT:-}" ]; then
    as_agent sh -c 'cd "${KARAKUM_MEMORY:-$HOME}" 2>/dev/null; '"$KARAKUM_MEMORY_INIT" \
        || echo "karakum: memory init hook failed (continuing): $KARAKUM_MEMORY_INIT" >&2
fi

# Drop root -> agent for the session command itself (see as_agent note above).
exec runuser -u "$target" -- env "HOME=/home/agent" "$@"
