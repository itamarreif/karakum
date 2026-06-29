# Testing principles

How we test karakum, and where the line between automated and manual sits. Keep
this in sync with `tests/` — when a new pattern proves useful, record it here.

Run the suite with `just test` (`uv run --group dev pytest`). It is offline and
fast (sub-second); it must stay that way.

## 1. Offline-first — no Docker, no network, no clock

Every test in `tests/` runs without a Docker daemon, network access, a real git
repo, or wall-clock/random dependence. The real `docker compose run` /
`os.execvpe` launch path and image builds are **not** unit-tested — that seam is
covered by the per-feature live QA checklist in the tracking issue instead (see
§6). This keeps CI hermetic and the inner loop instant.

## 2. Keep logic pure; push I/O to the edges

The most valuable tests target **pure functions** that take plain data and return
plain data. So when you add behavior, extract the logic from the I/O:

- arg/script *builders* return lists/strings from inputs — e.g. `_git_identity_args`,
  `_git_signing_args`, `_terminal_args`, `_clean_builtins`, `_clean_script`,
  `_clean_map_from_projects`.
- the function that *reads files or shells out* is a thin wrapper that calls the
  pure builder — e.g. `_project_clean_map()` loads `projects/*.yaml` then calls
  the pure `_clean_map_from_projects(dicts)`.

Test the pure half directly; test the wrapper only enough to confirm wiring.
If something is hard to test, that's usually a signal to split it.

## 3. Monkeypatch `subprocess.run` at the module boundary

Docker/git/gh calls are mocked by replacing `subprocess.run` on the *module under
test* (`cli.subprocess`, `cleanup.subprocess`), not globally. Return a
`SimpleNamespace(returncode=, stdout=, stderr=)`. The canonical fake is
`_fake_git_config` in `test_cli_args.py` (answers `git config --global <key>`
from a dict; absent key → returncode 1). Assert on the **argv** that was built
and on branching by return code — that's where the regressions we've actually hit
live (the `+agent` email subaddress order, SSH-signing gating, `docker ps`
without `-q`).

## 4. Test command wiring with Click's `CliRunner`

For the command layer, invoke `cli.main` through `CliRunner` with the helpers it
calls stubbed via monkeypatch (`cli.cleanup.iter_sessions`, `cli._project_clean_map`,
`cli.manifest.load/toolchains_path/config_dir`). Assert on `exit_code`, captured
output, and that side-effecting calls (`docker run`, `stop_containers`) happened
— or, for `--dry-run` and abort paths, did **not** happen. Click 8.4 captures
stdout/stderr separately; combine them when asserting on a message that might go
to either (`result.output + result.stderr`). Drive prompts with `input=`.

## 5. Verify generated artifacts for real when it's cheap

When code emits a script/command that another program runs, test that it actually
behaves — not just that the string looks right:

- run the generated bash through real `bash` in a `tmp_path` (rebinding `/work/`
  to the tmp dir) and assert on the filesystem effects — this catches quoting and
  control-flow bugs (`set +e` tolerance, `detect`-guarded execution).
- syntax-check with `bash -n` (feed the script on stdin) over adversarial inputs
  (labels with spaces, chained `&&` commands).

## 6. Guard shipped config; document what stays manual

- **Config guards.** Assert invariants on files we ship, so a future edit fails
  loudly — e.g. every entry in `toolchains.yaml` defines both `detect` and
  `clean`. Load the *repo default* (`manifest.karakum_root()/...`), not the
  config-dir override.
- **Manual/live QA.** Anything needing Docker, a built image, dep installs, or
  container lifecycle goes in the tracking issue as a numbered, copy-pasteable
  checklist (what to run, what to observe) — see
  `session clean`/`down` in the scratchpad issue. Treat that checklist as the
  spec for the part the unit suite can't reach.

## Conventions

- One test file per feature area (`test_cli_args.py`, `test_secrets.py`,
  `test_session_clean.py`); group with `# --- name ---` section comments.
- Test names state the asserted behavior (`test_identity_email_is_user_plus_agent_in_local_part`).
- Use pytest's `monkeypatch` and `tmp_path` fixtures; no global state.
- Add `pytest`-only deps to the `dev` dependency group in `pyproject.toml`.
