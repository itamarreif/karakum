# Git auth: SSH agent forwarding

In-container `git push`/`pull` authenticate over SSH using your **host SSH agent**,
forwarded into the container — **no private keys are ever copied into the image**.
The base image ships `openssh-client` and a pinned GitHub host key; commit identity
is set separately to the agent (`GIT_AUTHOR_*`/`GIT_COMMITTER_*`), so commits are
attributed to the agent while the push is signed by the host key.

The container uses whatever keys your **host default agent** holds — exactly what
`ssh-add -l` returns on the host.

## How it's forwarded

- **macOS (Docker Desktop and OrbStack)** — a host unix socket can't be bind-mounted
  into the VM, so both runtimes expose the host agent inside the VM at the fixed
  bridge `/run/host-services/ssh-auth.sock`. karakum mounts that and points the
  container's `SSH_AUTH_SOCK` at it. The bridge forwards your host **default** agent.
- **Linux** — the host `$SSH_AUTH_SOCK` is bind-mounted directly.

The bridge forwards the *default* agent only — it can't cherry-pick a specific one.
So to use a particular key set (e.g. 1Password's), make that your default agent
(below). Forwarding is automatic; there's no per-launch flag.

A preflight runs `ssh-add -l` against your host agent and **warns** (doesn't block)
if it holds no keys — the symptom otherwise is an in-container
`Permission denied (publickey)` even when host `git` works.

## Using 1Password keys

Make the 1Password agent your host default; then `auto` forwards it.

1. Enable it: 1Password → Settings → Developer → **Use the SSH agent**.
2. Point your host `SSH_AUTH_SOCK` at it (1Password sets this up; the socket is
   referenced as `IdentityAgent` in `~/.ssh/config`, default
   `~/Library/Group Containers/2BUA8C4S2C.com.1password/t/agent.sock`).
3. Confirm it's the default and holds your key:

   ```bash
   ssh-add -l        # lists your GitHub key (no SSH_AUTH_SOCK override needed)
   ```

## Verify

```bash
ssh-add -l                          # host: lists your GitHub key
just shell <agent> <slug> <project>
# inside the container:
ssh-add -l                          # same keys
ssh -T git@github.com               # "Hi <user>!"  (1Password prompts on the host)
git pull
```
