# karakum CLI architecture

How the Python CLI is structured and what happens on each `just` invocation.

> Keep this in sync with the code. When you change `karakum/cli.py`'s flow, the
> module split, the docker handoff, or the session/mount contract, update the
> matching section here in the same PR.
>
> For how this code is tested, see [testing.md](testing.md).

## Entry points

`Justfile` recipes are thin shims that shell out to the `karakum` CLI (installed
via `uv pip install -e .`, exposed as a console script by `pyproject.toml`'s
`[project.scripts]`). The package `karakum/` is a [Click](https://click.palletsprojects.com)
app.

```
Justfile recipe          →  shell command
─────────────────────────────────────────────────────────────
just build               →  uv run karakum build           (Docker images)
just install             →  uv pip install -e .            (install the CLI)
just shell  A [P] [S]     →  uv run karakum launch claude A P S bash
just agents              →  uv run karakum agents
just projects            →  uv run karakum projects
just sessions [A]        →  uv run karakum session ls A    (alias: karakum sessions)
just session-rm S [..]   →  uv run karakum session rm S ..
just session-clean S [..]→  uv run karakum session clean S ..  (free build artifacts)
just session-down S [..] →  uv run karakum session down S ..   (stop a stuck container)
just (default)           →  just --list
```

## Files

```
karakum/
  __init__.py     Empty — marks the package.
  __main__.py     `python -m karakum` shim: imports cli.main and calls it.
  cli.py          The Click app. Defines `main` group + commands:
                  launch / build / agents / projects / session (group: ls,
                  rm, clean, down). `sessions` is an alias for `session ls`.
                  Orchestrates everything; ends `launch` by exec'ing
                  `docker compose run`.
  manifest.py     YAML manifest I/O + location resolvers. karakum_root()
                  (the checkout), config_dir() ($KARAKUM_CONFIG_DIR, default
                  ~/.config/karakum), data_dir() ($KARAKUM_DATA_DIR, default
                  ~/.karakum). Locates agents/<n>.yaml & projects/<n>.yaml
                  under config_dir(), loads them, dotted-key getter, ~ path
                  expansion. Pure host-side, no side effects. See
                  docs/configuration.md for the full config model.
  preflight.py    Fail-fast guards: check_tools() (docker on PATH),
                  check_repo() (path is a git repo whose origin matches
                  the manifest). Raises SystemExit(2) on failure.
  session.py      Per-session isolated clone. ensure() clones the source
                  repo into <sessions_root>/<agent>/<slug>/<label> (label =
                  "scratchpad" for the memory repo, else the project name),
                  repoints origin at GitHub, checks out the caller-supplied
                  branch (<agent>/<slug> for a project, <project>/<slug> for
                  memory). Reuses an existing clone, but only if its .git is a
                  real directory (else fails loud). no_session_warning() for
                  the no-slug escape hatch.
  config.py       Optional host settings from <config_dir>/config.yaml (a
                  missing file or key falls back to defaults). sessions_root()
                  / state_root() → where session clones / harness state live
                  (default <data_dir>/sessions and <data_dir>/state, so
                  $KARAKUM_DATA_DIR relocates both).
  cleanup.py      Session enumeration + remove + container control.
                  iter_sessions() scans
                  <sessions_root>/<agent>/<slug>/<label> (real-clone guard);
                  Clone is a frozen dataclass; clone_status() returns dirty +
                  unpushed via parallel ThreadPoolExecutor; pr_states() batches
                  gh calls per repo; remove() rmtree's the session dir + reaps
                  exited agent-<agent>-<slug>-* containers.
                  running_containers()/stop_containers() find + `docker stop`
                  the *running* agent-<agent>-<slug>-* containers (for
                  `session down`).
  secrets.py      Pluggable secret resolution. load() reads the host-wide
                  <config_dir>/secrets.yaml `.secrets` map, dispatches each URI by
                  scheme to a provider (op:// → 1Password, env:// → host env),
                  returns (env_dict, ["-e", VAR, ...]) for docker.
pyproject.toml    setuptools build; deps click + pyyaml; `karakum`
                  console-script entry → karakum.cli:main.
```

**Design split:** `cli.py` is the orchestrator; the other modules are
single-responsibility workers (manifest parsing, preflight checks, session
creation, secret resolution, host config). Workers stay independent except for
shared helpers — `manifest` (path/root lookup, used by `secrets`) and `config`
(host settings, used by `session`). Each fails loud with `SystemExit(2)`.

## Command dispatch

```
just <recipe>
   │
   ▼
uv run karakum <command> ...
   │
   ▼
karakum.cli:main            (click.Group)
   ├── launch   ◄── just shell
   ├── build    ◄── just build
   ├── agents   ◄── just agents
   ├── projects ◄── just projects
   ├── sessions ◄── just sessions   (alias for session ls)
   └── session  ◄── (group)
         ├── ls    ◄── just sessions
         ├── rm    ◄── just session-rm
         ├── clean ◄── just session-clean
         └── down  ◄── just session-down
```

All three of `rm` / `clean` / `down` resolve their `<slug>` argument through the
shared `_resolve_session()` helper, so they share identical no-match and
multi-agent-collision errors.

`agents` / `projects` just glob the manifest dir and print a TSV:

```
agents()  /  projects()
   └─ manifest.config_dir() / "agents" | "projects"
   └─ for each *.yaml:
        manifest.load(path)         → manifest.require → yaml.safe_load
        manifest.get(data, "...")   → dotted-key traversal
      print(name \t path \t repo)
```

`session ls` / `session rm` operate on the session-clone tree via `cleanup`:

```
session_ls(agent?)                        session_rm(slug, --dry-run, --yes)
   └─ cleanup.iter_sessions(agent)           ├─ cleanup.iter_sessions() filter by slug
   └─ clone_status() in parallel             ├─ error if slug matches multiple agents
        (dirty, unpushed via ThreadPool)     ├─ print plan; stop if --dry-run
   └─ pr_states() batched per repo (gh)      └─ confirm (unless --yes) → cleanup.remove(s)
   └─ print(agent label slug pr-state              (rmtree session dir + reap exited containers)
            branch)
```

`session clean` frees build artifacts without touching source or git state. It
runs inside the bundled agent image (`karakum-agent-claude:latest`, so cargo /
npm / uv are present) over the session's host-mounted clones:

```
session_clean(slug, --dry-run)
   ├─ _resolve_session(slug)
   ├─ _clean_builtins(toolchains.yaml)   → [(detect, clean), …] per toolchain
   ├─ _project_clean_map()               → {clone-label: [custom cmd, …]} from projects/*.yaml
   ├─ _clean_script(clones, builtins, custom)
   │     per clone under /work/<label> (set +e, each in a subshell):
   │       • custom commands if the label has them (overrides autodetect), ELSE
   │       • each builtin guarded by `if <detect>; then ( <clean> ); fi`
   ├─ --dry-run → print the docker cmd + script; stop
   ├─ require image (docker image inspect; else "run `karakum build`")
   └─ docker run --rm -v <session.path>:/work -w /work <image> bash -c <script>
```

Per-clone, a project's custom `clean:` and toolchain autodetect are **mutually
exclusive** — a project declaring `clean:` fully replaces autodetect for its
clone (used for monorepos whose relevant package is nested and root-only
autodetect can't reach). The scratchpad/memory clone never maps to a project, so
it always takes the autodetect path.

`session down` kills a stuck/runaway session's container(s) without removing the
clone:

```
session_down(slug, --yes)
   ├─ _resolve_session(slug)
   ├─ cleanup.running_containers(agent, slug)   → running agent-<agent>-<slug>-* names
   ├─ none → "no running containers"; stop
   ├─ confirm (unless --yes)
   └─ cleanup.stop_containers(names)            → docker stop (compose --rm auto-removes)
```

## `launch` flow (the main path)

Driven by `just shell`. Args: `toolchain agent project slug cmd`.

```
cli.launch(toolchain, agent, project, slug, cmd_args)
│
├─1 preflight.check_tools()
│      └─ shutil.which("docker")            # else SystemExit(2)
│
├─2 MEMORY (always)
│   ├─ manifest.load(manifest.agent_path(agent))
│   │     └─ manifest.require → open → yaml.safe_load
│   ├─ memory_path = manifest.expand_path(manifest.get(agent_data,"memory.path"))
│   ├─ memory_repo =                manifest.get(agent_data,"memory.repository")
│   ├─ preflight.check_repo(memory_path, memory_repo, "memory")
│   │     ├─ (path/.git exists?)
│   │     ├─ git -C <path> remote get-url origin      (subprocess)
│   │     └─ _canonicalize(actual) == _canonicalize(expected)?   # else SystemExit(2)
│   │
│   └─ if slug in ("-",""):   no_session = True
│        ├─ ksession.no_session_warning()         # mounts LIVE repo, no clone
│        └─ memory_session = memory_path ; session_name = "main"
│      else:
│        ├─ memory_branch = <project>/<slug> if project else <slug>
│        ├─ memory_session = ksession.ensure(memory_path, agent, slug, "agent", memory_repo, memory_branch)
│        │     ├─ session = config.sessions_root()/<agent>/<slug>/scratchpad
│        │     │            (default root <data_dir>/sessions; config.yaml `sessions_root` override)
│        │     ├─ if it exists: reuse it — but only if <session>/.git is a real
│        │     │            directory (a karakum clone), else SystemExit(2)
│        │     ├─ git -C <repo> remote get-url origin          (capture GitHub URL)
│        │     ├─ git clone --no-local file://<repo> <session>  (independent .git)
│        │     ├─ git -C <session> remote set-url origin <url>
│        │     └─ git -C <session> checkout [-b] <memory_branch>
│        └─ session_name = slug          # slug-only identity (no date); KARAKUM_SESSION
│
├─3 PROJECT (optional, only if project != "-")
│   ├─ manifest.load(manifest.project_path(project))
│   ├─ manifest.expand_path / manifest.get  (path, repository)
│   ├─ preflight.check_repo(project_path, project_repo, "project '...'")
│   ├─ project_session = project_path  (no_session)
│   │                  |  ksession.ensure(project_path, agent, slug, "project", project_repo, "<agent>/<slug>")
│   │                     → <sessions_root>/<agent>/<slug>/<project-name>  on branch <agent>/<slug>
│   └─ project_mount = ~/<project-name>             # mount under container home, not host path
│       project_args = ["-v", "<ps>:<pm>:rw", "-e", "KARAKUM_PROJECT=<pm>"]
│
├─4 SECRETS
│   ├─ ksecrets.load()                        # reads host-wide <config_dir>/secrets.yaml
│   │     └─ for var,ref in secrets.yaml .secrets:
│   │           scheme = ref.split("://")[0]
│   │           _PROVIDERS[scheme](ref):
│   │             ├─ _provider_op(ref)  → op read <ref>        (subprocess)
│   │             └─ _provider_env(ref) → os.environ[VAR]
│   │         returns (env_dict, ["-e", VAR, ...])
│   ├─ env = os.environ | env_dict ; env["MEMORY_SESSION"]=memory_session ; env["MEMORY_MOUNT"]=~/scratchpad
│   └─ (secret values go into env; only "-e VAR" names hit the argv)
│
├─5 BUILD docker argv
│   container_name = f"agent-{agent}-{slug_label}-{uuid4[:6]}"
│   docker_cmd = ["docker","compose","run","--rm","--name",...,
│                 "-e KARAKUM_SESSION/AGENT", "-e KARAKUM_MEMORY=~/scratchpad", *project_args,
│                 *_git_identity_args(agent),            # GIT_AUTHOR/COMMITTER → agent (user+agent@host)
│                 *_ssh_agent_args(),                    # forward host SSH agent (see docs/ssh.md)
│                 *_git_signing_args(),                  # SSH commit signing via that agent
│                 *_terminal_args(),                     # TERM + COLORTERM=truecolor
│                 "-w", "/home/agent", *secret_docker_args,   # always land in ~
│                 f"agent-{toolchain}", cmd, *extra_args]
│
└─6 HANDOFF
    ├─ os.chdir(manifest.karakum_root())    # so compose finds docker-compose.yaml
    └─ os.execvpe("docker", docker_cmd, env)   # replaces the process; no return
```

## Module dependencies

```
cli ─┬─► preflight ──► (subprocess: git; shutil: docker/gh)
     ├─► manifest  ──► (yaml, pathlib, os.environ)   # karakum_root / config_dir / data_dir
     ├─► config    ──► manifest.config_dir, .data_dir, .expand_path ; (yaml)
     ├─► session   ──► config.sessions_root ; (subprocess: git)
     ├─► cleanup   ──► config.sessions_root ; (subprocess: git, gh, docker; ThreadPoolExecutor)
     └─► secrets   ──► manifest.config_dir ; (yaml; subprocess: op, os.environ)

(cli is the only orchestrator; the shared helper is manifest — it owns the three
 location resolvers. session resolves clone roots via config.sessions_root();
 cleanup enumerates the same tree; secrets reads <config_dir>/secrets.yaml via
 manifest.config_dir(); config reads the optional <config_dir>/config.yaml and
 derives session/state roots from manifest.data_dir(). config imports from
 manifest, not the reverse — no cycle.)
```

## Design notes

- **Single exec handoff.** `launch` does all host-side prep (manifests →
  preflight → clone → secrets → argv), then `os.execvpe` *replaces* the Python
  process with `docker compose run`. Nothing after the exec runs; the container's
  `cmd` (`bash`, passed by the `shell` recipe) becomes the foreground process.
  `launch` takes the `cmd` as a trailing argument, so a future recipe could exec
  a different entry point (e.g. `claude`) through the same code path.

- **Static vs dynamic contract.** `docker-compose.yaml` is the static half: it
  declares the toolchain service, the `claude` state mount, and the memory mount
  (host `${MEMORY_SESSION}` → container `${MEMORY_MOUNT}`, i.e. `~/scratchpad`).
  `cli.py` is the dynamic half: it injects per-session flags (`-v` project at
  `~/<name>`, `-w /home/agent`, `-e` env, secret `-e` names, `--name`). Compose
  stays agent/project-agnostic.

- **Three orthogonal axes → three inputs.** toolchain (`agent-<toolchain>`
  service/image) · agent (`<config_dir>/agents/<n>.yaml` → memory) · project
  (`<config_dir>/projects/<n>.yaml`, optional). Secrets are host-wide
  (`<config_dir>/secrets.yaml`), not an axis. `cli.launch` is where the three
  combine.

- **Fail-loud preflight before side effects.** `check_tools` and `check_repo`
  run *before* any clone or secret resolution, so a bad manifest or missing
  remote aborts cleanly (`SystemExit(2)`) rather than half-creating a session.

- **No-slug escape hatch.** `slug` = `-` or omitted skips `session.ensure`:
  `memory_session = memory_path`, mounting the *live* repo RW with a warning —
  the one intentionally non-isolated mode, for quick main-branch work.

- **Isolated session clones.** With a slug, each session is a full independent
  clone (`git clone --no-local file://…`, its own `.git`, no shared objects), so
  the container can never touch the host repo's git database. Branches/commits
  reach the host via GitHub push + pull/PR, not a shared `.git`. See the README
  "Mount contract" for the host-side guarantee.

- **Session layout & identity.** Clones live under one root, grouped by session:
  `<sessions_root>/<agent>/<slug>/<label>` (`label` = `scratchpad` for memory, or
  the project name), so every repo a session touches sits together and outside the
  source repos (no collision with a manual `git worktree add`). The **slug alone**
  is the stable identity (no date) — re-running the same slug, even days later,
  reuses the same clone and branch. `sessions_root` defaults to
  `<data_dir>/sessions` (`$KARAKUM_DATA_DIR`, default `~/.karakum`), overridable
  via the `sessions_root` key in `<config_dir>/config.yaml` (`config.py`).

- **Cleanup is git-derived, not metadata-driven.** `session rm` derives state
  from *live* git + `gh` (working tree, `rev-list` vs `origin`, PR status) rather
  than a launch-time sidecar — so the launch path stays untouched and there's no
  record to keep in sync. `session rm` is explicit with no predicate gate: naming
  a slug deletes its directory unconditionally (after confirmation). `session ls`
  shows current git/gh state so you can decide yourself.

- **Secret hygiene boundary.** `secrets.load` returns values in `env_dict`
  (passed via the `env` arg to `execvpe`, never on the command line) and only
  `-e VAR` *names* in `docker_args`, so resolved secrets never appear in the
  process argv / `ps`.
