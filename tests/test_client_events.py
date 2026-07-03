import unittest

from server.client_events import emit_client_event, sanitize_type


class FakeBus:
    def __init__(self):
        self.events = []

    def emit(self, type, **data):
        self.events.append((type, data))


class ClientEventsTest(unittest.TestCase):
    def test_bulk_style_events_emit_one_bus_event_each_with_client_ts(self):
        bus = FakeBus()
        events = [
            {"type": "voice_offline_timing", "created_at": 1000.5, "seed_local_id": 1},
            {"type": "session_start_tap", "created_at": 1001.25, "blocked": False},
            {"type": "offline_question_served", "created_at": 1002.0, "seed_local_id": 2},
        ]
        for e in events:
            emit_client_event(bus, e)

        self.assertEqual(len(bus.events), 3)
        self.assertEqual(
            [t for t, _ in bus.events],
            [
                "client.voice_offline_timing",
                "client.session_start_tap",
                "client.offline_question_served",
            ],
        )
        for (_, data), src in zip(bus.events, events):
            self.assertEqual(data["client_ts"], src["created_at"])
            self.assertNotIn("created_at", data)
            self.assertNotIn("type", data)
            self.assertNotIn("ts", data)

    def test_sanitizes_weird_type_strings(self):
        bus = FakeBus()
        emit_client_event(bus, {"type": "Weird Type!! With Spaces/Slashes"})
        emit_client_event(bus, {})  # missing type entirely
        emit_client_event(bus, {"type": "x" * 200})
        emit_client_event(bus, {"type": "Foo.Bar-Baz_1"})

        self.assertEqual(bus.events[0][0], "client." + sanitize_type("Weird Type!! With Spaces/Slashes"))
        self.assertEqual(bus.events[1][0], "client.event")
        self.assertEqual(bus.events[2][0], "client." + "x" * 80)
        self.assertEqual(bus.events[3][0], "client.foo.bar-baz_1")

    def test_sanitize_type_defaults_and_lowercases(self):
        self.assertEqual(sanitize_type(None), "event")
        self.assertEqual(sanitize_type(""), "event")
        self.assertEqual(sanitize_type("Foo.Bar-Baz_1"), "foo.bar-baz_1")

    def test_ts_field_never_overrides_bus_generated_ts(self):
        bus = FakeBus()
        emit_client_event(bus, {"type": "x", "ts": 999999})
        self.assertNotIn("ts", bus.events[0][1])


class ClientLogBulkEndpointTest(unittest.TestCase):
    """Exercises the same helper the way server/main.py's
    POST /client-log/bulk endpoint uses it: iterate payload['events'],
    call emit_client_event per item, return {"ok": True, "count": N}."""

    def test_bulk_emits_n_events_and_reports_count(self):
        bus = FakeBus()
        payload = {
            "events": [
                {"type": "a", "created_at": 1.0},
                {"type": "b", "created_at": 2.0},
                {"type": "c", "created_at": 3.0},
            ]
        }
        count = 0
        for event in payload.get("events") or []:
            if isinstance(event, dict):
                emit_client_event(bus, event)
                count += 1
        result = {"ok": True, "count": count}

        self.assertEqual(len(bus.events), 3)
        self.assertEqual(result, {"ok": True, "count": 3})


if __name__ == "__main__":
    unittest.main()
