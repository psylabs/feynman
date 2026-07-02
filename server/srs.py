"""FSRS wrapper: single place that touches the py-fsrs library.

Cards are stored as JSON blobs in item_state; the scheduler only ever sees
(item_key, due_at). Default FSRS parameters — do not fit personal params
at this data scale (~400 attempts).
"""
import json
from datetime import datetime, timezone

from fsrs import Scheduler, Card, Rating

_scheduler = Scheduler()


def rate(correct: bool, latency_ms: int | None, target_ms: int) -> Rating:
    if not correct:
        return Rating.Again
    if latency_ms is not None and latency_ms > target_ms:
        return Rating.Hard
    return Rating.Good


def review(card_json: str | None, rating: Rating, when: datetime) -> tuple[str, float]:
    card = Card.from_dict(json.loads(card_json)) if card_json else Card()
    card, _log = _scheduler.review_card(card, rating, when)
    # card.due is already tz-aware (UTC) in fsrs>=5.0, so call .timestamp() directly
    return json.dumps(card.to_dict()), card.due.timestamp()
