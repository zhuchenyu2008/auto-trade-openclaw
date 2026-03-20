#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.demo.local.json")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    src_path = str(root / "src")
    if src_path not in sys.path:
        sys.path.insert(0, src_path)

    from tg_okx_auto_trade.runtime import Runtime

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = (root / config_path).resolve()

    runtime = Runtime(config_path)
    try:
        public_state = runtime.public_snapshot()
        direct_use = runtime.direct_use_payload(snapshot=public_state)
        payload = _build_payload(root, config_path, public_state, direct_use)
    finally:
        runtime.stop()

    if args.format == "markdown":
        print(_render_markdown(payload))
    else:
        print(json.dumps(payload, indent=2))
    return 0


def _build_payload(
    root: Path,
    config_path: Path,
    public_state: dict[str, object],
    direct_use: dict[str, object],
) -> dict[str, object]:
    run_paths = _as_dict(direct_use.get("run_paths"))
    wiring = _as_dict(direct_use.get("wiring"))
    activation_summary = _as_dict(direct_use.get("activation_summary"))
    secret_status = _as_dict(direct_use.get("secret_status"))
    config = _as_dict(public_state.get("config"))
    telegram = _as_dict(config.get("telegram"))
    ai = _as_dict(config.get("ai"))
    enabled_public_web_channels = [
        {
            "id": str(channel.get("id", "")),
            "name": str(channel.get("name", "")),
            "channel_username": str(channel.get("channel_username", "")),
        }
        for channel in _as_list(telegram.get("channels"))
        if isinstance(channel, dict) and channel.get("enabled") and channel.get("source_type") == "public_web"
    ]
    runtime_dir = Path(str(run_paths.get("runtime_state_dir", "") or "")).resolve()
    direct_use_json = str(run_paths.get("runtime_direct_use_json", "") or "")
    public_state_json = str(run_paths.get("runtime_public_state_json", "") or "")
    verify_capture = runtime_dir / "m3-verify-before.json"
    direct_use_capture = runtime_dir / "m3-direct-use-before.json"
    prep_capture = runtime_dir / "m3-acceptance-prep.md"
    snapshot_after_ingest = runtime_dir / "m3-snapshot-after-public-web.json"
    snapshot_after_close = runtime_dir / "m3-snapshot-after-close.json"
    close_command_capture = runtime_dir / "m3-close-command.json"
    topic_test_capture = runtime_dir / "m3-topic-test.json"
    smoke_okx_capture = runtime_dir / "m3-smoke-okx-demo.json"
    direct_use_command = str(run_paths.get("direct_use_command", "") or "")
    snapshot_command = str(run_paths.get("snapshot_command", "") or "")

    repo_local_baseline = [
        str(run_paths.get("smoke_suite_command", "") or ""),
        f"python3 {root / 'scripts' / 'run_fixture_suite.py'} --fixtures {root / 'tests' / 'fixtures' / 'public_web' / 'messages'}",
        f"python3 {root / 'scripts' / 'run_fixture_suite.py'} --fixtures {root / 'tests' / 'fixtures' / 'public_web' / 'scenarios'}",
        f"python3 {root / 'scripts' / 'run_fixture_suite.py'} --fixtures {root / 'tests' / 'fixtures' / 'public_web' / 'html'}",
    ]
    manual_steps = [
        {
            "id": "prep_report",
            "purpose": "Save the repo-side M3 prep summary before any credentialed run.",
            "command": f"python3 {root / 'scripts' / 'm3_acceptance_prep.py'} --config {config_path} --format markdown > {prep_capture}",
        },
        {
            "id": "optional_clean_runtime",
            "purpose": "Start M3 from a clean local runtime after archiving prior artifacts if you want easier evidence review.",
            "command": str(run_paths.get("reset_local_state_command", "") or ""),
        },
        {
            "id": "capture_preflight_verify",
            "purpose": "Capture the redacted readiness state that proves demo-only guardrails and current wiring before the credentialed run.",
            "command": f"{str(run_paths.get('verify_command', '') or '')} > {verify_capture}",
        },
        {
            "id": "capture_preflight_direct_use",
            "purpose": "Capture the short structured run-path summary before the credentialed run.",
            "command": f"{direct_use_command} --json > {direct_use_capture}",
        },
        {
            "id": "start_service",
            "purpose": "Start the runtime that the operator will use for the human-controlled M3 acceptance run.",
            "command": str(run_paths.get("serve_command", "") or ""),
        },
        {
            "id": "topic_smoke",
            "purpose": "Send one real outbound topic smoke and save the CLI result plus Telegram-side evidence.",
            "command": f"{str(run_paths.get('topic_test_command', '') or '')} > {topic_test_capture}",
        },
        {
            "id": "public_web_ingest",
            "purpose": "Allow one real configured public_web post to ingest through the running service, then save a snapshot.",
            "command": f"{snapshot_command} > {snapshot_after_ingest}",
        },
        {
            "id": "close_credentialed_position",
            "purpose": "After a credentialed demo open or reverse, close the position on the configured demo path and save a second snapshot.",
            "command": (
                f"python3 -m tg_okx_auto_trade.main close-positions --config {config_path} "
                f"--symbol <symbol-from-open> > {close_command_capture}"
            ),
        },
        {
            "id": "capture_post_close_snapshot",
            "purpose": "Capture the runtime state immediately after the configured-path reverse or close step.",
            "command": f"{snapshot_command} > {snapshot_after_close}",
        },
        {
            "id": "supplemental_okx_smoke",
            "purpose": "Optional supplemental evidence for the OKX demo REST path only; this is not a substitute for real public_web ingest evidence.",
            "command": f"{str(run_paths.get('smoke_okx_demo_command', '') or '')} > {smoke_okx_capture}",
        },
    ]
    evidence_checks = [
        "Save the prep output, preflight verify JSON, and preflight direct-use JSON.",
        "Save the repo-local baseline outputs from run_demo_suite.py and all three run_fixture_suite.py commands.",
        "For the real ingest snapshot, confirm the newest message shows `payload.adapter` = `public_web`.",
        "For the real ingest snapshot, confirm the newest AI decision shows `payload.raw.parser_source` = `openclaw`, not `heuristic_fallback`.",
        "For the real ingest or follow-up close snapshot, confirm the newest order carries a non-empty `exchange_order_id`.",
        "Capture the actual operator-topic smoke evidence from Telegram, not only the local CLI stdout.",
        "Capture the OKX demo order ids used for the open and reverse/close step from app output or the OKX demo UI.",
    ]
    truth_constraints = [
        "This prep script does not validate M3 and does not perform any outbound Telegram or OKX action.",
        "Do not count `inject-message` as evidence of `public_web` ingestion.",
        "Do not claim configured-path support for `update_protection`, trailing protection, or ratio-based global TP/SL on OKX demo.",
        "Do not treat inbound operator-topic bot commands as part of the intended supported acceptance scope; the supported operator path is public_web ingestion plus outbound topic logs and Web/local controls.",
    ]
    ambiguity_flags = []
    if not enabled_public_web_channels:
        ambiguity_flags.append("No enabled public_web channels are configured.")
    if secret_status.get("okx_demo_credentials_configured") is not True:
        ambiguity_flags.append("OKX demo credentials are not currently configured in env or local config.")
    if activation_summary.get("operator_topic_outbound", {}).get("status") != "ready":
        ambiguity_flags.append("Operator topic outbound path is not currently reported as ready.")
    if activation_summary.get("configured_okx_demo", {}).get("status") != "ready":
        ambiguity_flags.append("Configured OKX demo path is not currently reported as ready.")

    return {
        "scope": "repo_side_m3a_prep_only",
        "m3_validated_by_this_script": False,
        "config_path": str(config_path),
        "repo_root": str(root),
        "status": str(direct_use.get("status", "unknown")),
        "verification_status": str(public_state.get("verification_status", "unknown")),
        "web_login": str(run_paths.get("web_login", "") or ""),
        "runtime_artifacts": {
            "runtime_state_dir": str(run_paths.get("runtime_state_dir", "") or ""),
            "runtime_direct_use_json": direct_use_json,
            "runtime_direct_use_text": str(run_paths.get("runtime_direct_use_text", "") or ""),
            "runtime_public_state_json": public_state_json,
        },
        "readiness_summary": {
            "demo_only_guard": _activation_item(activation_summary, "demo_only_guard"),
            "manual_demo": _activation_item(activation_summary, "manual_demo"),
            "automatic_telegram": _activation_item(activation_summary, "automatic_telegram"),
            "configured_okx_demo": _activation_item(activation_summary, "configured_okx_demo"),
            "operator_topic_outbound": _activation_item(activation_summary, "operator_topic_outbound"),
            "operator_topic_inbound": _activation_item(activation_summary, "operator_topic_inbound"),
        },
        "secret_status": {
            "web_pin_source": str(run_paths.get("web_pin_source", "") or ""),
            "telegram_bot_token_source": str(_as_dict(run_paths.get("secret_sources")).get("telegram_bot_token", "")),
            "okx_demo_credentials_source": str(_as_dict(run_paths.get("secret_sources")).get("okx_demo_credentials", "")),
            "telegram_bot_token_configured": secret_status.get("telegram_bot_token_configured") is True,
            "okx_demo_credentials_configured": secret_status.get("okx_demo_credentials_configured") is True,
        },
        "wired_targets": {
            "topic_target": str(wiring.get("topic_target", "") or ""),
            "topic_target_link": str(run_paths.get("topic_target_link", "") or ""),
            "topic_delivery_verified": bool(wiring.get("topic_delivery_verified")),
            "operator_command_ingress": str(wiring.get("operator_command_ingress", "") or ""),
            "okx_execution_path": str(wiring.get("okx_execution_path", "") or ""),
            "configured_okx_supported_actions": _as_list(wiring.get("configured_okx_supported_actions")),
            "configured_okx_unsupported_actions": _as_list(wiring.get("configured_okx_unsupported_actions")),
        },
        "enabled_public_web_channels": enabled_public_web_channels,
        "ai_path": {
            "provider": str(ai.get("provider", "") or ""),
            "openclaw_agent_id": str(ai.get("openclaw_agent_id", "") or ""),
        },
        "repo_local_baseline_commands": [item for item in repo_local_baseline if item],
        "manual_m3_steps": manual_steps,
        "evidence_checks": evidence_checks,
        "truth_constraints": truth_constraints,
        "remaining_gaps": _as_list(direct_use.get("remaining_gaps")),
        "ambiguity_flags": ambiguity_flags,
    }


