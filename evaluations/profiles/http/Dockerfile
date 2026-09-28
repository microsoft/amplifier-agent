FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl ca-certificates jq ripgrep \
    && apt-get clean
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

RUN useradd --create-home --shell /bin/bash agent \
    && mkdir -p /workspace \
    && chown agent:agent /workspace

COPY install.sh /opt/setup/install.sh
RUN chmod +x /opt/setup/install.sh

USER agent
ENV HOME=/home/agent
WORKDIR /workspace

CMD ["/opt/setup/install.sh"]
