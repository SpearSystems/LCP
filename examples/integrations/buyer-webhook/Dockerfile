# Example image — not for production. Build from the example directory:
#   docker build -t lcp-buyer-webhook examples/integrations/buyer-webhook
#   docker run --rm -p 8090:8090 -e LCP_WEBHOOK_SECRET=... lcp-buyer-webhook
#
# The receiver writes nothing to disk, so it runs with a read-only root
# filesystem and no writable mounts:
#   docker run --read-only --tmpfs /tmp --user 10001:10001 ...
FROM python:3.12-alpine@sha256:e6dae589c1c05976188fd3bd019afd091445fc533ed2ea29001beddc7368a9c8

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY python/receiver.py /app/receiver.py

RUN adduser -D -u 10001 -h /home/lcp -s /sbin/nologin lcp

USER lcp

EXPOSE 8090
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python3 -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8090/healthz', timeout=2).status == 200 else 1)"

# The HMAC secret is read from LCP_WEBHOOK_SECRET; the container refuses to
# start without it. Port 8090 matches the receiver's default.
ENTRYPOINT ["python3", "/app/receiver.py"]
