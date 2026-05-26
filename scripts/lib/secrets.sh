#!/usr/bin/env bash
# secrets.sh — resolve secret references from a karakum manifest.
#
# Source from another script:
#   source "$(dirname "${BASH_SOURCE[0]}")/lib/secrets.sh"
#
# Exposes:
#   secrets_load <manifest>     → iterates .secrets, resolves each via its
#                                 scheme provider, exports env, populates
#                                 SECRETS_DOCKER_ARGS=(-e VAR …) for the caller.
#                                 Fails 2 on any resolver failure.
#   secrets_resolve <ref>       → echoes resolved value for a single reference
#                                 (used internally; safe to call directly).
#
# Pluggable providers
# -------------------
# Secret references use a URI scheme that selects a provider function:
#
#   op://Personal/GitHub/token        → 1Password (op CLI)
#   env://ANTHROPIC_API_KEY           → host shell env var passthrough
#   <new-scheme>://<rest>             → register a new provider below
#
# To add a provider:
#   1. Define a function `secrets_provider_<scheme>` that takes the full URI
#      as $1 and prints the resolved value on stdout. Do its own dependency
#      check (e.g. `command -v vault`) and fail 2 with a clear message.
#   2. Register it in SECRETS_PROVIDERS below.
#
# Discipline (per the `secrets` skill):
#   - resolved values are never echoed or logged by this lib
#   - references (op://…, env://…) may appear in error messages
#   - failure is fail-loud; no silent partial state

SECRETS_DOCKER_ARGS=()

# --- providers ---------------------------------------------------------------

secrets_provider_op() {
  local ref=$1
  if ! command -v op >/dev/null 2>&1; then
    echo "karakum: 'op' not on PATH (brew install 1password-cli; then 'op signin')" >&2
    return 2
  fi
  op read "$ref"
}

secrets_provider_env() {
  # env://VAR_NAME — read VAR_NAME from the caller's environment
  local ref=$1
  local var=${ref#env://}
  if [[ -z ${!var:-} ]]; then
    echo "karakum: env var '$var' is unset or empty (ref: $ref)" >&2
    return 2
  fi
  printf '%s' "${!var}"
}

# Registry: scheme → provider function name
declare -A SECRETS_PROVIDERS=(
  [op]=secrets_provider_op
  [env]=secrets_provider_env
)

# --- core --------------------------------------------------------------------

secrets_resolve() {
  local ref=$1
  local scheme=${ref%%://*}
  if [[ $scheme == "$ref" ]]; then
    echo "karakum: malformed secret reference (no scheme): $ref" >&2
    return 2
  fi
  local provider=${SECRETS_PROVIDERS[$scheme]:-}
  if [[ -z $provider ]]; then
    echo "karakum: no provider registered for scheme '$scheme' (ref: $ref)" >&2
    echo "        registered schemes: ${!SECRETS_PROVIDERS[*]}" >&2
    return 2
  fi
  "$provider" "$ref"
}

secrets_load() {
  local manifest=$1
  SECRETS_DOCKER_ARGS=()

  if ! yq -e '.secrets' "$manifest" >/dev/null 2>&1; then
    return 0
  fi

  local var ref value
  while IFS= read -r var; do
    [[ -z "$var" ]] && continue
    ref=$(yq -r ".secrets.\"$var\"" "$manifest")
    if ! value=$(secrets_resolve "$ref" 2>&1); then
      echo "karakum: failed to resolve secret '$var' from $ref" >&2
      echo "        resolver output: $value" >&2
      return 2
    fi
    export "$var=$value"
    SECRETS_DOCKER_ARGS+=(-e "$var")
  done < <(yq -r '.secrets | keys | .[]' "$manifest")
}
