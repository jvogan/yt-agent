FROM python:3.12-slim AS builder

ARG UV_VERSION=0.11.14

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN python -m pip install --upgrade pip "uv==${UV_VERSION}"

COPY LICENSE README.md pyproject.toml ./
COPY src ./src

RUN uv build


FROM python:3.12-slim

ARG YT_DLP_VERSION=2026.3.17

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /work

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --upgrade pip \
    && python -m pip install "yt-dlp==${YT_DLP_VERSION}"

COPY --from=builder /app/dist/yt_agent-*.whl /tmp/

RUN python -m pip install /tmp/yt_agent-*.whl \
    && rm -f /tmp/yt_agent-*.whl

ENTRYPOINT ["yt-agent"]
