# karakum — container infra for AI agents.
# Recipes here are thin dispatch; logic lives in karakum/.
# Run `just` (no args) to list recipes.
#
# Schema:
#   just <toolchain> <agent> [<session>] [<project>]
#   - toolchain selects the container image
#   - agent provides identity (memory)
#   - session names the work (becomes branch <agent>/<session>); '-' or omit for no session clone
#   - project (optional) names a workspace repo to mount RW

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

# Run the unit test suite.
test:
    uv run --group dev pytest

# Run Claude Code: just claude <agent> [<session>] [<project>]
claude agent session="-" project="-":
    uv run karakum launch claude {{agent}} {{session}} {{project}} claude

# Drop into bash: just shell <agent> [<session>] [<project>]
shell agent session="-" project="-":
    uv run karakum launch claude {{agent}} {{session}} {{project}} bash

# Copy the macOS clipboard image into the session container's /tmp; prints the path
# to hand to the agent. just pngpaste <agent> <session> [<name>]
pngpaste agent session name="clip.png":
    uv run karakum pngpaste {{agent}} {{session}} {{name}}

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
session-rm slug *flags:
    uv run karakum session rm {{slug}} {{flags}}

# Free disk: run each toolchain's clean in the session's clones. just session-clean <slug> [--dry-run]
session-clean slug *flags:
    uv run karakum session clean {{slug}} {{flags}}

# Stop running containers for a stuck session: just session-down <slug> [--yes]
session-down slug *flags:
    uv run karakum session down {{slug}} {{flags}}
