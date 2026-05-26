#!/usr/bin/env bash
# list-projects.sh — list configured projects from projects/*.yaml.

set -euo pipefail

KARAKUM_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$KARAKUM_ROOT"

shopt -s nullglob
for f in projects/*.yaml; do
  name=$(yq -r '.name' "$f")
  path=$(yq -r '.path' "$f")
  repo=$(yq -r '.repository' "$f")
  printf "  %-16s path=%s (%s)\n" "$name" "$path" "$repo"
done
