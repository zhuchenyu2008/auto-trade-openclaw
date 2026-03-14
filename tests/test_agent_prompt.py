from __future__ import annotations

import unittest

from tradeclaw.agent_client import OpenClawAgentClient
from tradeclaw.config import AppConfig
from tradeclaw.models import ChannelEvent, PublicMessage


class AgentPromptTests(unittest.TestCase):
    def test_prompt_includes_event_driven_boundary(self):
        config = AppConfig()
        client = OpenClawAgentClient(config)
        event = ChannelEvent(
            kind="new",
            channel="demochan",
            message=PublicMessage(
                post_id=1,
                permalink="https://t.me/demochan/1",
                dt="2026-03-14T10:00:00+00:00",
                text="BTC long now",
                fingerprint="abc",
            ),
        )
        prompt = client._build_prompt(event, {"positions": []})
        self.assertIn("event-driven", prompt)
        self.assertIn("ONLY decide based on the incoming Telegram channel event", prompt)
        self.assertIn("global_leverage_is_fixed_externally", prompt)


if __name__ == "__main__":
    unittest.main()
