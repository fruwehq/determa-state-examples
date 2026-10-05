# syntax=docker/dockerfile:1
FROM python:3.13.7-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.lock.txt source-lock.json ./
COPY scripts ./scripts
RUN --mount=type=secret,id=extra_ca \
    if [ -f /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca GIT_SSL_CAINFO=/run/secrets/extra_ca; fi; \
    python -m pip install --require-hashes -r requirements.lock.txt \
    && python scripts/install_candidate.py
COPY server.py ./
COPY machines ./machines
EXPOSE 8088
CMD ["python", "server.py", "--address", "0.0.0.0", "--database", "/data/host.sqlite3"]
