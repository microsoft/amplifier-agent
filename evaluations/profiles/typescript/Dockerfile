FROM node:22-bookworm-slim AS node

FROM python:3.13-slim

# binutils: PyInstaller inspects the native runtime's libraries with objdump.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl ca-certificates jq ripgrep binutils \
    && apt-get clean
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && npm install --global pnpm@11.25.0

RUN useradd --create-home --shell /bin/bash agent \
    && mkdir -p /workspace \
    && chown agent:agent /workspace

COPY install.sh /opt/setup/install.sh
RUN chmod +x /opt/setup/install.sh

USER agent
ENV HOME=/home/agent
WORKDIR /workspace

CMD ["/opt/setup/install.sh"]
