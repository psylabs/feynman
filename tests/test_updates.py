import unittest

from server.updates import build_manifest

BASE = "https://mini.example:8765"
LATEST = {"version": "abc1234", "checksum": "deadbeef", "file": "abc1234.zip"}


class BuildManifestTest(unittest.TestCase):
    def test_returns_update_when_device_is_behind(self):
        r = build_manifest(LATEST, "old9999", BASE)
        self.assertEqual(r["version"], "abc1234")
        self.assertEqual(r["url"], f"{BASE}/app/bundles/abc1234.zip")
        self.assertEqual(r["checksum"], "deadbeef")

    def test_no_update_when_device_matches_latest(self):
        r = build_manifest(LATEST, "abc1234", BASE)
        self.assertNotIn("version", r)

    def test_no_update_when_nothing_published(self):
        r = build_manifest(None, "abc1234", BASE)
        self.assertNotIn("version", r)

    def test_no_update_when_latest_is_missing_file(self):
        # A partial/hand-corrupted latest.json (valid JSON, has version, no
        # file) must not 500 the endpoint — treat it as nothing to serve.
        r = build_manifest({"version": "abc1234"}, "old9999", BASE)
        self.assertNotIn("version", r)
