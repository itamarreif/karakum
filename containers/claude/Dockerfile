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
COPY --from=karakum-toolchain-rust:latest /usr/local/cargo  /usr/local/cargo
COPY --from=karakum-toolchain-rust:latest /usr/local/rustup /usr/local/rustup
ENV CARGO_HOME=/usr/local/cargo \
    RUSTUP_HOME=/usr/local/rustup \
    PATH=/usr/local/cargo/bin:$PATH

# --- agent harness ---
RUN npm install -g @anthropic-ai/claude-code

# Stay root; the base image's entrypoint renames the `agent` account to the
# launching agent and drops to it. Re-asserting `USER agent` here would bypass
# that and break the rename.
WORKDIR /home/agent
CMD ["bash"]
