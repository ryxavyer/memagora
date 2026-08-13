# MemAgora team server.
#
# Build context is the REPOSITORY ROOT, not this directory — the image needs
# both `contracts/` (the shared wire format) and `agora/`:
#
#     docker build -f agora/Dockerfile -t memagora-agora .
#
# docker-compose.yml already sets `context: ..` for this reason.

FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# contracts first: it satisfies agora's `memagora-contracts` requirement from
# the local source tree, so the install never reaches for a package index that
# does not publish it.
COPY contracts/ /app/src/contracts/
RUN pip install /app/src/contracts

COPY agora/ /app/src/agora/
RUN pip install /app/src/agora

# Unprivileged at runtime. The server writes nothing to disk when backed by
# Postgres; a SQLite deployment mounts a writable volume for its database.
RUN useradd --create-home --uid 10001 agora \
    && mkdir -p /data \
    && chown agora:agora /data
USER agora

ENV AGORA_HOST=0.0.0.0 \
    AGORA_PORT=8000 \
    AGORA_SQLITE_PATH=/data/agora.sqlite3

EXPOSE 8000

# The unauthenticated health form exists precisely for this: liveness without
# credentials, and without disclosing anything about the deployment.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"

CMD ["agora-server"]
