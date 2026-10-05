# syntax=docker/dockerfile:1
FROM rust:1.86-bookworm AS builder
WORKDIR /app
COPY Cargo.toml Cargo.lock ./
COPY machines ./machines
COPY src ./src
RUN --mount=type=secret,id=extra_ca \
    if [ -f /run/secrets/extra_ca ]; then export CARGO_HTTP_CAINFO=/run/secrets/extra_ca GIT_SSL_CAINFO=/run/secrets/extra_ca; fi; \
    cargo build --release --locked

FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 10001 app
COPY --from=builder /app/target/release/determa-change-control /usr/local/bin/determa-change-control
RUN mkdir -p /data && chown app:app /data
USER app
ENV CHANGE_CONTROL_ADDRESS=0.0.0.0:8080
ENV CHANGE_CONTROL_DATABASE=/data/change-control.sqlite3
EXPOSE 8080
HEALTHCHECK --interval=5s --timeout=2s --start-period=10s --retries=12 CMD curl --fail http://127.0.0.1:8080/health || exit 1
ENTRYPOINT ["determa-change-control"]
