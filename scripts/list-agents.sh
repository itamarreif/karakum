#!/usr/bin/env bash
# list-agents.sh — list configured agents from agents/*.yaml.

set -euo pipefail

KARAKUM_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$KARAKUM_ROOT"

shopt -s nullglob
for f in agents/*.yaml; do
  name=$(yq -r '.name' "$f")
  mem_path=$(yq -r '.memory.path' "$f")
  mem_repo=$(yq -r '.memory.repository' "$f")
  printf "  %-16s memory=%s (%s)\n" "$name" "$mem_path" "$mem_repo"
done
