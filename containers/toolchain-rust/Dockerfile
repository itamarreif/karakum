ARG RUST_VERSION=1
FROM rust:${RUST_VERSION}-slim-bookworm

# rustup components (clippy, rustfmt, ...). The slim image uses the `minimal`
# profile, so these must be added explicitly. They install under RUSTUP_HOME
# (with proxies in CARGO_HOME/bin), both of which the claude image copies out.
ARG RUST_COMPONENTS=""
RUN [ -z "$RUST_COMPONENTS" ] || rustup component add $RUST_COMPONENTS

ARG RUST_TOOLS=""
RUN [ -z "$RUST_TOOLS" ] || cargo install $RUST_TOOLS
