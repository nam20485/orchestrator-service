# --- Stage 1: Rust Builder (Beads ecosystem: br) ---
# Mirrors the br-only subset of Dockerfile.beads (single source of truth);
# bvr intentionally omitted — this image only needs br (COPY at line 86).
# Pinned to immutable commit SHAs for reproducibility; version comment tracks
# the upstream tag. beads_rust 0.2.15 (via the `asupersync` dependency) uses
# `#![feature]`, which requires the nightly toolchain.
FROM rust:1.95-slim-bookworm AS rust-builder
RUN apt-get update && apt-get install -y --no-install-recommends git pkg-config libssl-dev \
    && rm -rf /var/lib/apt/lists/*
RUN rustup toolchain install nightly && rustup default nightly
# beads_rust v0.2.15 @ d9f8d7083dee46d04a8e4741c5f535eb7fcabc97
RUN cargo install --git https://github.com/Dicklesworthstone/beads_rust.git --rev d9f8d7083dee46d04a8e4741c5f535eb7fcabc97 --locked beads_rust

# --- Stage 2: Final Image ---
FROM debian:trixie-20260518-slim
LABEL Name=orchestratorservice Version=0.0.1

ARG DEBIAN_FRONTEND=noninteractive
ARG OPENCODE_VERSION=1.17.8
#ARG DOTNET_SDK_VERSION=10.0.300
ARG NODE_LTS_VERSION=24.14.0
ARG POWERSHELL_VERSION=7.6.2

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        file \
        git \
        gnupg \
        jq \
        make \
        openssh-client \
        patch \
        procps \
        python3 \
        python3-pip \
        ripgrep \
        tar \
        unzip \
        xz-utils \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# PowerShell (pwsh) — tarball install (Microsoft apt repo fails on trixie SHA1 key policy)
RUN apt-get update \
    && apt-get install -y --no-install-recommends libicu76 libssl3t64 \
    && curl -fsSL "https://github.com/PowerShell/PowerShell/releases/download/v${POWERSHELL_VERSION}/powershell-${POWERSHELL_VERSION}-linux-x64.tar.gz" -o /tmp/powershell.tar.gz \
    && mkdir -p /opt/powershell \
    && tar -xzf /tmp/powershell.tar.gz -C /opt/powershell \
    && chmod +x /opt/powershell/pwsh \
    && ln -sf /opt/powershell/pwsh /usr/local/bin/pwsh \
    && rm /tmp/powershell.tar.gz \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# GitHub CLI
RUN mkdir -p /etc/apt/keyrings \
    && chmod 755 /etc/apt/keyrings \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
       -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
       | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
    && apt-get update && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# Node.js 24.14.0 LTS (needed for MCP server packages)
RUN curl -fsSL "https://nodejs.org/dist/v${NODE_LTS_VERSION}/node-v${NODE_LTS_VERSION}-linux-x64.tar.gz" -o /tmp/node.tar.gz \
    && tar -xzf /tmp/node.tar.gz -C /usr/local --strip-components=1 \
    && rm /tmp/node.tar.gz

# uv (Astral Python package manager)
RUN curl -LsSf https://astral.sh/uv/0.10.9/install.sh | sh \
    && cp /root/.local/bin/uv /usr/local/bin/uv \
    && cp /root/.local/bin/uvx /usr/local/bin/uvx \
    && chmod +x /usr/local/bin/uv /usr/local/bin/uvx

#RUN curl -fsSL https://opencode.ai/install | bash -s -- --no-modify-path
# opencode CLI
RUN curl -fsSL https://opencode.ai/install | bash -s -- --version "${OPENCODE_VERSION}" --no-modify-path \
    && cp /root/.opencode/bin/opencode /usr/local/bin/opencode \
    && chmod +x /usr/local/bin/opencode

ENV PATH="/root/.opencode/bin:${PATH}"

# Beads CLI (br) from the Rust builder stage. Pre-installed so agent sessions
# running the plan-to-beads skill do not bootstrap a toolchain at runtime.
COPY --from=rust-builder /usr/local/cargo/bin/br /usr/local/bin/br

# Agent workspace (sessions via --dir); separate from OpenCode config in /app
RUN mkdir -p /workspace && chmod 755 /workspace

WORKDIR /app
COPY image/ /app/

# The OpenCode config tree ships in image/.opencode (opencode.json, AGENTS.md,
# agents/, commands/, skills/). Install it into the GLOBAL config dir so every
# session loads it regardless of working directory (sessions run in /workspace,
# not /app). opencode.json + AGENTS.md sit side-by-side there, so the
# `instructions: ["AGENTS.md"]` path still resolves. Removed from /app afterward
# so the server cwd (/app) cannot rediscover it as a project .opencode dir.
RUN rm -rf /root/.config/opencode \
    && mkdir -p /root/.config/opencode \
    && cp -r /app/.opencode/. /root/.config/opencode/ \
    && rm -rf /app/.opencode

COPY scripts/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 4099

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["opencode", "serve", "--hostname", "0.0.0.0", "--port", "4099", "--log-level", "INFO", "--print-logs"]
