#!/usr/bin/env bash
# Live, Docker-backed smoke test for `session clean` / `session down`.
#
# Exercises the real `docker run` + container lifecycle the offline pytest suite
# deliberately can't reach (see docs/testing.md). Self-contained: it builds its
# own throwaway session tree + a throwaway container under $KARAKUM_DATA_DIR /
# $KARAKUM_CONFIG_DIR pointed at temp dirs, and cleans up after itself.
#
# Run via `just smoke` (needs `just build` first so the agent image exists).
set -euo pipefail

IMAGE="karakum-agent-claude:latest"
AGENT="smoke"
SLUG="smoketest"
CONTAINER="agent-${AGENT}-${SLUG}-test"

fail() { echo "SMOKE FAIL: $*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || fail "docker not on PATH"
docker image inspect "$IMAGE" >/dev/null 2>&1 \
  || fail "image $IMAGE missing — run \`just build\` first"

WORK="$(mktemp -d)"
cleanup() {
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

# Isolate from the real config/data: empty config dir => no project overrides
# (everything takes the toolchains.yaml autodetect path), temp data dir => our
# fake session is the only one `session clean`/`down` can resolve.
export KARAKUM_DATA_DIR="$WORK/data"
export KARAKUM_CONFIG_DIR="$WORK/config"
SESS="$KARAKUM_DATA_DIR/sessions/$AGENT/$SLUG"

# clone A: a minimal Rust crate with a fake target/ -> autodetect `cargo clean`.
mkdir -p "$SESS/rustproj/.git" "$SESS/rustproj/src" "$SESS/rustproj/target/debug"
cat > "$SESS/rustproj/Cargo.toml" <<'TOML'
[package]
name = "smoke"
version = "0.0.0"
edition = "2021"
TOML
echo 'fn main() {}' > "$SESS/rustproj/src/main.rs"
dd if=/dev/zero of="$SESS/rustproj/target/debug/blob" bs=1024 count=50 2>/dev/null

# clone B: a node project whose clean script drops node_modules
# -> autodetect `npm run clean --if-present`.
mkdir -p "$SESS/nodeproj/.git" "$SESS/nodeproj/node_modules/junk"
cat > "$SESS/nodeproj/package.json" <<'JSON'
{ "name": "smoke", "version": "0.0.0", "scripts": { "clean": "rm -rf node_modules" } }
JSON
echo "keep" > "$SESS/nodeproj/index.js"

echo "== session clean =="
uv run karakum session clean "$SLUG"
[ ! -e "$SESS/rustproj/target" ]       || fail "rust target/ not removed"
[ -f "$SESS/rustproj/Cargo.toml" ]     || fail "rust manifest clobbered"
[ -f "$SESS/rustproj/src/main.rs" ]    || fail "rust source clobbered"
[ ! -e "$SESS/nodeproj/node_modules" ] || fail "node_modules not removed"
[ -f "$SESS/nodeproj/index.js" ]       || fail "node source clobbered"
echo "  OK: target/ + node_modules removed, source intact"

echo "== session down =="
docker run -d --rm --name "$CONTAINER" "$IMAGE" sleep 120 >/dev/null
docker ps --format '{{.Names}}' | grep -qx "$CONTAINER" || fail "test container didn't start"
uv run karakum session down "$SLUG" --yes
! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER" || fail "container still running after down"
echo "  OK: container stopped"

echo "SMOKE PASS"
