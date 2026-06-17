# Git auth: SSH agent forwarding

In-container `git push`/`pull` authenticate over SSH using a **host SSH agent**
forwarded into the container — **no private keys are ever copied into the image**.
The base image ships `openssh-client` and a pinned GitHub host key; commit identity
is set separately to the agent (`GIT_AUTHOR_*`/`GIT_COMMITTER_*`), so commits are
attributed to the agent while the push is signed by the host key.

The container can only use keys the forwarded agent holds — i.e. exactly what
`ssh-add -l` returns **on the host** for the agent you forward.

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

## Runtime setup

How the socket reaches the container differs by runtime:

- **OrbStack / native Linux** — the resolved socket is bind-mounted directly. No
  extra setup.
- **Docker Desktop** — can't bind-mount a host socket, so it forwards the agent
  through its host-services bridge, which proxies the host's **default** agent. You
  must point that default at the right agent **once**:

  ```bash
  just ssh-setup          # launchctl setenv SSH_AUTH_SOCK <1Password socket>
  ```

  Then **restart Docker Desktop** so its backend re-reads the value. (Skip this on
  OrbStack.)

## Using the 1Password agent

1. Enable it: 1Password → Settings → Developer → **Use the SSH agent**.
2. Find its socket (default shown; yours may differ — it's the `IdentityAgent` in
   your SSH config):

   ```bash
   grep -i identityagent ~/.ssh/config ~/.ssh/config.* 2>/dev/null
   # default: ~/Library/Group Containers/2BUA8C4S2C.com.1password/t/agent.sock
   ```
3. Confirm it holds your key:

   ```bash
   SSH_AUTH_SOCK="$HOME/Library/Group Containers/2BUA8C4S2C.com.1password/t/agent.sock" ssh-add -l
   ```
4. On Docker Desktop, run `just ssh-setup` + restart Docker Desktop (see above).

## Verify

```bash
ssh-add -l                       # host: lists your GitHub key
just shell <agent> <slug> <project> 1password   # 4th positional = ssh agent
# inside the container:
ssh-add -l                       # same key appears
ssh -T git@github.com            # "Hi <user>!"  (1Password prompts on the host)
git pull
```

If keys list on the host but not inside the container (Docker Desktop), re-check
`just ssh-setup` ran and Docker Desktop was restarted.
