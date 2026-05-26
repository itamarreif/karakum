# karakum — container infra for AI agents.
# Recipes here are thin dispatch; logic lives in scripts/.
# Run `just` (no args) to list recipes.
#
# Schema:
#   just <toolchain> <agent> <session> [<project>]
#   - toolchain selects the container image
#   - agent provides identity (memory)
#   - session names the work (becomes branch <agent>/<session>)
#   - project (optional) names a workspace repo to mount RW

set shell := ["bash", "-euo", "pipefail", "-c"]

# Default: list recipes.
default:
    @just --list

# Build base + toolchain images.
build:
    bash scripts/build.sh

# Run Claude Code: just claude <agent> <session> [<project>]
claude agent session project="-":
    bash scripts/launch.sh claude {{agent}} {{session}} {{project}} claude

# Drop into bash: just shell <agent> <session> [<project>]
shell agent session project="-":
    bash scripts/launch.sh claude {{agent}} {{session}} {{project}} bash

# List configured agents.
agents:
    bash scripts/list-agents.sh

# List configured projects.
projects:
    bash scripts/list-projects.sh
