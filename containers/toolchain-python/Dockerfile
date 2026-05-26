ARG PYTHON_VERSION=3.13
ARG UV_VERSION=latest

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM python:${PYTHON_VERSION}-slim-bookworm
COPY --from=uv /uv  /usr/local/bin/uv
COPY --from=uv /uvx /usr/local/bin/uvx
