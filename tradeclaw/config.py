from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class OpenClawConfig:
    session_prefix: str = "tradeclaw"
    thinking: str = "high"
    timeout_seconds: int = 120


@dataclass
class SourceConfig:
    mode: str = "public_html"  # currently only public_html
    channels: list[str] = field(default_factory=list)
    poll_interval_seconds: int = 5
    max_recent_messages: int = 8


@dataclass
class OkxConfig:
    profile: str = "demo"
    site: str = "global"
    instrument_scope: str = "swap"
    margin_mode: str = "cross"
    position_mode: str = "long_short_mode"
    default_leverage: str = "10"
    min_position_pct: float = 0.4
    max_position_pct: float = 0.8
    default_position_pct: float = 0.6
    allowed_instruments: list[str] = field(default_factory=list)


@dataclass
class ReportConfig:
    channel: str = "telegram"
    target: str = "chat_or_group_id"
    thread_id: str | None = None
    silent: bool = True
    enabled: bool = False


@dataclass
class RuntimeConfig:
    execution_enabled: bool = False
    dry_run: bool = True
    state_path: str = "./state/state.json"
    okx_audit_path: str = "./state/okx_audit.jsonl"


@dataclass
class AppConfig:
    source: SourceConfig = field(default_factory=SourceConfig)
    okx: OkxConfig = field(default_factory=OkxConfig)
    report: ReportConfig = field(default_factory=ReportConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    openclaw: OpenClawConfig = field(default_factory=OpenClawConfig)

    @staticmethod
    def _merge_dataclass(cls, data: dict[str, Any]):
        instance = cls()
        for key, value in data.items():
            if hasattr(instance, key):
                setattr(instance, key, value)
        return instance

    @classmethod
    def load(cls, path: str | Path) -> "AppConfig":
        p = Path(path)
        data = json.loads(p.read_text(encoding="utf-8"))
        return cls(
            source=cls._merge_dataclass(SourceConfig, data.get("source", {})),
            okx=cls._merge_dataclass(OkxConfig, data.get("okx", {})),
            report=cls._merge_dataclass(ReportConfig, data.get("report", {})),
            runtime=cls._merge_dataclass(RuntimeConfig, data.get("runtime", {})),
            openclaw=cls._merge_dataclass(OpenClawConfig, data.get("openclaw", {})),
        )

    def resolve_paths(self, base_dir: str | Path) -> None:
        base = Path(base_dir)
        self.runtime.state_path = str((base / self.runtime.state_path).resolve())
        self.runtime.okx_audit_path = str((base / self.runtime.okx_audit_path).resolve())
