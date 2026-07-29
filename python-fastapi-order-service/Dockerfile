FROM python:3.13.7-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ORDER_DATABASE=/data/orders.sqlite3 \
    ORDER_DEFINITION_VERSION=2

WORKDIR /app

COPY requirements.lock.txt pyproject.toml README.md ./
COPY src ./src
COPY machines ./machines
COPY migrations ./migrations

RUN python -m pip install --no-cache-dir --require-hashes -r requirements.lock.txt \
    && python -m pip install --no-cache-dir --no-build-isolation --no-deps .

VOLUME ["/data"]
EXPOSE 8000
CMD ["order-service"]
