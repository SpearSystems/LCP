FROM python:3.12-alpine@sha256:e6dae589c1c05976188fd3bd019afd091445fc533ed2ea29001beddc7368a9c8

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    LCP_SCHEMA_DIR=/app/schemas \
    LCP_DATABASE_PATH=/app/data/lcp.sqlite3 \
    LCP_HOST=0.0.0.0 \
    LCP_PORT=8080

WORKDIR /app

COPY schemas /app/schemas
COPY verticals /app/verticals
COPY examples/sandbox /app/examples/sandbox
COPY implementations/reference-platform /app/implementations/reference-platform

RUN python -m pip install --no-cache-dir --upgrade pip 'setuptools>=78.1.1' \
    && apk upgrade --no-cache \
    && python -m pip install --no-cache-dir -e '/app/implementations/reference-platform[production]' \
    && adduser -D -u 10001 -h /home/lcp -s /sbin/nologin lcp \
    && mkdir -p /app/data \
    && chown -R lcp:lcp /app/data /app/implementations

# `apk upgrade` applies Alpine security fixes published after the pinned base
# digest (for example krb5-libs 1.22.3-r0, which fixed 16 HIGH CVEs before the
# upstream python image was rebuilt). The container scan still gates the result.
# The image has no compiler toolchain or application build dependencies. The
# Kubernetes example additionally enforces read-only rootfs, no privilege
# escalation, and zero Linux capabilities.
USER lcp

EXPOSE 8080
STOPSIGNAL SIGTERM

CMD ["lcp-platform"]
