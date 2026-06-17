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

# Run Claude Code: just claude <agent> [<session>] [<project>] [<ssh_agent>]
claude agent session="-" project="-" ssh_agent="system":
    uv run karakum launch --ssh-agent={{ssh_agent}} claude {{agent}} {{session}} {{project}} claude

# Drop into bash: just shell <agent> [<session>] [<project>] [<ssh_agent>]
shell agent session="-" project="-" ssh_agent="system":
    uv run karakum launch --ssh-agent={{ssh_agent}} claude {{agent}} {{session}} {{project}} bash

# List configured agents.
agents:
    uv run karakum agents

# List configured projects.
projects:
    uv run karakum projects
