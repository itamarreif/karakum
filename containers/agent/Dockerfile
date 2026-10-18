FROM karakum-base:latest

USER root

# --- node + ecosystem CLIs (tsc, pnpm) from official node image ---
COPY --from=karakum-toolchain-node:latest /usr/local/bin/node         /usr/local/bin/node
COPY --from=karakum-toolchain-node:latest /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -sf ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 && ln -sf ../lib/node_modules/typescript/bin/tsc /usr/local/bin/tsc \
 && ln -sf ../lib/node_modules/pnpm/bin/pnpm.cjs  /usr/local/bin/pnpm

# --- python + uv from official python image (uv copied in upstream stage) ---
COPY --from=karakum-toolchain-python:latest /usr/local/bin/python3 /usr/local/bin/python3
COPY --from=karakum-toolchain-python:latest /usr/local/bin/uv      /usr/local/bin/uv
COPY --from=karakum-toolchain-python:latest /usr/local/bin/uvx     /usr/local/bin/uvx
COPY --from=karakum-toolchain-python:latest /usr/local/lib/        /usr/local/lib/
RUN ln -sf python3 /usr/local/bin/python

# --- rust + cargo (rustup toolchain from official rust image) ---
# The official rust image installs via rustup: the binaries in /usr/local/cargo/bin
# (cargo, rustc, rustup, ...) are proxies that dispatch to the real toolchain under
# /usr/local/rustup. So copy both dirs and set CARGO_HOME/RUSTUP_HOME (the install
# dirs are non-default, so the proxies can't find the toolchain without them).
#
# CARGO_HOME is the registry/build cache cargo writes to at runtime, so it must be
# owned by the `agent` account (uid/gid bake to the host's). Without --chown it
# lands root-owned and the dropped-privilege user can't create registry/cache —
# `cargo check` then dies with "Permission denied". RUSTUP_HOME is read-only at
# runtime (toolchain dispatch only), so it can stay root-owned.
COPY --from=karakum-toolchain-rust:latest --chown=agent:agent /usr/local/cargo /usr/local/cargo
COPY --from=karakum-toolchain-rust:latest /usr/local/rustup /usr/local/rustup
ENV CARGO_HOME=/usr/local/cargo \
    RUSTUP_HOME=/usr/local/rustup \
    PATH=/usr/local/cargo/bin:$PATH

# --- protobuf (protoc + buf, from the proto toolchain image) ---
# protoc needs its well-known-type includes (/usr/local/include/google/protobuf)
# alongside the binary, or prost/tonic codegen can't resolve `import "google/..."`.
COPY --from=karakum-toolchain-proto:latest /usr/local/bin/protoc      /usr/local/bin/protoc
COPY --from=karakum-toolchain-proto:latest /usr/local/bin/buf         /usr/local/bin/buf
COPY --from=karakum-toolchain-proto:latest /usr/local/include/google /usr/local/include/google

# --- agent harnesses ---
# Five interchangeable agent CLIs live on PATH; the user picks one after
# `just shell`. All authenticate purely from injected env vars (secrets pipeline),
# no interactive login: claude from CLAUDE_CODE_OAUTH_TOKEN (or ANTHROPIC_API_KEY);
# opencode + codex + pi + omp from ANTHROPIC_API_KEY / OPENAI_API_KEY. pi and omp
# also drive a Claude subscription from ANTHROPIC_OAUTH_TOKEN (an sk-ant-oat token,
# same kind as CLAUDE_CODE_OAUTH_TOKEN) — see examples/secrets.yaml.
RUN npm install -g @anthropic-ai/claude-code opencode-ai @openai/codex

# pi (https://pi.dev) ships on its own layer because its official install line
# uses `--ignore-scripts` (a global npm flag — applying it to the line above could
# break the others' postinstall). pi keeps all state under ~/.pi/agent, so no XDG
# parent pre-creation is needed (unlike opencode below).
RUN npm install -g --ignore-scripts @earendil-works/pi-coding-agent

# oh-my-pi (https://omp.sh) — a fifth CLI, and the only one NOT installed from
# npm. Its published package (@oh-my-pi/pi-coding-agent, binary `omp`) declares
# `engines.bun >= 1.3.14` and no node engine, so the node toolchain above can't
# run it; installing from the registry would mean carrying bun as a fifth
# toolchain to host a single CLI. The release binary bundles its own runtime, so
# it needs nothing but glibc + libstdc++ (already here via build-essential).
#
# Pinned by version AND by content hash: the sha256s below are the ones in the
# release's own SHA256SUMS.txt, so a rebuild fails loudly if an asset is ever
# replaced under a tag we already shipped. The other four CLIs float at npm
# `latest` — here the pin is free, because the version only moves when someone
# edits this file. Rebuild to upgrade; don't run `omp update` in a session (it
# would try to overwrite this root-owned binary as the agent user, and anything
# it did manage to write is thrown away with the container).
#
# To bump: set OMP_VERSION, then replace both hashes from
#   curl -fsSL https://github.com/can1357/oh-my-pi/releases/download/v<VERSION>/SHA256SUMS.txt
ARG OMP_VERSION=18.10.4
ARG OMP_SHA256_X64=ff59fc1b484c1308a7247ad208305005627b3fac62b440737c2b1835ac32074e
ARG OMP_SHA256_ARM64=58d711d56fa0c95d208c150575dfdb08fe8fc1262e3820d2d900f0a5e750110b
RUN set -eux; \
    case "$(uname -m)" in \
      x86_64 | amd64)  asset="omp-linux-x64";   sha="${OMP_SHA256_X64}"   ;; \
      aarch64 | arm64) asset="omp-linux-arm64"; sha="${OMP_SHA256_ARM64}" ;; \
      *) echo "oh-my-pi: no release asset for $(uname -m)" >&2; exit 1 ;; \
    esac; \
    curl --proto '=https' --tlsv1.2 -fsSL -o /usr/local/bin/omp.download \
      "https://github.com/can1357/oh-my-pi/releases/download/v${OMP_VERSION}/${asset}"; \
    echo "${sha}  /usr/local/bin/omp.download" | sha256sum -c -; \
    chmod 0755 /usr/local/bin/omp.download; \
    mv /usr/local/bin/omp.download /usr/local/bin/omp; \
    omp --version

# opencode/codex follow the XDG base-dir spec and create ~/.config, ~/.local/share,
# and ~/.local/state at startup. compose bind-mounts *nested* paths under these
# (~/.config/opencode, ~/.local/share/opencode); Docker would otherwise auto-create
# the parents (~/.local, ~/.local/share) root-owned, and the dropped-privilege agent
# user couldn't then create a sibling like ~/.local/state → EACCES. Pre-create the
# parents agent-owned so the mounts land inside writable dirs.
RUN mkdir -p /home/agent/.config /home/agent/.local/share /home/agent/.local/state \
 && chown -R agent:agent /home/agent/.local /home/agent/.config

# Stay root; the base image's entrypoint renames the `agent` account to the
# launching agent and drops to it. Re-asserting `USER agent` here would bypass
# that and break the rename.
WORKDIR /home/agent
CMD ["bash"]
