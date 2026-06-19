"""Bulk sync helpers for offline-created mobile records."""

from __future__ import annotations

from typing import Any


def _feedback_payload(payload: dict[str, Any]) -> tuple[int | None, str | None]:
    thumb = payload.get("thumb")
    if thumb is not None:
        try:
            thumb = int(thumb)
        except (TypeError, ValueError):
            thumb = None
    reason = (payload.get("reason") or "").strip() or None
    return thumb, reason


def _record_feedback(storage, bus, user_id: str, payload: dict[str, Any]) -> dict:
    session_id = payload.get("session_id")
    if not session_id:
        return {"ok": False, "reason": "session_id_required"}
    session = storage.get_session(session_id)
    if not session:
        return {"ok": False, "reason": "session_not_found"}
    if session.get("user_id") != user_id:
        return {"ok": False, "reason": "session_user_mismatch"}

    attempt_id = payload.get("attempt_id")
    if attempt_id is not None:
        try:
            attempt_id = int(attempt_id)
        except (TypeError, ValueError):
            return {"ok": False, "reason": "invalid_attempt_id"}

    thumb, reason = _feedback_payload(payload)
    if thumb is not None and thumb not in (1, -1):
        return {"ok": False, "reason": "invalid_thumb"}
    if thumb is None and not reason:
        return {"ok": False, "reason": "thumb_or_reason_required"}

    fid = storage.insert_user_feedback(user_id, session_id, attempt_id, thumb, reason)
    bus.emit(
        "feedback.user_submitted",
        id=fid,
        session_id=session_id,
        attempt_id=attempt_id,
        thumb=thumb,
        has_reason=bool(reason),
    )
    return {"ok": True, "id": fid}


def sync_records(orch, storage, bus, user_id: str, records: list[dict]) -> dict:
    """Sync typed outbox records from the mobile client.

    Attempts are recorded first so feedback created against offline attempt
    client IDs can attach to the synthetic server session and attempt rows in
    the same sync call.
    """
    if not isinstance(records, list):
        return {"synced": 0, "records": []}

    results_by_local_id: dict[str, dict] = {}
    attempt_payloads: list[dict] = []
    attempt_records: list[dict] = []
    attempt_map: dict[str, dict] = {}

    for record in records:
        if record.get("kind") != "attempt":
            continue
        payload = record.get("payload") or {}
        attempt_payloads.append(payload)
        attempt_records.append(record)

    if attempt_payloads:
        bulk = orch.record_bulk_attempts(user_id, attempt_payloads)
        for record, attempt_result in zip(attempt_records, bulk.get("attempts", []), strict=False):
            local_id = record.get("local_id")
            client_id = attempt_result.get("client_id") or (record.get("payload") or {}).get("client_id")
            ok = attempt_result.get("attempt_id") is not None
            result = {
                "local_id": local_id,
                "kind": "attempt",
                "ok": ok,
                "client_id": client_id,
                "server_session_id": bulk.get("session_id") if ok else None,
                "server_attempt_id": attempt_result.get("attempt_id"),
                "reason": attempt_result.get("reason"),
            }
            results_by_local_id[local_id] = result
            if ok and client_id:
                attempt_map[client_id] = result

    for record in records:
        if record.get("kind") == "attempt":
            continue
        local_id = record.get("local_id")
        kind = record.get("kind")
        payload = dict(record.get("payload") or {})
        if kind != "review_feedback":
            results_by_local_id[local_id] = {
                "local_id": local_id,
                "kind": kind,
                "ok": False,
                "reason": "unknown_kind",
            }
            continue

        attempt_client_id = payload.get("attempt_client_id")
        mapped = attempt_map.get(attempt_client_id)
        if mapped:
            payload["session_id"] = payload.get("session_id") or mapped.get("server_session_id")
            payload["attempt_id"] = payload.get("attempt_id") or mapped.get("server_attempt_id")
        result = _record_feedback(storage, bus, user_id, payload)
        results_by_local_id[local_id] = {
            "local_id": local_id,
            "kind": "review_feedback",
            **result,
        }

    ordered = [
        results_by_local_id.get(record.get("local_id"), {
            "local_id": record.get("local_id"),
            "kind": record.get("kind"),
            "ok": False,
            "reason": "not_processed",
        })
        for record in records
    ]
    return {
        "synced": sum(1 for result in ordered if result.get("ok")),
        "records": ordered,
    }
