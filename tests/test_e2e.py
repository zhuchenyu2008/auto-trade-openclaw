from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tradeclaw.app import TradeClawApp
from tradeclaw.models import TradeDecision


FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


class FakeAgent:
    def decide(self, event, snapshot):
        return TradeDecision(
            kind="trade",
            intent="open_long",
            symbol="BTC-USDT-SWAP",
            order_type="market",
            size_mode="contracts",
            size_value=1,
            stop_loss_trigger_price=68100,
            take_profit_trigger_price=72500,
            reason="fixture signal",
            confidence=0.9,
            raw={"intent": "open_long"},
        )


class FakeOkx:
    def get_account_snapshot(self):
        return {"positions": [], "open_orders": [], "algo_orders": [], "okx_config": {"ok": True}}

    def execute(self, decision):
        from tradeclaw.models import CommandResult, ExecutionResult

        return ExecutionResult(
            mode="dry-run",
            summary="planned 1 command",
            commands=[CommandResult(command=["okx", "swap", "place"], returncode=0, stdout="DRY_RUN", stderr="", ok=True)],
            pre_snapshot={"positions": []},
        )


class FakeReporter:
    def __init__(self):
        self.bodies = []

    def build_report(self, event, decision, execution):
        from tradeclaw.models import ReportBundle

        return ReportBundle(title="x", body=f"{event.channel}:{event.message.post_id}:{decision.intent}:{execution.mode}")

    def send(self, report):
        self.bodies.append(report.body)


class TradeClawE2ETests(unittest.TestCase):
    def test_once_cycle_processes_fixture_event(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = {
                "source": {"channels": ["demochan"], "poll_interval_seconds": 1, "max_recent_messages": 5},
                "report": {"enabled": False, "target": "chat_or_group_id"},
                "runtime": {"dry_run": True, "execution_enabled": False, "state_path": "./state/state.json", "okx_audit_path": "./state/okx_audit.jsonl"},
            }
            cfg_path = Path(td) / "config.json"
            cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
            app = TradeClawApp(cfg_path)
            app.agent = FakeAgent()
            app.okx = FakeOkx()
            reporter = FakeReporter()
            app.reporter = reporter
            app.watcher.fetch = lambda channel: (FIXTURES / "channel_sample_v1.html").read_text(encoding="utf-8")
            app.state["channels"] = {
                "demochan": {
                    "seen": {
                        "999": {"fingerprint": "old", "text": "old", "dt": None, "permalink": "x"}
                    }
                }
            }

            processed = app.run_once()
            self.assertEqual(len(processed), 2)
            self.assertEqual(len(reporter.bodies), 2)
            self.assertTrue(reporter.bodies[0].startswith("demochan:1001:open_long:dry-run"))


if __name__ == "__main__":
    unittest.main()
