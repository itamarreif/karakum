#!/usr/bin/env bash
# build.sh — build karakum images in tiered order.
#
# Tiered image convention:
#   1. base               — karakum-base: Debian + shared CLI tooling + agent user.
#   2. toolchain-<lang>   — thin wrapper over the canonical upstream image for a
#                           language ecosystem (FROM node:..., FROM python:...),
#                           plus that ecosystem's standard companion tools
#                           (uv for python; tsc/pnpm/... for node).
#   3. agent-<harness>    — sits downstream of *all* toolchains via `COPY --from`,
#                           then installs the agent harness (e.g. claude-code).
#
# Versions and per-toolchain tool lists live in `toolchains.yaml` at the repo
# root. To bump Node, Python, uv, or add a Node-side CLI, edit that file and
# rerun this script — no Dockerfile edits required.
#
# Usage: build.sh
# Exits: 0 success, non-zero on build failure.

set -euo pipefail

KARAKUM_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$KARAKUM_ROOT"

command -v yq >/dev/null || { echo "build.sh: yq is required (brew install yq)" >&2; exit 1; }

NODE_VERSION=$(yq   '.node.version'           toolchains.yaml)
NODE_TOOLS=$(yq     '.node.tools | join(" ")' toolchains.yaml)
PYTHON_VERSION=$(yq '.python.version'         toolchains.yaml)
UV_VERSION=$(yq     '.python.uv_version'      toolchains.yaml)

echo "karakum: building base image"
docker build \
  -t karakum-base:latest \
  --build-arg HOST_UID="$(id -u)" \
  --build-arg HOST_GID="$(id -g)" \
  containers/base

echo "karakum: building toolchain images"
docker build \
  --build-arg NODE_VERSION="$NODE_VERSION" \
  --build-arg NODE_TOOLS="$NODE_TOOLS" \
  -t karakum-toolchain-node:latest containers/toolchain-node

docker build \
  --build-arg PYTHON_VERSION="$PYTHON_VERSION" \
  --build-arg UV_VERSION="$UV_VERSION" \
  -t karakum-toolchain-python:latest containers/toolchain-python

echo "karakum: building agent images via compose"
docker compose build
