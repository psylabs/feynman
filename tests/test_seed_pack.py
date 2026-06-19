import unittest
from unittest.mock import patch

from server import seed_pack


class FakeStorage:
    def __init__(self):
        self.ensured = []

    def ensure_skill_states_for_user(self, user_id):
        self.ensured.append(user_id)

    def get_skill(self, skill_id):
        return {"id": skill_id, "tolerance": {"type": "exact"}}


def _fake_pick(storage, user_id, emit):
    return {"skill_id": "add", "level": 1, "target_fact": None}


def _fake_generate(skill_id, level=None, target=None):
    return {"prompt": "What is 2 plus 2?", "expected": 4.0, "parameters": {"a": 2, "b": 2}}


def _fake_tts(text, emit):
    return {"filename": "abc.wav", "duration_ms": 1000}


def noop(*a, **k):
    pass


class SeedPackTest(unittest.TestCase):
    @patch("server.tts.synthesize", _fake_tts)
    @patch("server.generator.generate", _fake_generate)
    @patch("server.scheduler.pick_drill", _fake_pick)
    def test_builds_manifest(self):
        storage = FakeStorage()
        pack = seed_pack.build_seed_pack(storage, "u1", 3, noop)
        self.assertEqual(pack["user_id"], "u1")
        self.assertEqual(pack["count"], 3)
        self.assertEqual(len(pack["items"]), 3)
        item = pack["items"][0]
        self.assertEqual(item["skill_id"], "add")
        self.assertEqual(item["prompt_text"], "What is 2 plus 2?")
        self.assertEqual(item["expected"], 4.0)
        self.assertEqual(item["tolerance_rule"], {"type": "exact"})
        self.assertEqual(item["audio_url"], "/audio/abc.wav")
        self.assertEqual(item["audio_duration_ms"], 1000)
        self.assertEqual(storage.ensured, ["u1"])

    @patch("server.tts.synthesize", _fake_tts)
    @patch("server.generator.generate", _fake_generate)
    @patch("server.scheduler.pick_drill", _fake_pick)
    def test_clamps_count(self):
        storage = FakeStorage()
        self.assertEqual(seed_pack.build_seed_pack(storage, "u1", 0, noop)["count"], 1)
        self.assertEqual(
            seed_pack.build_seed_pack(storage, "u1", 9999, noop)["count"], seed_pack.MAX_ITEMS
        )


if __name__ == "__main__":
    unittest.main()
