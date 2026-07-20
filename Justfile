# karakum — container infra for AI agents.
# Recipes here are thin dispatch; logic lives in karakum/.
# Run `just` (no args) to list recipes.
#
# Schema:
#   just shell <agent> <project> <slug>
#   - agent provides identity (memory); project branch is <agent>/<slug>
#   - project names a workspace repo to mount RW; memory branch is <project>/<slug>
#     ('-' for no project — memory-only session, memory branch is just <slug>)
#   - slug names the work; '-' for no session clone (runs on main, with a warning)

set shell := ["bash", "-euo", "pipefail", "-c"]

# Default: list recipes.
default:
    @just --list

# Build base + toolchain + agent images.
build:
    uv run karakum build

# Install karakum CLI into the uv-managed virtual environment.
install:
    uv pip install -e .

# Run the unit test suite (offline, no Docker).
test:
    uv run --group dev pytest

# Live Docker smoke test for session clean/down (needs `just build` first).
smoke:
    bash tests/smoke.sh

# Use '-' for <project> (memory-only) or <slug> (run on main branch).
# Drop into a session shell (in ~): just shell <agent> <project> <slug>
# Then run whichever agent CLI you want: claude, codex, or opencode.
shell agent project="-" slug="-":
    uv run karakum launch {{agent}} {{project}} {{slug}}

# Reopen an existing session by slug (agent + project recovered from disk):
# just resume <slug>  — or <agent>/<slug> if the slug exists under >1 agent.
resume slug:
    uv run karakum resume {{slug}}

# Copy the macOS clipboard image into the session container's /tmp; prints the path
# to hand to the agent. just pngpaste <agent> <slug> [<name>]
pngpaste agent slug name="clip.png":
    uv run karakum pngpaste {{agent}} {{slug}} {{name}}

# List configured agents.
agents:
    uv run karakum agents

# List configured projects.
projects:
    uv run karakum projects

# List session clones + status: just sessions [<agent>]
sessions agent="":
    uv run karakum session ls {{agent}} | column -t

# Remove a session directory: just session-rm <slug> [--dry-run] [--yes]
# <slug> may be qualified as <agent>/<slug> if it exists under more than one agent.
session-rm slug *flags:
    uv run karakum session rm {{slug}} {{flags}}

# Free disk: run each toolchain's clean in the session's clones. just session-clean <slug> [--dry-run]
# <slug> may be qualified as <agent>/<slug> to disambiguate.
session-clean slug *flags:
    uv run karakum session clean {{slug}} {{flags}}

# Stop running containers for a stuck session: just session-down <slug> [--yes]
# <slug> may be qualified as <agent>/<slug> to disambiguate.
session-down slug *flags:
    uv run karakum session down {{slug}} {{flags}}
