# LCP Sandbox

This sandbox uses the same reference platform package, schema validation,
offer matcher, HMAC code, persistence, and delivery worker as a live
deployment. It is isolated to synthetic test data and a separate database.

## Start

From the repository root:

```bash
docker compose -f implementations/reference-platform/docker-compose.yml up --build
```

The platform is available at `http://localhost:8080`. The synthetic buyer is
internal to the compose network and listens on port `8090`.

The compose stack bootstraps:

- A sandbox buyer credential.
- A mortgage AU offer with a 30-second ping window.
- The same reference platform used by a live deployment.
- A synthetic buyer that accepts pings at AUD 22 and accepts test posts.

## Submit a synthetic lead

In another terminal:

```bash
python3 examples/sandbox/publisher.py
```

The publisher submits the repository mortgage fixture with a new message ID,
idempotency key, lead ID, and `test: true` marker. The platform validates and
persists it, sends a ping to the synthetic buyer, receives a bid, waits for
the auction window to close, and then sends the winning post.

Follow the logs to see the bid and post. Query the result with the lead ID
returned by the publisher or inspect the sandbox database volume.

## Stop and reset

```bash
docker compose -f implementations/reference-platform/docker-compose.yml down -v
```

Never point this compose file at production credentials, production webhook
URLs, or real consumer data.
