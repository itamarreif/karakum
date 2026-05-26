#!/usr/bin/env bash
# manifest.sh — agent + project manifest parsing helpers.
#
# Source from another script:
#   source "$(dirname "${BASH_SOURCE[0]}")/lib/manifest.sh"
#
# Exposes:
#   manifest_agent_path <name>      → echoes "${KARAKUM_ROOT}/agents/<name>.yaml"
#   manifest_project_path <name>    → echoes "${KARAKUM_ROOT}/projects/<name>.yaml"
#   manifest_require <path>         → fails 2 if file missing
#   manifest_get <path> <yq-expr>   → echoes the value; passes through yq -r
#   manifest_expand_path <s>        → echoes path with leading ~ expanded to $HOME

manifest_agent_path() {
  echo "${KARAKUM_ROOT}/agents/${1}.yaml"
}

manifest_project_path() {
  echo "${KARAKUM_ROOT}/projects/${1}.yaml"
}

manifest_require() {
  local path=$1
  if [[ ! -f "$path" ]]; then
    echo "karakum: no manifest at $path" >&2
    return 2
  fi
}

manifest_get() {
  local path=$1
  local expr=$2
  yq -r "$expr" "$path"
}

manifest_expand_path() {
  local v=$1
  echo "${v/#\~/$HOME}"
}
