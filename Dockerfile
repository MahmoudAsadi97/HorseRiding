# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Australia/Perth
WORKDIR /app

# The optional CA secret is ephemeral; TLS verification stays enabled. A combined
# system+proxy bundle can be passed as proxy_ca in managed environments.
RUN --mount=type=secret,id=proxy_ca \
    set -eu; \
    if [ -s /run/secrets/proxy_ca ]; then \
      export SSL_CERT_FILE=/run/secrets/proxy_ca; \
    fi; \
    apt-get update; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends cron tzdata; \
    rm -rf /var/lib/apt/lists/*; \
    ln -snf /usr/share/zoneinfo/Australia/Perth /etc/localtime; \
    echo Australia/Perth > /etc/timezone; \
    groupadd --gid 10001 punting; \
    useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin punting

COPY requirements.lock ./
RUN --mount=type=secret,id=proxy_ca \
    set -eu; \
    if [ -s /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    pip install --no-cache-dir --require-hashes -r requirements.lock

COPY pyproject.toml ./
COPY --chown=10001:10001 src ./src
RUN pip install --no-cache-dir --no-deps --no-build-isolation .
COPY --chown=10001:10001 alembic.ini ./
COPY --chown=10001:10001 migrations ./migrations
COPY --chown=10001:10001 config ./config
COPY --chown=10001:10001 deploy ./deploy
COPY deploy/punting.cron /etc/cron.d/punting
RUN chmod 0644 /etc/cron.d/punting \
    && chmod a+r /app/pyproject.toml \
    && chmod -R a+rX /app/deploy /app/config /app/migrations \
    && chmod 0755 /app/deploy/entrypoint.sh

USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["/app/deploy/entrypoint.sh"]
CMD ["app"]
