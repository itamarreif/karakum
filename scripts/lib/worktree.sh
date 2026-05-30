#!/usr/bin/env bash
# worktree.sh — manage per-session worktrees in the scratchpad repo.
#
# Source from another script:
#   source "$(dirname "${BASH_SOURCE[0]}")/lib/worktree.sh"
#
# Exposes:
#   worktree_ensure <scratchpad> <agent> <slug>
#     → echoes worktree path on stdout
#     → creates <scratchpad>/.worktrees/YYYYMMDD-<slug>/ on branch <agent>/<slug>
#     → reuses existing worktree if present; diagnostics to stderr

worktree_ensure() {
  local scratchpad=$1
  local agent=$2
  local slug=$3

  local session_name branch worktree
  session_name="$(date +%Y%m%d)-${slug}"
  branch="${agent}/${slug}"
  worktree="${scratchpad}/.worktrees/${session_name}"

  # If this branch is already checked out in a worktree (e.g. a slug resumed on a
  # later day, where the dir is date-stamped but the branch is not), reuse that
  # worktree. Otherwise `git worktree add <branch>` fails with "already used by
  # worktree". Branch → path lookup via git's own porcelain.
  local existing
  existing=$(git -C "$scratchpad" worktree list --porcelain | awk -v b="branch refs/heads/$branch" '
    /^worktree /{wt=substr($0, 10)}
    $0==b{print wt; exit}
  ')
  if [[ -n "$existing" ]]; then
    echo "karakum: reusing worktree $existing for branch $branch" >&2
    echo "$existing"
    return 0
  fi

  if [[ -d "$worktree" ]]; then
    echo "karakum: reusing existing worktree $worktree" >&2
  else
    echo "karakum: creating worktree $worktree on branch $branch" >&2
    if git -C "$scratchpad" show-ref --verify --quiet "refs/heads/$branch"; then
      git -C "$scratchpad" worktree add "$worktree" "$branch" >&2
    else
      git -C "$scratchpad" worktree add -b "$branch" "$worktree" >&2
    fi
  fi

  echo "$worktree"
}
