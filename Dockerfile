# uv is taken from Astral's own image (pinned by tag + digest, both kept current by
# Dependabot's docker updates) instead of an unpinned `pip install uv`.
FROM ghcr.io/astral-sh/uv:0.12.18@sha256:3adc3706091ce7c2fe595e669628caedd6d951551b92b258b7e7dbe06d9440bc AS uv

FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DB_PATH=/data/sessions.db

# Pull in Debian's security updates on every build: the digest-pinned base image lags behind
# the security archive (three fixed perl-base CVEs blocked the first release's Trivy gate) and a
# rebuild is cheaper than waiting for the next python:3.12-slim digest. HTTPS mirror because
# plain-http port 80 is blocked on some build hosts.
RUN sed -i 's#http://deb.debian.org#https://deb.debian.org#' /etc/apt/sources.list.d/debian.sources     && apt-get update     && apt-get -y --no-install-recommends upgrade     && apt-get clean     && rm -rf /var/lib/apt/lists/*

# pip is bundled into python:3.12-slim via ensurepip but never invoked anywhere in this
# image - dependencies are installed exclusively through uv sync below. Removing it here
# drops its CVEs from the Trivy gate instead of just carrying an unused, vulnerable binary.
RUN python3 -m pip uninstall --yes --break-system-packages pip setuptools wheel 2>/dev/null || true

COPY --from=uv /uv /uvx /usr/local/bin/

WORKDIR /app

# Install dependencies first so this layer is cached as long as
# pyproject.toml / uv.lock don't change (source changes shouldn't
# trigger a full dependency reinstall).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY README.md ./
COPY app ./app
RUN uv sync --frozen --no-dev

# /data holds the SQLite session store when no Redis is configured. It must exist (and be
# owned by the app user) before the named volume mounts over it - otherwise Docker
# auto-creates the mount point as root, and the non-root user below can't open its sqlite
# files there. uid/gid 10001 matches the Kubernetes Deployment's securityContext.
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /data \
    && chown -R app:app /data /app
USER 10001:10001

ENV PATH="/app/.venv/bin:$PATH"

VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
