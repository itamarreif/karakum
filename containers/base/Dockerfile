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
      ripgrep \
    && rm -rf /var/lib/apt/lists/*

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

USER agent
WORKDIR /home/agent
CMD ["bash"]
