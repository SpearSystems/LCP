#!/usr/bin/env python3
"""Submit synthetic lead data to the LCP sandbox."""

from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
with (ROOT / "examples" / "lead.json").open(encoding="utf-8") as handle:
    lead = json.load(handle)
message = lead["lcp"]["message"]
message["id"] = str(uuid4())
message["timestamp"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
message["idempotency_key"] = f"sandbox-publisher-{uuid4().hex}"
message["test"] = True
lead["lcp"]["payload"]["lead_id"] = f"sandbox-lead-{uuid4().hex[:8]}"
body = json.dumps(lead).encode()
request = Request(
    "http://localhost:8080/v1/lcp/leads",
    data=body,
    headers={"Content-Type": "application/json", "X-LCP-Test": "true"},
    method="POST",
)
with urlopen(request, timeout=10) as response:
    print(response.read().decode())