def _activation_item(summary: dict[str, object], name: str) -> dict[str, str]:
    item = summary.get(name)
    if not isinstance(item, dict):
        return {"status": "unknown", "detail": "", "action": ""}
    return {
        "status": str(item.get("status", "unknown")),
        "detail": str(item.get("detail", "")),
        "action": str(item.get("action", "")),
    }


def _as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _render_markdown(payload: dict[str, object]) -> str:
    readiness = _as_dict(payload.get("readiness_summary"))
    wired = _as_dict(payload.get("wired_targets"))
    secret_status = _as_dict(payload.get("secret_status"))
    lines = [
        "# M3 Acceptance Prep",
        "",
        f"- scope: `{payload.get('scope', '')}`",
        f"- m3_validated_by_this_script: `{payload.get('m3_validated_by_this_script', False)}`",
        f"- config_path: `{payload.get('config_path', '')}`",
        f"- web_login: `{payload.get('web_login', '')}`",
        "",
        "## Current readiness summary",
        "",
    ]
    for name in (
        "demo_only_guard",
        "manual_demo",
        "automatic_telegram",
        "configured_okx_demo",
        "operator_topic_outbound",
        "operator_topic_inbound",
    ):
        item = _as_dict(readiness.get(name))
        lines.append(
            f"- `{name}`: `{item.get('status', 'unknown')}`"
            + (f" | {item.get('detail', '')}" if item.get("detail") else "")
        )
    lines.extend(
        [
            "",
            "## Wiring",
            "",
            f"- topic_target: `{wired.get('topic_target', '')}`",
            f"- topic_target_link: `{wired.get('topic_target_link', '')}`",
            f"- operator_command_ingress: `{wired.get('operator_command_ingress', '')}`",
            f"- okx_execution_path: `{wired.get('okx_execution_path', '')}`",
            f"- topic_delivery_verified: `{wired.get('topic_delivery_verified', False)}`",
            "",
            "## Secret status",
            "",
            f"- web_pin_source: `{secret_status.get('web_pin_source', '')}`",
            f"- telegram_bot_token_source: `{secret_status.get('telegram_bot_token_source', '')}`",
            f"- okx_demo_credentials_source: `{secret_status.get('okx_demo_credentials_source', '')}`",
            f"- telegram_bot_token_configured: `{secret_status.get('telegram_bot_token_configured', False)}`",
            f"- okx_demo_credentials_configured: `{secret_status.get('okx_demo_credentials_configured', False)}`",
            "",
            "## Manual M3 steps",
            "",
        ]
    )
    for step in _as_list(payload.get("manual_m3_steps")):
        if not isinstance(step, dict):
            continue
        lines.append(f"- `{step.get('id', '')}`: {step.get('purpose', '')}")
        lines.append(f"  command: `{step.get('command', '')}`")
    lines.extend(["", "## Evidence checks", ""])
    for item in _as_list(payload.get("evidence_checks")):
        lines.append(f"- {item}")
    lines.extend(["", "## Truth constraints", ""])
    for item in _as_list(payload.get("truth_constraints")):
        lines.append(f"- {item}")
    ambiguity_flags = _as_list(payload.get("ambiguity_flags"))
    if ambiguity_flags:
        lines.extend(["", "## Ambiguity flags", ""])
        for item in ambiguity_flags:
            lines.append(f"- {item}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
