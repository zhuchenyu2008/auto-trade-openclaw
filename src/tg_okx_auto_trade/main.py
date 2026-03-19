from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from pathlib import Path

from .config import (
    DEFAULT_CONFIG_PATH,
    EXAMPLE_CONFIG_PATH,
    ensure_config_file,
    externalize_config_secrets,
    hash_pin,
)
from .runtime import Runtime
from .web import create_server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")

    serve = subparsers.add_parser("serve")
    serve.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))

    verify = subparsers.add_parser("verify")
    verify.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))

    paths = subparsers.add_parser("paths")
    paths.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))

    direct_use = subparsers.add_parser("direct-use")
    direct_use.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    direct_use.add_argument("--json", action="store_true")

    snapshot = subparsers.add_parser("snapshot")
    snapshot.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))

    inject = subparsers.add_parser("inject-message")
    inject.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    inject.add_argument("--text", required=True)
    inject.add_argument("--chat-id", default="-1000000000000")
    inject.add_argument("--message-id", type=int, default=1)
    inject.add_argument("--event-type", choices=["new", "edit"], default="new")
    inject.add_argument("--version", type=int)
    inject.add_argument("--real-okx-demo", action="store_true")

    pause = subparsers.add_parser("pause")
    pause.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    pause.add_argument("--reason", default="Manual pause from CLI")

    resume = subparsers.add_parser("resume")
    resume.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    resume.add_argument("--reason", default="Manual resume from CLI")

    reconcile = subparsers.add_parser("reconcile")
    reconcile.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))

    topic_test = subparsers.add_parser("topic-test")
    topic_test.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))

    operator_command = subparsers.add_parser("operator-command")
    operator_command.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    operator_command.add_argument("--text", required=True)

    set_topic_target = subparsers.add_parser("set-topic-target")
    set_topic_target.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    set_topic_target.add_argument("--target", required=True)
    set_topic_target.add_argument(
        "--field",
        choices=["operator_target", "report_topic"],
        default="operator_target",
    )

    upsert_channel = subparsers.add_parser("upsert-channel")
    upsert_channel.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    upsert_channel.add_argument("--id")
    upsert_channel.add_argument("--name", required=True)
    upsert_channel.add_argument("--source-type", default="bot_api", choices=["bot_api", "mtproto"])
    upsert_channel.add_argument("--chat-id", default="")
    upsert_channel.add_argument("--channel-username", default="")
    upsert_channel.add_argument("--enabled", action="store_true")
    upsert_channel.add_argument("--disabled", action="store_true")
    upsert_channel.add_argument("--reconcile-interval-seconds", type=int, default=30)
    upsert_channel.add_argument("--dedup-window-seconds", type=int, default=3600)
    upsert_channel.add_argument("--notes", default="")

    set_channel_enabled = subparsers.add_parser("set-channel-enabled")
    set_channel_enabled.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    set_channel_enabled.add_argument("--channel-id", required=True)
    set_channel_enabled.add_argument("--enabled", action="store_true")
    set_channel_enabled.add_argument("--disabled", action="store_true")

    remove_channel = subparsers.add_parser("remove-channel")
    remove_channel.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    remove_channel.add_argument("--channel-id", required=True)

    reset_local_state = subparsers.add_parser("reset-local-state")
    reset_local_state.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))

    close_positions = subparsers.add_parser("close-positions")
    close_positions.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    close_positions.add_argument("--symbol")

    externalize = subparsers.add_parser("externalize-secrets")
    externalize.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))

    init_config = subparsers.add_parser("init-config")
    init_config.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    init_config.add_argument("--pin")
    init_config.add_argument("--force", action="store_true")

    hash_pin_parser = subparsers.add_parser("hash-pin")
    hash_pin_parser.add_argument("--pin", required=True)

    parser.set_defaults(command="serve")
    return parser


def init_config(config_path: Path, pin: str | None, force: bool) -> int:
    if config_path.exists() and not force:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error": f"Refusing to overwrite existing config: {config_path}",
                    "hint": "Pass --force to replace it.",
                },
                indent=2,
            )
        )
        return 1
    payload = json.loads(EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8"))
    if pin is not None:
        if len(pin) != 6 or not pin.isdigit():
            raise ValueError("Web PIN must be exactly 6 digits")
        payload["web"]["pin_hash"] = hash_pin(pin)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "ok",
                "config_path": str(config_path.resolve()),
                "web_login": f"http://{payload['web']['host']}:{payload['web']['port']}/login",
                "pin_source": "config.web.pin_hash" if pin else payload["web"]["pin_plaintext_env"],
            },
            indent=2,
        )
    )
    return 0


def _explicit_enabled(enabled_flag: bool, disabled_flag: bool, *, default: bool | None = True) -> bool:
    if enabled_flag and disabled_flag:
        raise ValueError("Choose either --enabled or --disabled, not both")
    if not enabled_flag and not disabled_flag and default is None:
        raise ValueError("Pass either --enabled or --disabled")
    if not enabled_flag and not disabled_flag:
        return bool(default)
    return not disabled_flag


