# oh-my-pi (omp) setup

[`omp`](https://omp.sh) is one of the five agent CLIs on `PATH` in the agent image
(alongside `claude`, `codex`, `opencode`, and `pi`). You pick it inside the session
shell by running `omp` — it is not a launch argument. It is a fork of
[`pi`](pi.md) with an IDE wired in (LSP, DAP, subagents, its own memory system), and
both are installed: they are different harnesses with different state, not two
versions of one thing.

## How omp is wired

- **Install** — the pinned standalone release binary, not the npm package. The
  published package (`@oh-my-pi/pi-coding-agent`, binary `omp`) declares
  `engines.bun >= 1.3.14` and no node engine, so the image's node toolchain can't
  run it; the release binary bundles its own runtime and needs only glibc +
  libstdc++, both already in the base image.

  The asset is pinned two ways in `containers/agent/Dockerfile`: by tag
  (`OMP_VERSION`) and by content (`OMP_SHA256_X64` / `OMP_SHA256_ARM64`, taken from
  the release's own `SHA256SUMS.txt`). A replaced asset under an already-shipped tag
  therefore fails the build instead of landing in a session. The other four CLIs
  float at npm `latest`; here the pin is free, because the version only moves when
  someone edits that file.

  Cost of this route: the Linux asset is ~240–275 MB, so it is the largest single
  layer in the agent image.

  **To upgrade**, bump `OMP_VERSION` and both hashes, then `just build`:

  ```sh
  curl -fsSL https://github.com/can1357/oh-my-pi/releases/download/v<VERSION>/SHA256SUMS.txt
  ```

  Don't run `omp update` inside a session. `/usr/local/bin/omp` is root-owned and
  the session runs as the agent, so it fails — and anything it did manage to write
  would be discarded with the container (`docker compose run --rm`).

- **State** — omp keeps its config root at `~/.omp` (`PI_CONFIG_DIR`), with the
  default profile's agent dir at `~/.omp/agent` (`PI_CODING_AGENT_DIR`). Like pi, it
  does **not** follow the XDG split, so it's a single per-agent host mount:
  `<state_root>/<agent>-omp` → `~/.omp`. The launcher also creates `~/.omp/agent`,
  so a `memory.init` hook has a parent dir to write into.

  karakum enforces **no** settings on omp, for the same reason as pi plus one more:
  omp's first-run setup scene *is* the model/provider picker, so seeding a config
  would take away the choice. Your picks persist in the mount across sessions.
  `OMP_SKIP_SETUP=1` skips that scene if you ever want it out of the way.

- **Auth** — entirely **env-injected via the secrets pipeline**, the same keys the
  other CLIs already use. Nothing interactive, nothing written to disk:

  | Env var | Use |
  |---|---|
  | `ANTHROPIC_OAUTH_TOKEN` | **Claude Pro/Max subscription** (takes precedence over the API key) |
  | `ANTHROPIC_API_KEY` | Anthropic API billing |
  | `OPENAI_API_KEY` | OpenAI API billing |

  omp supports 60+ providers, each with its own key — if you want one, it's one more
  line in `secrets.yaml`, resolved and injected like the rest. Auth selection per
  host lives in `<config-dir>/secrets.yaml` (see [configuration](configuration.md)
  and [`examples/secrets.yaml`](../examples/secrets.yaml)).

### ⚠️ omp and pi share the `PI_*` env namespace

omp reads `PI_CONFIG_DIR`, `PI_CODING_AGENT_DIR`, `PI_PROXY`, and more — the same
names pi reads. Both CLIs already default into their own mount (`~/.pi`, `~/.omp`),
which is why karakum sets **neither** dir override in `docker-compose.yaml`. If you
add one, it will point *both* harnesses at the same state.

## Wiring the agent's master prompt (scratchpad)

omp's native global context file is **`~/.omp/agent/AGENTS.md`** — the exact analog
of claude's `~/.claude/CLAUDE.md` and pi's `~/.pi/agent/AGENTS.md`. It is loaded as
*context* on top of omp's built-in system prompt, which is what a persona/workflow
layer wants.

Two details specific to omp:

- It can also read foreign user-level files (`~/.claude/CLAUDE.md`,
  `~/.codex/AGENTS.md`, `~/.config/opencode/AGENTS.md`, …), but only behind an
  `enabledProviders` opt-in — so don't count on the claude link doing double duty.
- Only **one** user-level context file loads, and the native file shadows every
  foreign one. Linking `~/.omp/agent/AGENTS.md` therefore makes the choice explicit
  rather than depending on which other CLI's dotfile happens to exist.

`~/.omp/agent/RULES.md` is the escalation if you ever need the prompt carried in the
system prompt on every request rather than as opening context. It's additive — it
doesn't replace the `AGENTS.md` link.

## Wiring skills

This is the one place omp is **not** symmetric with pi: there is no global skills
directory to link. omp discovers skills from `~/.omp/agent/managed-skills/` (skills
it writes itself), plugin packages, the project's `.omp/skills/` and
`.github/skills/`, and anything listed in the `skills.customDirectories` setting.
Scanning is non-recursive — one level of `<root>/<skill-name>/SKILL.md`, which is
exactly the vault's layout.

So the vault's skills need a config key rather than a symlink:

```yaml
# ~/.omp/agent/config.yml
skills:
  customDirectories:
    - /home/<agent>/<agent>/skills   # $KARAKUM_MEMORY/skills
```

Write it from the agent's `memory.init` hook (below), not from karakum — karakum
runs the hook verbatim and stays framework-agnostic. There is also an `omp config
set <key> <value>` CLI; whether it accepts a list value in one call is untested
here, so the snippet writes the file.

**Append when the key is missing, don't write only when the file is missing.** omp's
own first run creates `config.yml` (your model pick from the setup scene), so a
`[ -f config.yml ] ||` guard would quietly never fire again on any state dir where
omp has already run — skills would stay unwired with nothing to show why. Keying the
guard on the *setting* instead is idempotent either way:

```sh
grep -qs customDirectories "$HOME/.omp/agent/config.yml" \
  || printf 'skills:\n  customDirectories:\n    - %s/skills\n' "$KARAKUM_MEMORY" \
     >> "$HOME/.omp/agent/config.yml"
```

The one case this gets wrong: if your `config.yml` already has a top-level `skills:`
block for something else, appending a second one makes a duplicate key and YAML keeps
only the last. Add the directory with `omp config set` by hand in that case.

## The `memory.init` hook

`memory.init` in `agents/<name>.yaml` runs in-container after the mounts land, from
`$KARAKUM_MEMORY`. Adding omp to an existing hook is two lines — one link, one
config file:

```yaml
memory:
  path: ~/code/you/your-memory-repo
  repository: github.com/you/your-memory-repo
  init: |
    mkdir -p "$HOME/.pi/agent" "$HOME/.omp/agent"
    ln -sfn "$KARAKUM_MEMORY/MASTER_PROMPT.md" "$HOME/.claude/CLAUDE.md"
    ln -sfn "$KARAKUM_MEMORY/MASTER_PROMPT.md" "$HOME/.config/opencode/AGENTS.md"
    ln -sfn "$KARAKUM_MEMORY/MASTER_PROMPT.md" "$HOME/.codex/AGENTS.md"
    ln -sfn "$KARAKUM_MEMORY/MASTER_PROMPT.md" "$HOME/.pi/agent/AGENTS.md"
    ln -sfn "$KARAKUM_MEMORY/MASTER_PROMPT.md" "$HOME/.omp/agent/AGENTS.md"
    ln -sfn "$KARAKUM_MEMORY/skills"           "$HOME/.claude/skills"
    ln -sfn "$KARAKUM_MEMORY/skills"           "$HOME/.pi/agent/skills"
    grep -qs customDirectories "$HOME/.omp/agent/config.yml" \
      || printf 'skills:\n  customDirectories:\n    - %s/skills\n' "$KARAKUM_MEMORY" \
         >> "$HOME/.omp/agent/config.yml"
```

The links are re-made every session (`ln -sfn` is idempotent), so a rebuild re-wires
them. The config line adds the skills directory once and then finds it already there,
so your own `omp config set` edits — a model role, a theme — live alongside it
untouched; drop the `customDirectories` lines to unwire the vault's skills.

Once a hook grows past a few lines, keep the shell **in the vault** and let the
manifest call it: `init: ./scripts/init-clis.sh` (the hook's cwd is
`$KARAKUM_MEMORY`). The knowledge of what a vault contains then lives in the vault,
version-controlled with it, instead of inside a YAML string on each machine.

A `memory.init` failure is **non-fatal** and silent, which is why the `mkdir -p` is
there: without it, a missing `~/.omp/agent` leaves you in the confusing half-state
where only some CLIs are wired.

## Verify

After `just build`, drop into a session:

```bash
just shell <agent> - omp-smoke
# inside the container:
command -v omp                  # -> /usr/local/bin/omp
omp --version                   # matches OMP_VERSION in the Dockerfile
readlink -f ~/.omp/agent/AGENTS.md   # -> the vault's MASTER_PROMPT.md
omp config get skills.customDirectories   # -> the vault's skills dir
# personal: confirm the subscription token is present (do NOT print its value)
[ -n "$ANTHROPIC_OAUTH_TOKEN" ] && echo "oauth token injected"
# work: confirm the API keys are present
[ -n "$ANTHROPIC_API_KEY" ] && [ -n "$OPENAI_API_KEY" ] && echo "api keys injected"
omp -p "reply with the single word: ok"   # end-to-end: a real model round-trip
```

A successful `omp -p` round-trip proves the auth path end-to-end. If it fails on
auth with the subscription token, re-mint it on the host with `claude setup-token`
and update the 1Password item — the same token drives `claude`, `pi`, and `omp`.

State lands in `<state_root>/<agent>-omp` on the host; `ls` it after the session to
confirm the mount persisted your model pick.
