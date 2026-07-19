---
name: session-orient
description: Orient at the start of a karakum session — for the memory (scratchpad) and project clones, show the session branch, its PR, dirty/ahead-behind status, and recent commits, then pull the scratchpad issues behind the work and read them. Use first thing when launched into a session, or any time you need "what's my current PR?", "where am I?", "why am I here?".
user-invocable: true
created: 2026-07-06
---

# Session orientation

A karakum session mounts up to two independent git clones under the container
home, each on its own role-namespaced branch:

| Clone       | Mount (`$VAR`)                     | Branch                                |
|-------------|-----------------------------------|---------------------------------------|
| memory      | `~/<agent>` (`$KARAKUM_MEMORY`)    | `<project>/<slug>` (or bare `<slug>`) |
| project     | `~/<repo>` (`$KARAKUM_PROJECT`)    | `<agent>/<slug>`                      |

The project clone is present only when the session was launched with a project.
Each clone's `origin` points at the repo's GitHub remote, so `gh` resolves the
PR for the session branch directly.

## Use this when

- **First thing after launching into a session** — orient before you touch anything.
- You need the current PR for the memory and/or project repo.
- You want a quick "am I dirty / ahead / behind?" read across both clones.
- You want the issue context behind the session before you start changing things.

Pairs with the `agent-session` skill: `session-orient` reads the state you *arrive*
in; `agent-session` is the branch-per-session + one-PR workflow you then run.

## Run

For each mounted clone — the memory clone (`$KARAKUM_MEMORY`, always present)
and, in a project session, the project clone (`$KARAKUM_PROJECT`) — run these
three and read the raw output:

    git -C "$KARAKUM_MEMORY" status -sb        # branch • dirty • ahead/behind upstream
    git -C "$KARAKUM_MEMORY" log --oneline -5  # what's already on this branch
    (cd "$KARAKUM_MEMORY" && gh pr view --json number,state,isDraft,title,url) 2>/dev/null \
      || echo "no PR for this branch (none yet, or gh not authed)"

Then repeat with `$KARAKUM_PROJECT` in place of `$KARAKUM_MEMORY` when a project
is mounted (skip it for a memory-only session).

## Reading it

- The first line of `status -sb` is the branch, plus `[ahead N, behind M]`
  against its own upstream and a `##`-prefixed dirty summary underneath.
- No PR line → open one at session end (see `agent-session`); if you *expected*
  one, check `gh auth status`.
- Only the memory clone shows in a memory-only session.

## Find related work

Orient on *why*, not just *where*. Scratchpad issues live in
`$KARAKUM_MEMORY/scratchpad/issues/` as `NN-slug.md` (frontmatter: `title`,
`status`, `tags`, `related: [[…]]`, `gh-prs: [N]`). Find the ones behind this
session and read them:

    ISSUES="$KARAKUM_MEMORY/scratchpad/issues"
    # 1. issues linked to this branch's PR (use the PR number from `gh pr view` above)
    grep -rl "gh-prs:.*<PR-number>" "$ISSUES"
    # 2. issues matching the session slug / branch keywords
    grep -ril "$KARAKUM_SESSION" "$ISSUES"

Also check the PR body's `## Refs` (from the `gh pr view` above) for
`scratchpad#<N>`. These signals are hints, not a guarantee — a fresh branch may
have no back-link yet, so fall back to matching keywords against issue titles
and `tags:`. Read the 1–3 most relevant issues before you start changing things.

Orientation only — it reads git/`gh`/issue state and changes nothing.
