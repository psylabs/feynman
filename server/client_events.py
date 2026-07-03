"""Shared sanitize+emit logic for client telemetry events.

Used by both POST /client-log (single event) and POST /client-log/bulk
(drained from the offline queue) in server/main.py so the two endpoints can't
drift on how a client-submitted `type` string gets turned into a bus topic.
"""

_MAX_TYPE_LEN = 80


def sanitize_type(raw_type) -> str:
    """Lowercase, strip to [a-z0-9._-], cap length, default to 'event'."""
    raw = str(raw_type or "event").lower()
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in raw)
    safe = safe[:_MAX_TYPE_LEN]
    return safe or "event"


def emit_client_event(bus, payload: dict) -> None:
    """Sanitize one client-submitted event dict and re-emit it on the bus as
    client.<type>. A client-provided `created_at` (present on events drained
    from the offline queue, whether via /client-log/bulk or the per-event
    fallback) is renamed to `client_ts` so it never collides with the bus's
    own server-side `ts` field."""
    payload = payload or {}
    safe_type = sanitize_type(payload.get("type"))
    data = dict(payload)
    data.pop("type", None)
    data.pop("ts", None)
    created_at = data.pop("created_at", None)
    if created_at is not None:
        data["client_ts"] = created_at
    bus.emit(f"client.{safe_type}", **data)
