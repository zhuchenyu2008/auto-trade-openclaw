from __future__ import annotations

import unittest
from pathlib import Path

from tradeclaw.public_channel import PublicChannelWatcher


FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


class PublicChannelWatcherTests(unittest.TestCase):
    def test_detects_new_and_edit_events(self):
        watcher = PublicChannelWatcher({"channels": {}})
        html_v1 = (FIXTURES / "channel_sample_v1.html").read_text(encoding="utf-8")
        html_v2 = (FIXTURES / "channel_sample_v2.html").read_text(encoding="utf-8")

        msgs_v1 = watcher.parse_messages("demochan", html_v1)
        self.assertEqual([m.post_id for m in msgs_v1], [1001, 1002])
        events_v1 = watcher.detect_events("demochan", msgs_v1, bootstrap_if_empty=False)
        self.assertEqual([e.kind for e in events_v1], ["new", "new"])

        msgs_v2 = watcher.parse_messages("demochan", html_v2)
        events_v2 = watcher.detect_events("demochan", msgs_v2)
        self.assertEqual(len(events_v2), 1)
        self.assertEqual(events_v2[0].kind, "edited")
        self.assertIn("68000", events_v2[0].previous_text)
        self.assertIn("68100", events_v2[0].message.text)


if __name__ == "__main__":
    unittest.main()
