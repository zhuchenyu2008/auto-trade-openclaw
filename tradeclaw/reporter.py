from __future__ import annotations

import os
import shutil
import subprocess
from textwrap import dedent

from .config import AppConfig
from .models import ChannelEvent, ExecutionResult, ReportBundle, TradeDecision
from .utils import tail_text


class OpenClawReporter:
    def __init__(self, config: AppConfig):
        self.config = config
        self.openclaw_bin = os.environ.get("OPENCLAW_BIN") or shutil.which("openclaw") or "/www/server/nodejs/v24.13.0/bin/openclaw"

    def build_report(self, event: ChannelEvent, decision: TradeDecision, execution: ExecutionResult) -> ReportBundle:
        before = ""
        if event.kind == "edited" and event.previous_text:
            before = f"\n- Before: {tail_text(event.previous_text, 260)}"
        commands_block = "\n".join(
            f"  {i+1}. {' '.join(result.command)}"
            for i, result in enumerate(execution.commands)
        ) or "  (none)"
        body = dedent(
            f"""
            [TradeClaw] {event.channel} #{event.message.post_id} {event.kind}
            - Text: {tail_text(event.message.text, 700)}{before}
            - Decision: {decision.intent}
            - Symbol: {decision.symbol or '-'}
            - Order: {decision.order_type}
            - Size: {decision.size_mode} / {decision.size_value}
            - TP: {decision.take_profit_trigger_price}
            - SL: {decision.stop_loss_trigger_price}
            - Trail: {decision.trailing_callback_ratio}
            - Confidence: {decision.confidence}
            - Reason: {decision.reason or '-'}
            - Execution: {execution.mode} / {execution.summary}
            - Link: {event.message.permalink}
            - Commands:
            {commands_block}
            """
        ).strip()
        if execution.errors:
            body += "\n- Errors:\n  - " + "\n  - ".join(tail_text(e, 500) for e in execution.errors)
        return ReportBundle(title=f"TradeClaw {event.channel} #{event.message.post_id}", body=body)

    def send(self, report: ReportBundle) -> None:
        if not self.config.report.enabled:
            return
        cmd = [
            self.openclaw_bin,
            "message",
            "send",
            "--channel",
            self.config.report.channel,
            "--target",
            self.config.report.target,
            "--message",
            report.body,
        ]
        if self.config.report.thread_id:
            cmd.extend(["--thread-id", self.config.report.thread_id])
        if self.config.report.silent:
            cmd.append("--silent")
        subprocess.run(cmd, check=True)
