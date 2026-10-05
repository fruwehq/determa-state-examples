# syntax=docker/dockerfile:1
FROM python:3.13.7-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ORDER_DATABASE=/data/orders.sqlite3 \
    ORDER_DEFINITION_VERSION=2

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.lock.txt pyproject.toml README.md ./
COPY src ./src
COPY machines ./machines
COPY migrations ./migrations
COPY source-lock.json ./
COPY scripts ./scripts

RUN --mount=type=secret,id=extra_ca \
    if [ -f /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca GIT_SSL_CAINFO=/run/secrets/extra_ca; fi; \
    python -m pip install --no-cache-dir --require-hashes -r requirements.lock.txt \
    && python scripts/install_candidate.py \
    && python -m pip install --no-cache-dir --no-build-isolation --no-deps .

VOLUME ["/data"]
EXPOSE 8000
CMD ["order-service"]
