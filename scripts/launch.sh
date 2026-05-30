#!/usr/bin/env bash
# launch.sh — orchestrate a karakum agent session.
#
# Usage: launch.sh <toolchain> <agent> <session-slug> [<project>] <cmd> [args...]
#
#   <toolchain>      compose service to run (claude, codex, …); selects the image
#   <agent>          name matching agents/<agent>.yaml; agent's identity (memory)
#   <session-slug>   short identifier; becomes branch <agent>/<slug> in each repo
#                    and worktree dir YYYYMMDD-<slug>
#   <project>        optional name matching projects/<project>.yaml; if omitted,
#                    pass `-` to skip and only mount agent memory
#   <cmd>            command to exec inside container (e.g. claude, bash)
#
# Exits:
#   0   success (exec'd into container)
#   2   precondition failure (missing tools, bad repo state, secret read failed)
#   64  EX_USAGE (bad args)

set -euo pipefail

KARAKUM_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export KARAKUM_ROOT

# shellcheck source=lib/manifest.sh
source "${KARAKUM_ROOT}/scripts/lib/manifest.sh"
# shellcheck source=lib/preflight.sh
source "${KARAKUM_ROOT}/scripts/lib/preflight.sh"
# shellcheck source=lib/secrets.sh
source "${KARAKUM_ROOT}/scripts/lib/secrets.sh"
# shellcheck source=lib/worktree.sh
source "${KARAKUM_ROOT}/scripts/lib/worktree.sh"

usage() {
  cat <<'EOF' >&2
usage: launch.sh <toolchain> <agent> <session-slug> <project|-> <cmd> [args...]

  <toolchain>      compose service to run (claude, codex, …)
  <agent>          name matching agents/<agent>.yaml
  <session-slug>   short identifier; becomes branch <agent>/<slug>
                   and worktree dir YYYYMMDD-<slug>
  <project>        name matching projects/<project>.yaml, or '-' for none
  <cmd>            command to exec inside container (e.g. claude, bash)
EOF
  exit 64
}

main() {
  [[ $# -ge 5 ]] || usage
  local toolchain=$1
  local agent=$2
  local slug=$3
  local project=$4
  local cmd=$5
  shift 5

  preflight_tools

  # --- memory (always present) ---
  local agent_manifest
  agent_manifest=$(manifest_agent_path "$agent")
  manifest_require "$agent_manifest"

  local memory_path memory_repo
  memory_path=$(manifest_expand_path "$(manifest_get "$agent_manifest" '.memory.path')")
  memory_repo=$(manifest_get "$agent_manifest" '.memory.repository')

  # Persistent per-agent harness state, mounted at the container's ~/.claude.
  local state_raw
  state_raw=$(manifest_get "$agent_manifest" '.state.path')
  if [[ -z "$state_raw" || "$state_raw" == "null" ]]; then
    echo "karakum: agent '$agent' is missing required .state.path in $agent_manifest" >&2
    exit 2
  fi
  CLAUDE_STATE_DIR=$(manifest_expand_path "$state_raw")
  mkdir -p "$CLAUDE_STATE_DIR"
  export CLAUDE_STATE_DIR

  preflight_repo "$memory_path" "$memory_repo" "memory"

  local memory_worktree
  memory_worktree=$(worktree_ensure "$memory_path" "$agent" "$slug")

  # --- project (optional) ---
  local project_worktree="" project_args=()
  if [[ "$project" != "-" ]]; then
    local project_manifest
    project_manifest=$(manifest_project_path "$project")
    manifest_require "$project_manifest"

    local project_path project_repo
    project_path=$(manifest_expand_path "$(manifest_get "$project_manifest" '.path')")
    project_repo=$(manifest_get "$project_manifest" '.repository')

    preflight_repo "$project_path" "$project_repo" "project '$project'"

    project_worktree=$(worktree_ensure "$project_path" "$agent" "$slug")
    project_args=(
      -v "${project_worktree}:${project_worktree}:rw"
      -e "KARAKUM_PROJECT=${project_worktree}"
    )
  fi

  # --- secrets (host-wide, shared across all agents/toolchains) ---
  secrets_load "${KARAKUM_ROOT}/secrets.yaml"

  # claude authenticates from CLAUDE_CODE_OAUTH_TOKEN, which takes precedence over
  # the mounted ~/.claude/.credentials.json. If it's missing, claude silently
  # falls back to the interactive /login that doesn't work over tmux — so fail
  # fast here instead of dropping the user into a broken prompt.
  if [[ "$toolchain" == "claude" && -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]]; then
    echo "karakum: CLAUDE_CODE_OAUTH_TOKEN is unset after resolving secrets.yaml" >&2
    echo "        claude would fall back to interactive /login inside the container" >&2
    echo "        add it to secrets.yaml (op://… from 'claude setup-token') and retry" >&2
    exit 2
  fi

  # --- env for compose ---
  export MEMORY_WORKTREE="$memory_worktree"

  local session_name cwd
  session_name="$(date +%Y%m%d)-${slug}"
  cwd="${project_worktree:-$memory_worktree}"

  cd "$KARAKUM_ROOT"

  exec docker compose run --rm \
    --name "agent-${agent}-${slug}" \
    -e "KARAKUM_SESSION=${session_name}" \
    -e "KARAKUM_AGENT=${agent}" \
    -e "KARAKUM_MEMORY=${memory_worktree}" \
    "${project_args[@]}" \
    -w "$cwd" \
    "${SECRETS_DOCKER_ARGS[@]}" \
    "agent-${toolchain}" \
    "$cmd" "$@"
}

main "$@"
