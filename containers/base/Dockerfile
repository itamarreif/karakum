FROM debian:bookworm-slim

ARG HOST_UID=501
ARG HOST_GID=20

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
      bash \
      ca-certificates \
      curl \
      git \
      gnupg \
      jq \
      less \
      ncurses-term \
      openssh-client \
      ripgrep \
      util-linux \
    && rm -rf /var/lib/apt/lists/*

# just is not in Debian repos — install via official script
RUN curl --proto '=https' --tlsv1.2 -sSf https://just.systems/install.sh | bash -s -- --to /usr/local/bin

# Pin GitHub's host key so SSH (via the forwarded host agent) authenticates
# non-interactively — without this, the first connection's known-hosts prompt
# would hang a non-interactive session.
RUN mkdir -p /etc/ssh \
    && ssh-keyscan -t rsa,ecdsa,ed25519 github.com >> /etc/ssh/ssh_known_hosts

# GitHub CLI
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      | install -m 0644 /dev/stdin /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# Non-root user matching host UID/GID. If a system group already owns the
# target GID (e.g. dialout=20 on Debian), rename it rather than collide.
RUN set -eux; \
    existing_group=$(getent group ${HOST_GID} | cut -d: -f1 || true); \
    if [ -n "$existing_group" ]; then \
        groupmod -n agent "$existing_group"; \
    else \
        groupadd -g ${HOST_GID} agent; \
    fi; \
    useradd -m -u ${HOST_UID} -g ${HOST_GID} -s /bin/bash agent

# Clean, host-distinct interactive prompt (appended so it wins over skel's PS1).
COPY prompt.bashrc /tmp/kk-prompt.bashrc
RUN cat /tmp/kk-prompt.bashrc >> /home/agent/.bashrc && rm /tmp/kk-prompt.bashrc

# Entrypoint renames the `agent` account to the launching agent and drops to it.
# The image therefore stays root by default (no `USER agent`); the entrypoint is
# the privilege boundary.
COPY entrypoint.sh /usr/local/bin/karakum-entrypoint
RUN chmod +x /usr/local/bin/karakum-entrypoint

WORKDIR /home/agent
ENTRYPOINT ["/usr/local/bin/karakum-entrypoint"]
CMD ["bash"]
