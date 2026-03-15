FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN python -m pip install --upgrade pip uv

COPY LICENSE README.md pyproject.toml ./
COPY src ./src

RUN uv build


FROM python:3.12-slim

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /work

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --upgrade pip \
    && python -m pip install yt-dlp

COPY --from=builder /app/dist/yt_agent-*.whl /tmp/

RUN python -m pip install /tmp/yt_agent-*.whl \
    && rm -f /tmp/yt_agent-*.whl

ENTRYPOINT ["yt-agent"]
