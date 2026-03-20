from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = Path("config.json")
EXAMPLE_CONFIG_PATH = PROJECT_ROOT / "config.example.json"
SENSITIVE_KEYS = {"pin_hash", "bot_token", "api_key", "api_secret", "passphrase", "session_id"}
_ENV_LOCK = threading.RLock()
_MANAGED_ENV_VALUES: dict[str, str] = {}


@dataclass
class ChannelConfig:
    id: str
    name: str
    source_type: str = "public_web"
    chat_id: str = ""
    channel_username: str = ""
    enabled: bool = True
    priority: int = 100
    parse_profile_id: str = "default"
    strategy_profile_id: str = "default"
    risk_profile_id: str = "default"
    paper_trading_enabled: bool = True
    live_trading_enabled: bool = False
    listen_new_messages: bool = True
    listen_edits: bool = True
    listen_deletes: bool = False
    reconcile_interval_seconds: int = 30
    dedup_window_seconds: int = 3600
    notes: str = ""


@dataclass
class WebConfig:
    host: str = "127.0.0.1"
    port: int = 6010
    pin_hash: str = ""
    pin_plaintext_env: str = "TG_OKX_WEB_PIN"


@dataclass
class RuntimeConfig:
    data_dir: str = "data"
    sqlite_path: str = "data/app.db"
    log_retention_days: int = 14
    config_reload_seconds: int = 5


@dataclass
class TradingConfig:
    mode: str = "demo"
    execution_mode: str = "automatic"
    default_leverage: int = 20
    margin_mode: str = "isolated"
    position_mode: str = "net"
    paper_trading_enabled: bool = True
    live_trading_enabled: bool = False
    global_tp_sl_enabled: bool = False
    global_take_profit_ratio: float = 50.0
    global_stop_loss_ratio: float = 20.0
    allow_live_switch: bool = False
    readonly_close_only: bool = False
    paused: bool = False


@dataclass
class AIConfig:
    provider: str = "openclaw"
    model: str = "default"
    openclaw_agent_id: str = "main"
    thinking: str = "high"
    timeout_seconds: int = 90
    system_prompt: str = (
        "Extract trading intent from Telegram signals. Return strict JSON only. "
        "Do not invent fields."
    )


@dataclass
class TelegramConfig:
    bot_token: str = ""
    bot_token_env: str = "TG_OKX_TELEGRAM_BOT_TOKEN"
    poll_interval_seconds: int = 5
    channels: list[ChannelConfig] = field(default_factory=list)
    report_topic: str = ""
    operator_target: str = ""
    operator_thread_id: int = 0


@dataclass
class OKXConfig:
    enabled: bool = False
    api_key: str = ""
    api_secret: str = ""
    passphrase: str = ""
    api_key_env: str = "TG_OKX_OKX_API_KEY"
    api_secret_env: str = "TG_OKX_OKX_API_SECRET"
    passphrase_env: str = "TG_OKX_OKX_PASSPHRASE"
    use_demo: bool = True
    rest_base: str = "https://www.okx.com"
    ws_private_url: str = "wss://ws.okx.com:8443/ws/v5/private"


