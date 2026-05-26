ARG NODE_VERSION=22
FROM node:${NODE_VERSION}-bookworm-slim

ARG NODE_TOOLS=""
RUN [ -z "$NODE_TOOLS" ] || npm install -g $NODE_TOOLS
