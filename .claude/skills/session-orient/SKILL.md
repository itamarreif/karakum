---
name: session-orient
description: Orient at the start of a karakum session — show, for both the memory (scratchpad) and project clones, the session branch, its PR (number/state/url), dirty + ahead/behind status, and recent commits. Use first thing when launched into a session, or any time you need to answer "what's my current PR?", "where am I?", "what's the session state?" for the memory and project repos.
user-invocable: true
created: 2026-07-06
---

# Session orientation

A karakum session mounts up to two independent git clones under the container
home, each on its own role-namespaced branch:

| Clone       | Mount (`$VAR`)              | Branch                              |
|-------------|----------------------------|-------------------------------------|
| memory      | `~/scratchpad` (`$KARAKUM_MEMORY`) | `<project>/<slug>` (or bare `<slug>`) |
| project     | `~/<repo>` (`$KARAKUM_PROJECT`)    | `<agent>/<slug>`                    |

The project clone is present only when the session was launched with a project.
Each clone's `origin` points at the repo's GitHub remote, so `gh` resolves the
PR for the session branch directly.

This skill prints a compact status block for both clones so you know, in one
shot, which branches you're on and whether they already have open PRs — before
you start making changes.

## Use this when

- **First thing after launching into a session** — orient before you touch anything.
- You need the current PR for the memory and/or project repo (`gh pr view` on the
  session branch).
- You want a quick "am I dirty / ahead / behind?" read across both clones.

## Run

Paste this into a shell (`bash`). It reads the `KARAKUM_*` env the launcher
injects and reports on whichever clones are mounted:

```bash
set -uo pipefail
hr() { printf -- '----------------------------------------\n'; }

if [ -z "${KARAKUM_AGENT:-}" ]; then
  echo "Not inside a karakum session (KARAKUM_* unset) — nothing to orient."
else
  printf 'karakum session: %s / %s\n' "$KARAKUM_AGENT" "${KARAKUM_SESSION:-?}"

  orient() {                                   # $1 = role label, $2 = clone path
    local role="$1" dir="$2" branch dirty base ab behind ahead pr
    [ -n "$dir" ] && [ -d "$dir/.git" ] || return 0
    hr
    branch=$(git -C "$dir" branch --show-current)
    dirty=$(git -C "$dir" status --porcelain | wc -l | tr -d ' ')
    # Default branch to diff against: origin/HEAD, else origin/main|master.
    base=$(git -C "$dir" symbolic-ref --quiet refs/remotes/origin/HEAD 2>/dev/null \
             | sed 's@^refs/remotes/@@')
    [ -z "$base" ] && for b in origin/main origin/master; do
      git -C "$dir" rev-parse --verify --quiet "$b" >/dev/null 2>&1 && { base="$b"; break; }
    done

    printf '%-8s %s\n' "$role" "$dir"
    printf '  branch  %s%s\n' "$branch" \
      "$([ "$dirty" -gt 0 ] && printf '   (dirty: %s files)' "$dirty")"
    if [ -n "$base" ]; then
      ab=$(git -C "$dir" rev-list --left-right --count "$base"...HEAD 2>/dev/null) || ab=""
      read -r behind ahead <<<"$ab"
      printf '  vs %s   behind %s / ahead %s\n' "$base" "${behind:-?}" "${ahead:-?}"
    fi
    # PR for this branch — clone origin already points at GitHub.
    pr=$(cd "$dir" && gh pr view "$branch" \
           --json number,state,isDraft,title,url \
           --jq '"#\(.number) \(.state)\(if .isDraft then " (draft)" else "" end)  \(.title)\n          \(.url)"' \
           2>/dev/null)
    printf '  pr      %s\n' "${pr:-no PR for this branch (gh found none / not authed)}"
    printf '  recent\n'
    git -C "$dir" log --oneline -3 2>/dev/null | sed 's/^/          /'
  }

  orient memory  "${KARAKUM_MEMORY:-}"
  orient project "${KARAKUM_PROJECT:-}"
  hr
fi
```

## Reading the output

```
karakum session: alice / fix-login
----------------------------------------
memory   /home/agent/scratchpad
  branch  webapp/fix-login   (dirty: 2 files)
  vs origin/main   behind 0 / ahead 1
  pr      #42 OPEN  Notes on the login fix
          https://github.com/you/scratchpad/pull/42
  recent
          a1b2c3d note the root cause
          ...
----------------------------------------
project  /home/agent/webapp
  branch  alice/fix-login
  vs origin/main   behind 3 / ahead 0
  pr      no PR for this branch (gh found none / not authed)
  recent
          ...
----------------------------------------
```

- **`no PR for this branch`** means either the branch has no PR yet *or* `gh`
  isn't authenticated. If unsure, run `gh auth status`.
- **`behind N`** on the project clone → your base moved; consider rebasing before
  you push.
- Only the memory clone shows when the session was launched without a project.

## Notes

- The mount paths mirror host paths only for the *host* side; inside the
  container they're always `~/scratchpad` and `~/<repo>`. Use the `$KARAKUM_*`
  vars, not hardcoded paths.
- This is orientation only — it reads git/`gh` state and changes nothing.
```