@dataclass
class AppConfig:
    web: WebConfig = field(default_factory=WebConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    okx: OKXConfig = field(default_factory=OKXConfig)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _channel_from_dict(payload: dict[str, Any]) -> ChannelConfig:
    normalized = dict(payload)
    normalized["chat_id"] = normalize_chat_id(normalized.get("chat_id", ""))
    normalized["channel_username"] = normalize_channel_username(normalized.get("channel_username", ""))
    return ChannelConfig(**normalized)


def _config_from_dict(payload: dict[str, Any]) -> AppConfig:
    telegram_payload = dict(payload.get("telegram", {}))
    telegram_payload["report_topic"] = normalize_topic_target(telegram_payload.get("report_topic", ""))
    telegram_payload["operator_target"] = normalize_topic_target(telegram_payload.get("operator_target", ""))
    active_topic_target = telegram_payload["operator_target"] or telegram_payload["report_topic"]
    _, resolved_thread_id = topic_target_parts(
        active_topic_target,
        int(telegram_payload.get("operator_thread_id", 0) or 0),
    )
    telegram_payload["operator_thread_id"] = resolved_thread_id
    telegram_payload["channels"] = [
        _channel_from_dict(item) for item in telegram_payload.get("channels", [])
    ]
    return AppConfig(
        web=WebConfig(**payload.get("web", {})),
        runtime=RuntimeConfig(**payload.get("runtime", {})),
        trading=TradingConfig(**payload.get("trading", {})),
        ai=AIConfig(**payload.get("ai", {})),
        telegram=TelegramConfig(**telegram_payload),
        okx=OKXConfig(**payload.get("okx", {})),
    )


def ensure_config_file(path: Path) -> None:
    if path.exists():
        return
    if EXAMPLE_CONFIG_PATH.exists():
        path.write_text(EXAMPLE_CONFIG_PATH.read_text(), encoding="utf-8")
        return
    raise FileNotFoundError(
        f"Missing config file at {path}. Create it from {EXAMPLE_CONFIG_PATH}."
    )


def local_env_path(base_dir: Path | None = None) -> Path:
    return env_search_paths(base_dir)[0]


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        values[key] = value.strip().strip("'\"")
    return values


def write_env_file(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{key}={value}" for key, value in sorted(values.items())]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def load_config(path: Path | None = None) -> AppConfig:
    config_path = path or DEFAULT_CONFIG_PATH
    ensure_config_file(config_path)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    config = _config_from_dict(payload)
    _resolve_runtime_paths(config, config_path.parent.resolve())
    validate_config(config)
    return config


def save_config(config: AppConfig, path: Path | None = None) -> None:
    config_path = path or DEFAULT_CONFIG_PATH
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(config.to_dict(), indent=2, sort_keys=True), encoding="utf-8"
    )


def validate_config(config: AppConfig) -> None:
    okx_key, okx_secret, okx_passphrase = resolve_okx_credentials(config)
    if config.web.port <= 0:
        raise ValueError("web.port must be a positive integer")
    if config.web.pin_hash and not _is_sha256(config.web.pin_hash):
        raise ValueError("web.pin_hash must be a 64-character SHA256 hex digest")
    if not config.runtime.data_dir:
        raise ValueError("runtime.data_dir must not be empty")
    if not config.runtime.sqlite_path:
        raise ValueError("runtime.sqlite_path must not be empty")
    if config.runtime.config_reload_seconds <= 0:
        raise ValueError("runtime.config_reload_seconds must be a positive integer")
    if not (1 <= int(config.trading.default_leverage) <= 125):
        raise ValueError("trading.default_leverage must be between 1 and 125")
    if config.trading.margin_mode not in {"isolated", "cross"}:
        raise ValueError("trading.margin_mode must be isolated or cross")
    if config.trading.position_mode != "net":
        raise ValueError("trading.position_mode must be net in this demo-only build")
    if config.trading.mode not in {"observe", "demo"}:
        raise ValueError("trading.mode must be observe or demo in this demo-only build")
    if config.trading.execution_mode not in {"automatic", "observe"}:
        raise ValueError(
            "trading.execution_mode must be automatic or observe in this demo-only build"
        )
    if config.trading.live_trading_enabled:
        raise ValueError("trading.live_trading_enabled must remain false in this demo-only build")
    if config.trading.allow_live_switch:
        raise ValueError("trading.allow_live_switch must remain false in this demo-only build")
    if config.trading.global_take_profit_ratio < 0:
        raise ValueError("trading.global_take_profit_ratio must be non-negative")
    if config.trading.global_stop_loss_ratio < 0:
        raise ValueError("trading.global_stop_loss_ratio must be non-negative")
    if config.ai.thinking not in {"low", "medium", "high", "custom", "minimal", "off"}:
        raise ValueError("ai.thinking is invalid")
    if config.telegram.poll_interval_seconds <= 0:
        raise ValueError("telegram.poll_interval_seconds must be a positive integer")
    if config.telegram.operator_thread_id < 0:
        raise ValueError("telegram.operator_thread_id must be zero or a positive integer")
    if config.okx.enabled and not config.okx.use_demo:
        raise ValueError("okx.use_demo must remain true when OKX execution is enabled")
    if config.okx.enabled and not all((okx_key, okx_secret, okx_passphrase)):
        raise ValueError(
            "okx.enabled requires demo credentials via config or env: "
            f"{config.okx.api_key_env}, {config.okx.api_secret_env}, {config.okx.passphrase_env}"
        )
    seen_channel_ids: set[str] = set()
    for channel in config.telegram.channels:
        if not channel.id.strip():
            raise ValueError("telegram.channels[].id must not be empty")
        if channel.id in seen_channel_ids:
            raise ValueError(f"Duplicate telegram channel id: {channel.id}")
        seen_channel_ids.add(channel.id)
        if not channel.name.strip():
            raise ValueError(f"Channel {channel.id} must include a display name")
        if channel.source_type not in {"bot_api", "mtproto", "public_web"}:
            raise ValueError(f"Channel {channel.id} has invalid source_type")
        if channel.live_trading_enabled:
            raise ValueError(f"Channel {channel.id} cannot enable live trading in this demo-only build")
        if channel.source_type == "public_web" and not channel.channel_username:
            raise ValueError(
                f"Channel {channel.id} must define channel_username when source_type=public_web"
            )
        if not channel.chat_id and not channel.channel_username:
            raise ValueError(f"Channel {channel.id} must define chat_id or channel_username")
        if channel.reconcile_interval_seconds <= 0:
            raise ValueError(f"Channel {channel.id} must have reconcile_interval_seconds > 0")
        if channel.dedup_window_seconds <= 0:
            raise ValueError(f"Channel {channel.id} must have dedup_window_seconds > 0")


def merge_config_patch(config: AppConfig, patch: dict[str, Any]) -> AppConfig:
    merged = _deep_merge_dicts(config.to_dict(), patch)
    rebound = _config_from_dict(merged)
    validate_config(rebound)
    return rebound


def replace_config(target: AppConfig, source: AppConfig) -> None:
    target.web = source.web
    target.runtime = source.runtime
    target.trading = source.trading
    target.ai = source.ai
    target.telegram = source.telegram
    target.okx = source.okx


def _deep_merge_dicts(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge_dicts(result[key], value)
        else:
            result[key] = value
    return result


def hash_pin(pin: str) -> str:
    return hashlib.sha256(pin.encode("utf-8")).hexdigest()


def resolve_pin_hash(config: AppConfig) -> str:
    if config.web.pin_hash:
        return config.web.pin_hash
    env_name = config.web.pin_plaintext_env
    pin = _resolved_env_value(env_name)
    if not pin:
        raise ValueError(
            f"Missing web PIN. Set web.pin_hash in config.json or export {env_name}."
        )
    if len(pin) != 6 or not pin.isdigit():
        raise ValueError("Web PIN must be exactly 6 digits")
    return hash_pin(pin)


def resolve_topic_target(config: AppConfig) -> str:
    operator_target = normalize_topic_target(config.telegram.operator_target or "")
    if operator_target:
        return operator_target
    return normalize_topic_target(config.telegram.report_topic or "")


def resolve_telegram_bot_token(config: AppConfig) -> str:
    if config.telegram.bot_token:
        return config.telegram.bot_token
    return _resolved_env_value(config.telegram.bot_token_env)


def resolve_okx_credentials(config: AppConfig) -> tuple[str, str, str]:
    return (
        config.okx.api_key or _resolved_env_value(config.okx.api_key_env),
        config.okx.api_secret or _resolved_env_value(config.okx.api_secret_env),
        config.okx.passphrase or _resolved_env_value(config.okx.passphrase_env),
    )


def secret_sources(config: AppConfig) -> dict[str, str]:
    okx_key, okx_secret, okx_passphrase = resolve_okx_credentials(config)
    okx_complete = bool(okx_key and okx_secret and okx_passphrase)
    return {
        "web_pin": "config" if config.web.pin_hash else ("env" if _resolved_env_value(config.web.pin_plaintext_env) else "missing"),
        "telegram_bot_token": "config" if config.telegram.bot_token else ("env" if _resolved_env_value(config.telegram.bot_token_env) else "missing"),
        "okx_demo_credentials": (
            "config"
            if (config.okx.api_key and config.okx.api_secret and config.okx.passphrase)
            else ("env" if okx_complete else "missing")
        ),
    }


def normalize_topic_target(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    match = re.match(r"^(?:https?://)?t\.me/c/(\d+)/(\d+)(?:\?.*)?$", raw, flags=re.IGNORECASE)
    if match:
        return f"-100{match.group(1)}:topic:{match.group(2)}"
    return raw


def normalize_chat_id(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    match = re.match(r"^(?:https?://)?t\.me/c/(\d+)(?:/\d+)?(?:\?.*)?$", raw, flags=re.IGNORECASE)
    if match:
        return f"-100{match.group(1)}"
    return raw


def normalize_channel_username(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    match = re.match(
        r"^(?:https?://)?t\.me/(?:s/)?([A-Za-z0-9_]{3,})(?:/\d+)?/?(?:\?.*)?$",
        raw,
        flags=re.IGNORECASE,
    )
    if match:
        raw = match.group(1)
    return raw.lstrip("@")


def topic_target_to_link(value: str) -> str:
    raw = normalize_topic_target(value)
    match = re.match(r"^-100(\d+):topic:(\d+)$", raw)
    if not match:
        return ""
    return f"https://t.me/c/{match.group(1)}/{match.group(2)}"


def topic_target_parts(value: str, fallback_thread_id: int = 0) -> tuple[str, int]:
    raw = normalize_topic_target(value)
    if not raw:
        return "", 0
    match = re.match(r"^(-100\d+):topic:(\d+)$", raw)
    if match:
        return match.group(1), int(match.group(2))
    return raw, max(int(fallback_thread_id or 0), 0)


def chat_target_to_link(chat_id: str = "", channel_username: str = "") -> str:
    normalized_chat_id = normalize_chat_id(chat_id)
    normalized_username = normalize_channel_username(channel_username)
    match = re.match(r"^-100(\d+)$", normalized_chat_id)
    if match:
        return f"https://t.me/c/{match.group(1)}"
    if normalized_username:
        return f"https://t.me/{normalized_username}"
    return ""


def public_config_dict(config: AppConfig | dict[str, Any]) -> dict[str, Any]:
    payload = config.to_dict() if isinstance(config, AppConfig) else copy.deepcopy(config)
    for path in (
        ("web", "pin_hash"),
        ("telegram", "bot_token"),
        ("okx", "api_key"),
        ("okx", "api_secret"),
        ("okx", "passphrase"),
    ):
        section = payload
        for key in path[:-1]:
            section = section.get(key, {})
        if isinstance(section, dict):
            section[path[-1]] = ""
    return payload


def redact_sensitive_data(value: Any, replacement: str = "<redacted>") -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if key in SENSITIVE_KEYS and item and not _is_placeholder_secret(item):
                redacted[key] = replacement
            else:
                redacted[key] = redact_sensitive_data(item, replacement)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive_data(item, replacement) for item in value]
    return value


def _is_placeholder_secret(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    return stripped.startswith("<") and stripped.endswith(">")


class ConfigManager:
    def __init__(self, path: Path | None = None):
        self.path = path or DEFAULT_CONFIG_PATH
        self._lock = threading.RLock()
        load_local_env(self.path.parent.resolve())
        self._config = load_config(self.path)
        self._mtime_ns = self.path.stat().st_mtime_ns
        self._env_state = env_watch_state(self.path.parent.resolve())

    def get(self) -> AppConfig:
        with self._lock:
            return copy.deepcopy(self._config)

    def update(self, mutator) -> AppConfig:
        with self._lock:
            config = copy.deepcopy(self._config)
            mutator(config)
            validate_config(config)
            save_config(config, self.path)
            self._config = config
            self._mtime_ns = self.path.stat().st_mtime_ns
            return copy.deepcopy(self._config)

    def reload_if_changed(self) -> bool:
        with self._lock:
            current_mtime_ns = self.path.stat().st_mtime_ns
            current_env_state = env_watch_state(self.path.parent.resolve())
            if current_mtime_ns <= self._mtime_ns and current_env_state == self._env_state:
                return False
            load_local_env(self.path.parent.resolve())
            self._config = load_config(self.path)
            self._mtime_ns = current_mtime_ns
            self._env_state = current_env_state
            return True

    def watch(self, callback, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            try:
                if self.reload_if_changed():
                    callback(self.get())
            finally:
                time.sleep(self._config.runtime.config_reload_seconds)


def load_local_env(base_dir: Path | None = None) -> dict[str, str]:
    env_values: dict[str, str] = {}
    for env_path in env_search_paths(base_dir):
        for key, value in read_env_file(env_path).items():
            env_values.setdefault(key, value)
    with _ENV_LOCK:
        for key in list(_MANAGED_ENV_VALUES):
            if key in env_values:
                continue
            if os.environ.get(key) == _MANAGED_ENV_VALUES[key]:
                os.environ.pop(key, None)
            _MANAGED_ENV_VALUES.pop(key, None)
        for key, value in env_values.items():
            current = os.environ.get(key)
            managed_value = _MANAGED_ENV_VALUES.get(key)
            if key not in os.environ or not current or current == managed_value:
                os.environ[key] = value
                _MANAGED_ENV_VALUES[key] = value
            elif managed_value is not None and current != managed_value:
                _MANAGED_ENV_VALUES.pop(key, None)
    return env_values


def env_search_paths(base_dir: Path | None = None) -> list[Path]:
    search_paths: list[Path] = []
    if base_dir is not None:
        search_paths.append((base_dir / ".env").resolve())
    project_env = (PROJECT_ROOT / ".env").resolve()
    if project_env not in search_paths:
        search_paths.append(project_env)
    return search_paths


def env_watch_state(base_dir: Path | None = None) -> dict[str, int]:
    state: dict[str, int] = {}
    for env_path in env_search_paths(base_dir):
        state[str(env_path)] = env_path.stat().st_mtime_ns if env_path.is_file() else -1
    return state


def externalize_config_secrets(config_path: Path) -> dict[str, Any]:
    config = load_config(config_path)
    env_path = local_env_path(config_path.parent.resolve())
    env_values = read_env_file(env_path)

    exported: list[str] = []

    def store(env_name: str, value: str) -> None:
        if not env_name or not value:
            return
        env_values[env_name] = value
        exported.append(env_name)

    telegram_bot_token = resolve_telegram_bot_token(config)
    okx_key, okx_secret, okx_passphrase = resolve_okx_credentials(config)

    store(config.telegram.bot_token_env, telegram_bot_token)
    store(config.okx.api_key_env, okx_key)
    store(config.okx.api_secret_env, okx_secret)
    store(config.okx.passphrase_env, okx_passphrase)

    write_env_file(env_path, env_values)
    load_local_env(config_path.parent.resolve())

    config.telegram.bot_token = ""
    config.okx.api_key = ""
    config.okx.api_secret = ""
    config.okx.passphrase = ""
    save_config(config, config_path)

    return {
        "config_path": str(config_path.resolve()),
        "env_path": str(env_path.resolve()),
        "exported_vars": exported,
        "secret_sources": secret_sources(load_config(config_path)),
    }


def _resolve_runtime_paths(config: AppConfig, base_dir: Path) -> None:
    data_dir = Path(config.runtime.data_dir)
    sqlite_path = Path(config.runtime.sqlite_path)
    if not data_dir.is_absolute():
        data_dir = (base_dir / data_dir).resolve()
    if not sqlite_path.is_absolute():
        sqlite_path = (base_dir / sqlite_path).resolve()
    config.runtime.data_dir = str(data_dir)
    config.runtime.sqlite_path = str(sqlite_path)


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(ch in "0123456789abcdefABCDEF" for ch in value)


def _resolved_env_value(env_name: str) -> str:
    if not env_name:
        return ""
    return str(os.environ.get(env_name, "") or "").strip()
