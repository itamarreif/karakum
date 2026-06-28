ARG RUST_VERSION=1
FROM rust:${RUST_VERSION}-slim-bookworm

ARG RUST_TOOLS=""
RUN [ -z "$RUST_TOOLS" ] || cargo install $RUST_TOOLS
