FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /uvx /usr/local/bin/
# git fetches amplifier-agent and its modules from their pinned revisions; tmux lets a checker drive the chat as a
# person would.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git tmux procps \
    && rm -rf /var/lib/apt/lists/*
RUN useradd -m user
# exec runs as this user, in WORKDIR.
USER user
WORKDIR /home/user/personal-assistant
# With uv's default concurrency, about twenty git fetches start at once in the build and the large amplifier-agent
# fetch can crawl for minutes until uv's lock timeout fails the build; two at a time finishes in under a minute.
# The cache mount keeps fetched repositories for the next build, so it is copied from rather than linked.
ENV UV_PYTHON_DOWNLOADS=never \
    UV_CONCURRENT_DOWNLOADS=2 \
    UV_LINK_MODE=copy
# Dependencies first, so a change to the source alone reuses the installed environment.
COPY --chown=user pyproject.toml uv.lock .python-version README.md ./
RUN --mount=type=cache,target=/home/user/.cache/uv,uid=1000,gid=1000 uv sync --locked --no-install-project
COPY --chown=user . .
RUN --mount=type=cache,target=/home/user/.cache/uv,uid=1000,gid=1000 uv sync --locked
