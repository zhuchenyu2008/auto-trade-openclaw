from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PublicMessage:
    post_id: int
    permalink: str
    dt: str | None
    text: str
    fingerprint: str


@dataclass
class ChannelEvent:
    kind: str  # new | edited
    channel: str
    message: PublicMessage
    previous_text: str | None = None
    recent_messages: list[PublicMessage] = field(default_factory=list)


@dataclass
class TradeDecision:
    kind: str
    intent: str
    symbol: str | None = None
    order_type: str = "market"
    limit_price: float | None = None
    size_mode: str = "contracts"
    size_value: float | None = None
    take_profit_trigger_price: float | None = None
    stop_loss_trigger_price: float | None = None
    trailing_callback_ratio: float | None = None
    cancel_existing_orders: bool = False
    reason: str = ""
    confidence: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class CommandResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    ok: bool


@dataclass
class ExecutionResult:
    mode: str  # dry-run | executed | skipped | failed
    summary: str
    commands: list[CommandResult] = field(default_factory=list)
    pre_snapshot: dict[str, Any] = field(default_factory=dict)
    post_snapshot: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


@dataclass
class ReportBundle:
    title: str
    body: str