def main() -> int:
    parser = build_parser()
    args = parser.parse_args(["serve"] if len(sys.argv) == 1 else None)

    if args.command == "hash-pin":
        print(hash_pin(args.pin))
        return 0

    config_path = Path(args.config)
    if args.command == "init-config":
        return init_config(config_path, getattr(args, "pin", None), getattr(args, "force", False))

    ensure_config_file(config_path)
    if args.command == "externalize-secrets":
        summary = externalize_config_secrets(config_path)
        print(
            json.dumps(
                {
                    "status": "ok",
                    **summary,
                    "detail": (
                        "Moved Telegram/OKX secrets from config into the local .env file "
                        "while keeping the demo-only configuration usable."
                    ),
                },
                indent=2,
            )
        )
        return 0
    runtime = Runtime(config_path)
    if args.command == "verify":
        report = runtime.public_verification_report()
        print(json.dumps(report, indent=2))
        return 0 if report["status"] != "error" else 1
    if args.command == "paths":
        report = runtime.public_verification_report()
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "run_paths": report["run_paths"],
                    "wiring": report["wiring"],
                    "capabilities": report["capabilities"],
                    "activation_summary": report["activation_summary"],
                    "remaining_gaps": report["remaining_gaps"],
                },
                indent=2,
            )
        )
        return 0 if report["status"] != "error" else 1
    if args.command == "direct-use":
        if args.json:
            print(json.dumps(runtime.direct_use_payload(), indent=2))
        else:
            print(runtime.direct_use_text(), end="")
        return 0
    if args.command == "snapshot":
        snapshot = runtime.public_snapshot()
        snapshot["run_paths"] = runtime.usage_paths()
        print(json.dumps(snapshot, indent=2))
        return 0
    if args.command == "inject-message":
        runtime.start(background=False)
        runtime.inject_message(
            text=args.text,
            chat_id=args.chat_id,
            message_id=args.message_id,
            event_type=args.event_type,
            version=args.version,
            use_configured_okx_path=args.real_okx_demo,
        )
        print(json.dumps(runtime.public_snapshot(), indent=2))
        runtime.stop()
        return 0
    if args.command == "pause":
        runtime.pause_trading(args.reason)
        snapshot = runtime.public_snapshot()
        snapshot["run_paths"] = runtime.usage_paths()
        print(json.dumps(snapshot, indent=2))
        return 0
    if args.command == "resume":
        runtime.resume_trading(args.reason)
        snapshot = runtime.public_snapshot()
        snapshot["run_paths"] = runtime.usage_paths()
        print(json.dumps(snapshot, indent=2))
        return 0
    if args.command == "reconcile":
        print(json.dumps(runtime.reconcile_now(), indent=2))
        return 0
    if args.command == "topic-test":
        try:
            result = runtime.send_topic_test()
        except ValueError as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, indent=2))
            return 1
        print(json.dumps(result, indent=2))
        return 0 if result.get("sent") or result.get("status") == "disabled" else 1
    if args.command == "operator-command":
        result = runtime.run_operator_command(args.text, source="cli")
        print(json.dumps(result, indent=2))
        return 0 if result.get("status") != "error" else 1
    if args.command == "set-topic-target":
        try:
            updated = runtime.update_config({"telegram": {args.field: args.target}})
        except ValueError as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, indent=2))
            return 1
        print(
            json.dumps(
                {
                    "status": "ok",
                    "field": args.field,
                    "target": getattr(updated.telegram, args.field),
                    "operator_thread_id": updated.telegram.operator_thread_id,
                    "topic_target_link": runtime.usage_paths()["topic_target_link"],
                },
                indent=2,
            )
        )
        return 0
    if args.command == "upsert-channel":
        try:
            enabled = _explicit_enabled(args.enabled, args.disabled, default=True)
            channel = runtime.upsert_channel(
                {
                    "id": args.id,
                    "name": args.name,
                    "source_type": args.source_type,
                    "chat_id": args.chat_id,
                    "channel_username": args.channel_username,
                    "enabled": enabled,
                    "reconcile_interval_seconds": args.reconcile_interval_seconds,
                    "dedup_window_seconds": args.dedup_window_seconds,
                    "notes": args.notes,
                }
            )
        except ValueError as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, indent=2))
            return 1
        print(
            json.dumps(
                {
                    "status": "ok",
                    "channel": channel,
                    "enabled_channel_ids": runtime.wiring_summary()["enabled_channel_ids"],
                },
                indent=2,
            )
        )
        return 0
    if args.command == "set-channel-enabled":
        try:
            enabled = _explicit_enabled(args.enabled, args.disabled, default=None)
            channel = runtime.set_channel_enabled(args.channel_id, enabled)
        except ValueError as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, indent=2))
            return 1
        print(json.dumps({"status": "ok", "channel": channel}, indent=2))
        return 0
    if args.command == "remove-channel":
        try:
            runtime.remove_channel(args.channel_id)
        except ValueError as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, indent=2))
            return 1
        print(
            json.dumps(
                {
                    "status": "ok",
                    "channel_id": args.channel_id,
                    "enabled_channel_ids": runtime.wiring_summary()["enabled_channel_ids"],
                },
                indent=2,
            )
        )
        return 0
    if args.command == "reset-local-state":
        print(json.dumps(runtime.reset_local_runtime_state(), indent=2))
        return 0
    if args.command == "close-positions":
        try:
            result = runtime.close_positions(args.symbol)
        except (RuntimeError, ValueError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, indent=2))
            return 1
        print(json.dumps(result, indent=2))
        return 0
    runtime.start()
    server = create_server(runtime)

    def shutdown(*_) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        runtime.unregister_web_server()
        runtime.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
