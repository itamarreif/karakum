ARG RUST_VERSION=1
FROM rust:${RUST_VERSION}-slim-bookworm

# System libs needed to *build* the cargo tools below (RUST_TOOLS). The slim rust
# image has a C toolchain but no -dev headers, so anything pulling openssl-sys
# (sqlx-cli, cargo-audit via native-tls) fails to link without libssl-dev +
# pkg-config. Mirrors the runtime deps baked into karakum-base.
RUN apt-get update && apt-get install -y --no-install-recommends \
      cmake \
      libssl-dev \
      pkg-config \
    && rm -rf /var/lib/apt/lists/*

# rustup components (clippy, rustfmt, ...). The slim image uses the `minimal`
# profile, so these must be added explicitly. They install under RUSTUP_HOME
# (with proxies in CARGO_HOME/bin), both of which the claude image copies out.
ARG RUST_COMPONENTS=""
RUN [ -z "$RUST_COMPONENTS" ] || rustup component add $RUST_COMPONENTS

ARG RUST_TOOLS=""
RUN [ -z "$RUST_TOOLS" ] || cargo install --locked $RUST_TOOLS
