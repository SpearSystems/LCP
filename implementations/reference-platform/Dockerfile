FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LCP_SCHEMA_DIR=/app/schemas \
    LCP_DATABASE_PATH=/app/data/lcp.sqlite3 \
    LCP_HOST=0.0.0.0 \
    LCP_PORT=8080

WORKDIR /app

COPY schemas /app/schemas
COPY verticals /app/verticals
COPY examples/sandbox /app/examples/sandbox
COPY implementations/reference-platform /app/implementations/reference-platform

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -e '/app/implementations/reference-platform[production]' \
    && useradd --system --uid 10001 --create-home --shell /usr/sbin/nologin lcp \
    && mkdir -p /app/data \
    && chown -R lcp:lcp /app/data /app/implementations

USER lcp

EXPOSE 8080

CMD ["lcp-platform"]
