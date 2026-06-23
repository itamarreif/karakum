---
name: karakum-config
description: Add, remove, or edit karakum agents and projects — the per-entity YAML manifests under $KARAKUM_CONFIG_DIR (default ~/.config/karakum). Use when wiring a new repo into karakum as an agent (memory repo) or project (workspace repo), retiring one, or debugging the "no manifest at …" / "unexpected origin" preflight failures from `just claude`.
user-invocable: true
created: 2026-06-23
---

# Karakum config — agents & projects

karakum has **no `add`/`remove` command**: an agent or project is just a one-file
YAML manifest in the config dir. Managing them = creating/deleting/editing those
files, then verifying with the list commands. This skill is the procedure and the
gotchas.

## Use This When

- Adding a new agent (an identity backed by a memory git repo) or project (a
  workspace git repo the agent acts on).
- Retiring an agent/project and cleaning up the data it left behind.
- A `just claude …` run fails preflight with `no manifest at …` or
  `unexpected origin`.

## Where things live

```
$KARAKUM_CONFIG_DIR        (default ~/.config/karakum)   — edit these
  agents/<name>.yaml       agent identity (memory repo)
  projects/<name>.yaml     project workspace (repo)

$KARAKUM_DATA_DIR          (default ~/.karakum)          — generated, clean up on removal
  sessions/<agent>/<slug>/<label>/   isolated clones
  state/<agent>/                     persistent ~/.claude per agent
```

The **filename basename is the identity** you pass on the CLI (`just claude <agent>
<slug> <project>`), via `manifest.agent_path()` / `project_path()`. The `name:`
field inside is for branch/container labels — keep it equal to the basename to
avoid confusion.

## Add an agent

1. The memory repo must already exist locally as a git repo with an `origin`
   remote (preflight verifies it — see Gotchas).
2. Create `$KARAKUM_CONFIG_DIR/agents/<name>.yaml`:

   ```yaml
   name: <name>
   memory:
     path: ~/code/you/<memory-repo>          # local clone
     repository: github.com/you/<memory-repo> # canonical remote
   ```

   Start from `examples/agents/example.yaml` if unsure.
3. Verify: `karakum agents` (or `just agents`) — the new row should appear.
4. Smoke-test: `just claude <name> -` (no-slug = mounts memory live; warns).

Secrets are **host-wide**, not per-agent — they live in
`$KARAKUM_CONFIG_DIR/secrets.yaml`, shared by every agent. Adding an agent never
touches secrets.

## Add a project

1. The project repo must exist locally with a matching `origin`.
2. Create `$KARAKUM_CONFIG_DIR/projects/<name>.yaml`:

   ```yaml
   name: <name>
   path: ~/code/you/<repo>
   repository: github.com/you/<repo>
   ```
3. Verify: `karakum projects` (or `just projects`).
4. Use it: `just claude <agent> <slug> <name>`.

## Remove an agent / project

Deleting the manifest is step one, but session clones and state under the **data
dir** persist — clean them too or they linger:

```bash
# 1. drop the manifest
rm "$KARAKUM_CONFIG_DIR/agents/<name>.yaml"      # or projects/<name>.yaml

# 2. reap its session clones (per slug)
karakum session ls <name>                         # see what exists
karakum session rm <slug>                         # repeat per slug (asks first)

# 3. (agents only) remove persistent harness state
rm -rf "${KARAKUM_DATA_DIR:-$HOME/.karakum}/state/<name>"
```

Removing a **project** leaves its label clones inside each session
(`sessions/<agent>/<slug>/<project>/`); `session rm <slug>` clears the whole
session dir. There's no project-only reaper.

## Gotchas

- **Origin must match.** Preflight (`check_repo`) compares the local repo's
  `origin` against the manifest `repository`, after canonicalizing both (strips
  `https://` / `http://` / `git@`, the `host:owner` colon, and a trailing `.git`).
  So `github.com/you/r`, `git@github.com:you/r.git`, and
  `https://github.com/you/r` are all equal — but a typo or a fork's origin fails
  with `unexpected origin` and exits 2.
- **Filename, not `name:`, is the lookup key.** `just claude foo …` loads
  `agents/foo.yaml` regardless of the `name:` inside. Mismatched `name:` only
  confuses the listing output.
- **No remote yet?** Preflight refuses a repo with no `origin` (PRs need a
  remote). Add one first: `git -C <path> remote add origin <url>`.
- **Editing is just editing the file** — no reload/cache. The next `just claude`
  reads it fresh.

## Verify

```bash
karakum agents      # name  memory.path  memory.repository   (TSV)
karakum projects    # name  path         repository          (TSV)
```
