import unittest
from unittest.mock import patch

from server import stt


def _collect():
    events = []
    return events, (lambda type, **kw: events.append({"type": type, **kw}))


class SttSkipProbeTests(unittest.TestCase):
    def test_skip_first_pass_triggers_probe_and_flags_prompt_false_skip(self):
        events, emit = _collect()
        with patch("server.stt._transcribe_once", side_effect=["skip", "4"]) as m:
            out = stt.transcribe("/tmp/answer.webm", emit)

        # Behavior unchanged: still returns the original "skip" transcript.
        self.assertEqual(out["text"], "skip")
        # The probe ran a second transcription with the neutral prompt.
        self.assertEqual(m.call_count, 2)
        self.assertEqual(m.call_args_list[1].args[1], stt._PROBE_PROMPT)
        probe = next(e for e in events if e["type"] == "stt.skip_probe")
        self.assertEqual(probe["alt"], "4")
        self.assertTrue(probe["alt_numeric"])
        self.assertEqual(probe["verdict"], "prompt_false_skip")

    def test_skip_with_unclear_alt_flags_audio_unclear(self):
        events, emit = _collect()
        with patch("server.stt._transcribe_once", side_effect=["skip", "skip"]):
            stt.transcribe("/tmp/answer.webm", emit)

        probe = next(e for e in events if e["type"] == "stt.skip_probe")
        self.assertFalse(probe["alt_numeric"])
        self.assertEqual(probe["verdict"], "audio_unclear")

    def test_numeric_first_pass_skips_the_probe(self):
        events, emit = _collect()
        with patch("server.stt._transcribe_once", side_effect=["13"]) as m:
            stt.transcribe("/tmp/answer.webm", emit)

        self.assertEqual(m.call_count, 1)  # no second call
        self.assertFalse(any(e["type"] == "stt.skip_probe" for e in events))


if __name__ == "__main__":
    unittest.main()
