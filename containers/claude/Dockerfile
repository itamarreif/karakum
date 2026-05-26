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

# --- agent harness ---
RUN npm install -g @anthropic-ai/claude-code

USER agent
WORKDIR /home/agent
CMD ["bash"]
