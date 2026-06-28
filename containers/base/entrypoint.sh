#!/bin/sh
# karakum container entrypoint — runs as root, then drops to the agent account.
#
# The base image bakes a single `agent` account whose uid/gid match the host so
# bind-mounted sessions stay writable. Here we rename that account to the
# launching agent (KARAKUM_AGENT) so `whoami`, the shell prompt (\u) and the
# ownership of newly created files all read e.g. `takwin`, then exec the
# requested command as that user. Only the *login name* changes — home stays
# /home/agent (where the scratchpad/project/.claude mounts land), so mount
# targets never depend on the agent name.
set -e

target="${KARAKUM_AGENT:-agent}"
if [ "$target" != "agent" ]; then
    usermod  -l "$target" agent 2>/dev/null || true
    groupmod -n "$target" agent 2>/dev/null || true
fi

# Drop root -> agent account, preserving the env the launcher injected
# (KARAKUM_*, GIT_*, SSH_AUTH_SOCK, TERM, COLORTERM, …). We entered as root so
# HOME is /root; force it back to the account's home for ~/.bashrc + ~/.claude.
exec runuser -u "$target" -- env "HOME=/home/agent" "$@"
