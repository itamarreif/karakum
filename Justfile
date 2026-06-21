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

# Build base + toolchain images.
build:
    bash scripts/build.sh

# Install karakum CLI into the uv-managed virtual environment.
install:
    uv pip install -e .

# Run Claude Code: just claude <agent> [<session>] [<project>]
claude agent session="-" project="-":
    uv run karakum launch claude {{agent}} {{session}} {{project}} claude

# Drop into bash: just shell <agent> [<session>] [<project>]
shell agent session="-" project="-":
    uv run karakum launch claude {{agent}} {{session}} {{project}} bash

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
