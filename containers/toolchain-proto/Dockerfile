# Protobuf toolchain: protoc (the compiler + well-known-type includes) and buf
# (lint/codegen/registry). Both are pinned single binaries pulled from their
# upstream GitHub releases — there's no canonical language image to wrap, so we
# stage them on a slim base and the claude image COPYs the binaries out.

FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      unzip \
    && rm -rf /var/lib/apt/lists/*

# protoc — pinned release from protocolbuffers/protobuf. The zip ships both the
# compiler (bin/protoc) and the well-known-type .proto includes (google/protobuf/*),
# both extracted under /usr/local. protoc's release arch is `aarch_64`/`x86_64`,
# so map `uname -m` (aarch64) onto it.
ARG PROTOC_VERSION
RUN arch="$(uname -m | sed 's/aarch64/aarch_64/')" \
    && curl -fsSL "https://github.com/protocolbuffers/protobuf/releases/download/v${PROTOC_VERSION}/protoc-${PROTOC_VERSION}-linux-${arch}.zip" \
         -o /tmp/protoc.zip \
    && unzip -o /tmp/protoc.zip -d /usr/local bin/protoc 'include/*' \
    && rm /tmp/protoc.zip \
    && chmod +x /usr/local/bin/protoc

# buf — pinned single static binary from bufbuild/buf releases. Asset names are
# `buf-Linux-aarch64` / `buf-Linux-x86_64` (matches uname -s/-m directly).
ARG BUF_VERSION
RUN curl -fsSL "https://github.com/bufbuild/buf/releases/download/v${BUF_VERSION}/buf-$(uname -s)-$(uname -m)" \
      -o /usr/local/bin/buf \
    && chmod +x /usr/local/bin/buf
