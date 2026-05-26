#!/usr/bin/env bash
# preflight.sh — environment + scratchpad preflight checks.
#
# Source from another script:
#   source "$(dirname "${BASH_SOURCE[0]}")/lib/preflight.sh"
#
# Exposes:
#   preflight_tools                              → check yq, docker on PATH (fail 2)
#   preflight_repo <path> <expected-repo> [lbl] → check git repo + origin matches (fail 2)
#
# Secret-provider tools (op, vault, …) are checked lazily by each provider in
# lib/secrets.sh — not here — so users with no secrets in their manifest don't
# need those tools installed.

_preflight_have() {
  local tool=$1
  local hint=$2
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "karakum: '$tool' not on PATH ($hint)" >&2
    return 2
  fi
}

preflight_tools() {
  _preflight_have yq "brew install yq" || return $?
  _preflight_have docker "install Docker Desktop or OrbStack" || return $?
}

# Normalize a git repo reference for comparison:
#   git@github.com:owner/repo.git → github.com/owner/repo
#   https://github.com/owner/repo → github.com/owner/repo
_canonicalize_repo() {
  local r=$1
  r="${r#https://}"
  r="${r#http://}"
  r="${r#git@}"
  r="${r/://}"        # git@host:owner → host/owner
  r="${r%.git}"
  r="${r%/}"
  echo "$r"
}

# preflight_repo — validate a path is a git repo whose origin matches an expected remote.
# Used for both memory and project repos.
#   $1 path (host filesystem)
#   $2 expected canonical remote (manifest-declared)
#   $3 label for error messages (e.g. "memory" or "project 'karakum'")
preflight_repo() {
  local path=$1
  local expected_repo=$2
  local label=${3:-repo}

  if [[ ! -d "$path/.git" ]]; then
    echo "karakum: $label at $path is not a git repo" >&2
    echo "        init it first: (cd $path && git init && add an 'origin' remote)" >&2
    return 2
  fi
  if ! git -C "$path" remote get-url origin >/dev/null 2>&1; then
    echo "karakum: $label at $path has no 'origin' remote" >&2
    echo "        PRs need a remote: git -C $path remote add origin <url>" >&2
    return 2
  fi

  local actual_repo
  actual_repo=$(git -C "$path" remote get-url origin)
  local actual_norm expected_norm
  actual_norm=$(_canonicalize_repo "$actual_repo")
  expected_norm=$(_canonicalize_repo "$expected_repo")
  if [[ "$actual_norm" != "$expected_norm" ]]; then
    echo "karakum: $label at $path has unexpected origin" >&2
    echo "        expected (from manifest): $expected_norm" >&2
    echo "        actual   (from origin)  : $actual_norm" >&2
    return 2
  fi
}
