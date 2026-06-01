FROM debian:trixie-20260518-slim
#FROM debianbookworm-20260518-slim
LABEL Name=orchestratorservice Version=0.0.1

ARG DEBIAN_FRONTEND=noninteractive
ARG OPENCODE_VERSION=1.15.13
ARG DOTNET_SDK_VERSION=10.0.300

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        python3 \
        python3-pip \
        tar \
        git \
        unzip \
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
ARG NODE_LTS_VERSION=24.14.0
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

# Agent workspace (sessions via --dir); separate from OpenCode config in /app
RUN mkdir -p /workspace && chmod 755 /workspace

WORKDIR /app
COPY image/opencode.json image/AGENTS.md /app/
COPY image/.github /app/.github
COPY image/local_ai_instruction_modules /app/local_ai_instruction_modules
COPY scripts /app/scripts
COPY image/.opencode /app/.opencode

COPY scripts/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 4099

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["opencode", "serve", "--hostname", "0.0.0.0", "--port", "4099", "--log-level", "INFO", "--print-logs"]
