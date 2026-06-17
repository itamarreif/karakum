# Git auth: SSH agent forwarding

In-container `git push`/`pull` authenticate over SSH using a **host SSH agent**
forwarded into the container — **no private keys are ever copied into the image**.
The base image ships `openssh-client` and a pinned GitHub host key; commit identity
is set separately to the agent (`GIT_AUTHOR_*`/`GIT_COMMITTER_*`), so commits are
attributed to the agent while the push is signed by the host key.

The container can only use keys the forwarded agent holds — i.e. exactly what
`ssh-add -l` returns **on the host** for the agent you forward.

> **Runtime: OrbStack only (for now).** karakum bind-mounts the resolved host
> socket directly into the container, which OrbStack (and native Linux) support.
> **Docker Desktop is not yet supported** — it can't bind-mount a host unix socket
> and instead needs its host-services bridge plus host-side default-agent wiring.
> Tracked as a TODO in the scratchpad.

## Choosing the agent: `--ssh-agent`

`launch` takes `--ssh-agent`; the `just shell`/`just claude` recipes expose it as
the **4th positional argument** (after agent, session, project):

| Value | Forwards | Use when |
|-------|----------|----------|
| `system` (default) | `$SSH_AUTH_SOCK` — your host's default agent | keys are loaded via `ssh-add` |
| `1password` | 1Password's agent socket | your GitHub key lives in 1Password |
| `none` | nothing | the session never needs git over SSH |

```bash
just shell takwin my-slug karakum 1password         # via just (positional)
uv run karakum launch --ssh-agent=1password claude takwin my-slug karakum bash
```

A preflight runs `ssh-add -l` against the chosen agent and **warns** (doesn't block)
if it holds no keys — the symptom of a misconfigured agent is an in-container
`Permission denied (publickey)` even when host `git` works.

## Using the 1Password agent (OrbStack)

No host-side default-agent wiring needed — karakum mounts the 1Password socket
directly.

1. Enable it: 1Password → Settings → Developer → **Use the SSH agent**.
2. Confirm it holds your GitHub key:

   ```bash
   SSH_AUTH_SOCK="$HOME/Library/Group Containers/2BUA8C4S2C.com.1password/t/agent.sock" ssh-add -l
   ```

   (That's the default socket path; yours may differ — it's the `IdentityAgent` in
   your `~/.ssh/config`.)
3. Launch with `1password`:

   ```bash
   just shell <agent> <slug> <project> 1password
   ```

## Verify

```bash
just shell <agent> <slug> <project> 1password   # 4th positional = ssh agent
# inside the container:
ssh-add -l                       # lists your GitHub key
ssh -T git@github.com            # "Hi <user>!"  (1Password prompts on the host)
git pull
```
