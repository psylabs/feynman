from datetime import datetime, timezone, timedelta
from server import srs
from fsrs import Rating

NOW = datetime(2026, 7, 1, tzinfo=timezone.utc)

def test_rate_mapping():
    assert srs.rate(False, 500, 1200) == Rating.Again
    assert srs.rate(True, 3000, 1200) == Rating.Hard
    assert srs.rate(True, 800, 1200) == Rating.Good
    assert srs.rate(True, None, 1200) == Rating.Good  # missing latency: don't punish

def test_review_roundtrip_and_growth():
    card1, due1 = srs.review(None, Rating.Good, NOW)
    assert isinstance(card1, str) and due1 > NOW.timestamp()
    card2, due2 = srs.review(card1, Rating.Good, NOW + timedelta(days=1))
    assert due2 > due1  # interval grows on success

def test_again_resets_sooner_than_good():
    good, due_g = srs.review(None, Rating.Good, NOW)
    bad, due_b = srs.review(None, Rating.Again, NOW)
    assert due_b < due_g

def test_item_state_storage(tmp_path):
    from server.storage import Storage
    st = Storage(tmp_path / "t.db")
    st.upsert_item_state("u1", "mul:6x7", "primitive", "mul.x7", "{}", 1000.0, 3)
    st.upsert_item_state("u1", "sub:18-4", "primitive", "sub.within20", "{}", 500.0, 1)
    st.upsert_item_state("u1", "mul:6x7", "primitive", "mul.x7", "{}", 2000.0, 3)  # upsert
    due = st.due_items("u1", now=1500.0)
    assert [d["item_key"] for d in due] == ["sub:18-4"]  # only overdue, sorted
    assert st.get_item_state("u1", "mul:6x7")["due_at"] == 2000.0
