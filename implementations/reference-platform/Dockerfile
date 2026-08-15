FROM python:3.12-alpine@sha256:78098ea6a3a9c6a7727a5d4674e4a44e57e01fac878ee9cb4d24a86bd93916ff

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
    && python -m pip install --no-cache-dir -e '/app/implementations/reference-platform[production]' \
    && adduser -D -u 10001 -h /home/lcp -s /sbin/nologin lcp \
    && mkdir -p /app/data \
    && chown -R lcp:lcp /app/data /app/implementations

# The image has no compiler toolchain or application build dependencies. The
# Kubernetes example additionally enforces read-only rootfs, no privilege
# escalation, and zero Linux capabilities.
USER lcp

EXPOSE 8080
STOPSIGNAL SIGTERM

CMD ["lcp-platform"]
