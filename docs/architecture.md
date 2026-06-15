# karakum CLI architecture

How the Python CLI is structured and what happens on each `just` invocation.

> Keep this in sync with the code. When you change `karakum/cli.py`'s flow, the
> module split, the docker handoff, or the session/mount contract, update the
> matching section here in the same PR.

## Entry points

`Justfile` recipes are thin shims that shell out to the `karakum` CLI (installed
via `uv pip install -e .`, exposed as a console script by `pyproject.toml`'s
`[project.scripts]`). The package `karakum/` is a [Click](https://click.palletsprojects.com)
app.

```
Justfile recipe          →  shell command
─────────────────────────────────────────────────────────────
just build               →  bash scripts/build.sh          (Docker images; not Python)
just install             →  uv pip install -e .            (install the CLI)
just claude A [S] [P]     →  uv run karakum launch claude A S P claude
just shell  A [S] [P]     →  uv run karakum launch claude A S P bash
just agents              →  uv run karakum agents
just projects            →  uv run karakum projects
just sessions [A]        →  uv run karakum sessions A
just clean [A] [S] [..]  →  uv run karakum clean A S ..
just (default)           →  just --list
```

## Files

```
karakum/
  __init__.py     Empty — marks the package.
  __main__.py     `python -m karakum` shim: imports cli.main and calls it.
  cli.py          The Click app. Defines `main` group + 5 commands:
                  launch / agents / projects / sessions / clean.
                  Orchestrates everything; ends `launch` by exec'ing
                  `docker compose run`.
  manifest.py     YAML manifest I/O. Locates agents/<n>.yaml &
                  projects/<n>.yaml, loads them, dotted-key getter,
                  ~ path expansion. Pure host-side, no side effects.
  preflight.py    Fail-fast guards: check_tools() (docker on PATH),
                  check_repo() (path is a git repo whose origin matches
                  the manifest). Raises SystemExit(2) on failure.
  session.py      Per-session isolated clone. ensure() clones the source
                  repo into <sessions_root>/<agent>/<slug>/<label> (label =
                  "scratchpad" for the memory repo, else the project name),
                  repoints origin at GitHub, checks out <agent>/<slug>.
                  Reuses an existing clone, but only if its .git is a real
                  directory (else fails loud). no_session_warning() for the
                  no-slug escape hatch.
  config.py       Optional host settings from ~/.karakum/config.yaml (a
                  missing file or key falls back to defaults). sessions_root()
                  → where session clones live (default ~/.karakum/sessions);
                  cleanup_predicate() → safe-delete rule (default "merged").
  cleanup.py      Session enumeration + reaping. iter_sessions() scans
                  <sessions_root>/<agent>/<slug>/<label> (real-clone guard);
                  git/gh status helpers (dirty, unpushed, pr_merged); a
                  predicate registry (merged | pushed); remove() rmtree's the
                  session dir + reaps exited agent-<agent>-<slug>-* containers.
  secrets.py      Pluggable secret resolution. load() reads the host-wide
                  <repo>/secrets.yaml `.secrets` map, dispatches each URI by
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
   ├── launch   ◄── just claude / just shell
   ├── agents   ◄── just agents
   ├── projects ◄── just projects
   ├── sessions ◄── just sessions
   └── clean    ◄── just clean
```

`agents` / `projects` just glob the manifest dir and print a TSV:

```
agents()  /  projects()
   └─ manifest.karakum_root()
   └─ for each *.yaml:
        manifest.load(path)         → manifest.require → yaml.safe_load
        manifest.get(data, "...")   → dotted-key traversal
      print(name \t path \t repo)
```

`sessions` / `clean` operate on the session-clone tree via `cleanup`:

```
sessions(agent?)                          clean(agent?, slug?, --dry-run/--force/--yes)
   └─ cleanup.iter_sessions(agent)           ├─ cleanup.iter_sessions(agent) (+ slug filter)
   └─ per clone: dirty / unpushed /          ├─ predicate = config.cleanup_predicate()  # default "merged"
        pr_state (gh)                         ├─ if predicate == "merged": preflight.check_gh()
      print(agent slug label branch           ├─ per session: cleanup.session_safe(s, predicate)
            dirty? unpushed pr-state)         │     (predicate holds for ALL clones)  | --force bypass
                                              ├─ print plan; stop if --dry-run
                                              └─ confirm (unless --yes) → cleanup.remove(s)
                                                    (rmtree session dir + reap exited containers)
```

## `launch` flow (the main path)

Driven by `just claude` / `just shell`. Args: `toolchain agent slug [project] cmd`.

```
cli.launch(toolchain, agent, slug, project, cmd_args)
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
│        ├─ memory_session = ksession.ensure(memory_path, agent, slug, "agent", memory_repo)
│        │     ├─ session = config.sessions_root()/<agent>/<slug>/scratchpad
│        │     │            (default root ~/.karakum/sessions; ~/.karakum/config.yaml override)
│        │     ├─ if it exists: reuse it — but only if <session>/.git is a real
│        │     │            directory (a karakum clone), else SystemExit(2)
│        │     ├─ git -C <repo> remote get-url origin          (capture GitHub URL)
│        │     ├─ git clone --no-local file://<repo> <session>  (independent .git)
│        │     ├─ git -C <session> remote set-url origin <url>
│        │     └─ git -C <session> checkout [-b] <agent>/<slug>
│        └─ session_name = slug          # slug-only identity (no date); KARAKUM_SESSION
│
├─3 PROJECT (optional, only if project != "-")
│   ├─ manifest.load(manifest.project_path(project))
│   ├─ manifest.expand_path / manifest.get  (path, repository)
│   ├─ preflight.check_repo(project_path, project_repo, "project '...'")
│   ├─ project_session = project_path  (no_session)
│   │                  |  ksession.ensure(project_path, agent, slug, "project", project_repo)
│   │                     → <sessions_root>/<agent>/<slug>/<project-name>
│   └─ project_args = ["-v", "<ps>:<ps>:rw", "-e", "KARAKUM_PROJECT=<ps>"]
│       cwd = project_session                        # else cwd = memory_session
│
├─4 SECRETS
│   ├─ ksecrets.load()                        # reads host-wide <repo>/secrets.yaml
│   │     └─ for var,ref in secrets.yaml .secrets:
│   │           scheme = ref.split("://")[0]
│   │           _PROVIDERS[scheme](ref):
│   │             ├─ _provider_op(ref)  → op read <ref>        (subprocess)
│   │             └─ _provider_env(ref) → os.environ[VAR]
│   │         returns (env_dict, ["-e", VAR, ...])
│   ├─ env = os.environ | env_dict ; env["MEMORY_SESSION"] = memory_session
│   └─ (secret values go into env; only "-e VAR" names hit the argv)
│
├─5 BUILD docker argv
│   container_name = f"agent-{agent}-{slug_label}-{uuid4[:6]}"
│   docker_cmd = ["docker","compose","run","--rm","--name",...,
│                 "-e KARAKUM_SESSION/AGENT/MEMORY", *project_args,
│                 *_git_identity_args(agent),            # GIT_AUTHOR/COMMITTER → agent
│                 *_ssh_agent_args(),                    # forward host SSH agent (see docs/ssh.md)
│                 "-w", cwd, *secret_docker_args,
│                 f"agent-{toolchain}", cmd, *extra_args]
│
└─6 HANDOFF
    ├─ os.chdir(manifest.karakum_root())    # so compose finds docker-compose.yaml
    └─ os.execvpe("docker", docker_cmd, env)   # replaces the process; no return
```

## Module dependencies

```
cli ─┬─► preflight ──► (subprocess: git; shutil: docker/gh)
     ├─► manifest  ──► (yaml, pathlib)
     ├─► config    ──► manifest.expand_path ; (yaml, os.environ)
     ├─► session   ──► config.sessions_root ; (subprocess: git)
     ├─► cleanup   ──► config.sessions_root/cleanup_predicate ; (subprocess: git, gh, docker)
     └─► secrets   ──► manifest.karakum_root ; (yaml; subprocess: op, os.environ)

(cli is the only orchestrator; the shared helpers are manifest and config —
 session resolves clone roots via config.sessions_root(); cleanup enumerates
 the same tree and reads config.cleanup_predicate(); secrets reads
 <repo>/secrets.yaml via manifest.karakum_root(); config reads the optional
 ~/.karakum/config.yaml)
```

## Design notes

- **Single exec handoff.** `launch` does all host-side prep (manifests →
  preflight → clone → secrets → argv), then `os.execvpe` *replaces* the Python
  process with `docker compose run`. Nothing after the exec runs; the container's
  `cmd` (`claude` or `bash`) becomes the foreground process. That's why
  `just shell` and `just claude` share one code path — only the final `cmd` differs.

- **Static vs dynamic contract.** `docker-compose.yaml` is the static half: it
  declares the toolchain service, the `claude` named volume, and the memory
  mount via `${MEMORY_SESSION}`. `cli.py` is the dynamic half: it injects
  per-session flags (`-v` project, `-w` cwd, `-e` env, secret `-e` names,
  `--name`). Compose stays agent/project-agnostic.

- **Three orthogonal axes → three inputs.** toolchain (`agent-<toolchain>`
  service/image) · agent (`agents/<n>.yaml` → memory) · project
  (`projects/<n>.yaml`, optional). Secrets are host-wide (`secrets.yaml`), not an
  axis. `cli.launch` is where the three combine.

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
  reuses the same clone and branch. `sessions_root` defaults to `~/.karakum/sessions`,
  overridable in `~/.karakum/config.yaml` (`config.py`).

- **Cleanup is git-derived, not metadata-driven.** `clean` infers what's safe to
  delete from *live* git + `gh` state (working tree, `rev-list` vs `origin`, merged
  PRs) rather than a launch-time sidecar — so the launch path stays untouched and
  there's no record to keep in sync. The safe-delete rule is a named predicate
  (`merged` default, `pushed`) selected via `config.cleanup_predicate()`; adding a
  predicate is one entry in `cleanup._PREDICATES`. A session (one `<agent>/<slug>`
  dir spanning ≥1 label clones) is reapable only when the predicate holds for *every*
  clone, so a session with an unpushed/unmerged project clone is never half-deleted.

- **Secret hygiene boundary.** `secrets.load` returns values in `env_dict`
  (passed via the `env` arg to `execvpe`, never on the command line) and only
  `-e VAR` *names* in `docker_args`, so resolved secrets never appear in the
  process argv / `ps`.
