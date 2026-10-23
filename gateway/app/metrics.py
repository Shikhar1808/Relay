"""What Prometheus scrapes from `/metrics` on the private port (service token required).

Labels are few and fixed on purpose: `server` is only ever a registered slug or
"unknown", never a name a caller typed, so a caller cannot make new series.
Counters are per process: with several replicas, add them up in the query.
"""
from prometheus_client import Counter, Gauge, Histogram

CALLS = Counter("relay_tool_calls_total", "Tool calls by server and how they ended", ["server", "outcome"])
CALL_SECONDS = Histogram(
    "relay_tool_call_seconds", "Tool call duration by server", ["server"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 30),  # up to CALL_TIMEOUT_SECONDS: the default stops at 10 s
)
UPSTREAM_UP = Gauge("relay_upstream_up", "1 if the server answered its last probe, else 0", ["server"])
REFUSALS = Counter("relay_gateway_refusals_total", "Requests refused before reaching a server", ["reason"])
AUDIT_FAILURES = Counter("relay_audit_write_failures_total", "Audit events that could not be stored")
