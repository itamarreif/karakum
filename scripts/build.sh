#!/usr/bin/env bash
# build.sh — build karakum base + toolchain images.
#
# Usage: build.sh
# Exits: 0 success, non-zero on build failure.

set -euo pipefail

KARAKUM_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$KARAKUM_ROOT"

echo "karakum: building base image"
docker build \
  -t karakum-base:latest \
  --build-arg HOST_UID="$(id -u)" \
  --build-arg HOST_GID="$(id -g)" \
  containers/base

echo "karakum: building toolchain images via compose"
docker compose build
