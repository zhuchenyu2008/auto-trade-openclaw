from __future__ import annotations

import time
from pathlib import Path
from typing import Iterable

from .agent_client import OpenClawAgentClient
from .config import AppConfig
from .okx_cli import OkxCliAdapter
from .public_channel import PublicChannelWatcher
from .reporter import OpenClawReporter
from .utils import load_json, save_json


class TradeClawApp:
    def __init__(self, config_path: str | Path):
        self.config_path = Path(config_path).resolve()
        self.base_dir = self.config_path.parent
        self.config = AppConfig.load(self.config_path)
        self.config.resolve_paths(self.base_dir)
        self.state = load_json(self.config.runtime.state_path, {"channels": {}})
        self.watcher = PublicChannelWatcher(self.state)
        self.agent = OpenClawAgentClient(self.config)
        self.okx = OkxCliAdapter(self.config)
        self.reporter = OpenClawReporter(self.config)

    def run_forever(self) -> None:
        while True:
            self.run_once()
            save_json(self.config.runtime.state_path, self.state)
            time.sleep(self.config.source.poll_interval_seconds)

    def run_once(self) -> list[dict]:
        processed: list[dict] = []
        for channel in self.config.source.channels:
            html = self.watcher.fetch(channel)
            messages = self.watcher.parse_messages(channel, html)
            events = self.watcher.detect_events(channel, messages, self.config.source.max_recent_messages)
            for event in events:
                snapshot = self.okx.get_account_snapshot()
                decision = self.agent.decide(event, snapshot)
                execution = self.okx.execute(decision)
                report = self.reporter.build_report(event, decision, execution)
                self.reporter.send(report)
                processed.append(
                    {
                        "channel": channel,
                        "post_id": event.message.post_id,
                        "kind": event.kind,
                        "decision": decision.raw,
                        "execution_mode": execution.mode,
                        "summary": execution.summary,
                    }
                )
        save_json(self.config.runtime.state_path, self.state)
        return processed
