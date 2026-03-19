from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .ai import OpenClawAI
from .config import AppConfig, ChannelConfig, save_config
from .models import NormalizedMessage
from .risk import RiskEngine
from .runtime import Runtime
from .telegram import parse_public_channel_html


SEED_GENERATED_AT = "2026-03-19T00:00:00+00:00"
CORPUS_VERSION = "m1b-public-web-deterministic"
SOURCE_TYPE = "public_web"
ACTION_FAMILIES = (
    "open_long",
    "open_short",
    "add_long",
    "add_short",
    "reduce_long",
    "reduce_short",
    "close_all",
    "reverse_to_long",
    "reverse_to_short",
    "cancel_orders",
    "update_protection",
)
CONTROLLED_TAGS = {
    *ACTION_FAMILIES,
    "ignore",
    "noise",
    "public_web",
    "edit",
    "replay",
    "dedup",
    "reconcile",
    "hashtag",
    "cn",
    "en",
    "mixed",
    "tp_text",
    "sl_text",
    "breakeven",
    "promo",
    "hold",
}
ACTION_SIDE = {
    "open_long": "buy",
    "open_short": "sell",
    "add_long": "buy",
    "add_short": "sell",
    "reduce_long": "sell",
    "reduce_short": "buy",
    "close_all": "flat",
    "reverse_to_long": "buy",
    "reverse_to_short": "sell",
    "cancel_orders": "flat",
    "update_protection": "flat",
    "ignore": "flat",
}
BASE_MESSAGE_DATE = datetime(2026, 3, 18, 0, 0, tzinfo=timezone.utc)
CHANNEL_FIXTURE_ORDER = (
    "cryptoninjas_trading_ann",
    "feiyangkanbi",
    "fixture_public_alpha",
)


def write_seed_fixture_corpus(base_dir: Path) -> dict[str, Any]:
    base_dir = Path(base_dir)
    messages_dir = base_dir / "messages"
    scenarios_dir = base_dir / "scenarios"
    html_dir = base_dir / "html"
    manifests_dir = base_dir / "manifests"
    for directory in (messages_dir, scenarios_dir, html_dir, manifests_dir):
        directory.mkdir(parents=True, exist_ok=True)
    _reset_fixture_dir(messages_dir, "*.json")
    _reset_fixture_dir(scenarios_dir, "*.json")
    _reset_fixture_dir(html_dir, "*.json")
    _reset_fixture_dir(html_dir, "*.html")
    _reset_fixture_dir(manifests_dir, "*.json")

    for payload in SEED_MESSAGE_FIXTURES:
        _write_json(messages_dir / f"{payload['fixture_id']}.json", payload)
    for payload in SEED_SCENARIO_FIXTURES:
        _write_json(scenarios_dir / f"{payload['fixture_id']}.json", payload)
    for payload in SEED_HTML_FIXTURES:
        fixture_id = str(payload["fixture_id"])
        (html_dir / f"{fixture_id}.html").write_text(f"{payload['html']}\n", encoding="utf-8")
        _write_json(
            html_dir / f"{fixture_id}.json",
            {
                "fixture_id": fixture_id,
                "channel_username": payload["channel_username"],
                "expected_posts": payload["expected_posts"],
            },
        )

    manifest = _build_manifest(base_dir)
    _write_json(manifests_dir / "corpus-index.json", manifest)
    return manifest


def run_fixture_suite(fixtures_dir: Path) -> dict[str, Any]:
    fixtures_dir = Path(fixtures_dir)
    if not fixtures_dir.is_dir():
        raise FileNotFoundError(f"Fixture directory not found: {fixtures_dir}")
    fixture_type = fixtures_dir.name
    if fixture_type == "messages":
        return _run_message_suite(fixtures_dir)
    if fixture_type == "scenarios":
        return _run_scenario_suite(fixtures_dir)
    if fixture_type == "html":
        return _run_html_suite(fixtures_dir)
    raise ValueError(
        f"Unsupported fixture directory `{fixtures_dir}`. Expected a messages, scenarios, or html directory."
    )


def _run_message_suite(fixtures_dir: Path) -> dict[str, Any]:
    config = _build_offline_config()
    ai = OpenClawAI(config)
    risk = RiskEngine(config)
    results = []
    failures = 0
    for path in sorted(fixtures_dir.glob("*.json")):
        payload = _read_json(path)
        errors = _validate_fixture_metadata(payload, path.stem, expected_type="message")
        errors.extend(_validate_message_fixture(payload, ai, risk))
        status = "passed" if not errors else "failed"
        failures += 0 if not errors else 1
        results.append(
            {
                "fixture_id": payload["fixture_id"],
                "status": status,
                "errors": errors,
            }
        )
    return _suite_summary("messages", fixtures_dir, results, failures)


def _run_scenario_suite(fixtures_dir: Path) -> dict[str, Any]:
    message_index = _load_message_index(fixtures_dir.parent / "messages")
    results = []
    failures = 0
    for path in sorted(fixtures_dir.glob("*.json")):
        payload = _read_json(path)
        errors = _validate_fixture_metadata(payload, path.stem, expected_type="scenario")
        errors.extend(_validate_scenario_fixture(payload, message_index))
        status = "passed" if not errors else "failed"
        failures += 0 if not errors else 1
        results.append(
            {
                "fixture_id": payload["fixture_id"],
                "status": status,
                "errors": errors,
            }
        )
    return _suite_summary("scenarios", fixtures_dir, results, failures)


def _run_html_suite(fixtures_dir: Path) -> dict[str, Any]:
    results = []
    failures = 0
    for path in sorted(fixtures_dir.glob("*.json")):
        payload = _read_json(path)
        errors = _validate_fixture_metadata(payload, path.stem, expected_type="html")
        fixture_id = str(payload["fixture_id"])
        html_path = fixtures_dir / f"{fixture_id}.html"
        errors.extend(_validate_html_fixture(payload, html_path))
        status = "passed" if not errors else "failed"
        failures += 0 if not errors else 1
        results.append(
            {
                "fixture_id": fixture_id,
                "status": status,
                "errors": errors,
            }
        )
    return _suite_summary("html", fixtures_dir, results, failures)


def _validate_fixture_metadata(payload: dict[str, Any], file_stem: str, *, expected_type: str) -> list[str]:
    errors: list[str] = []
    fixture_id = str(payload.get("fixture_id", ""))
    if fixture_id != file_stem:
        errors.append(f"fixture_id mismatch: expected {file_stem!r}, got {fixture_id!r}")
    if expected_type == "message":
        tags = payload.get("tags", [])
        if not isinstance(tags, list) or not tags:
            errors.append("tags: expected a non-empty list")
        else:
            invalid_tags = sorted({str(item) for item in tags if str(item) not in CONTROLLED_TAGS})
            if invalid_tags:
                errors.append(f"tags: invalid controlled tags {invalid_tags!r}")
        if payload.get("source_type") != SOURCE_TYPE:
            errors.append(f"source_type: expected {SOURCE_TYPE!r}, got {payload.get('source_type')!r}")
        if payload.get("channel_id") != payload.get("channel_username"):
            errors.append(
                "channel_id: expected the normalized public-web channel id to match channel_username"
            )
    if expected_type == "scenario" and not payload.get("scenario_id"):
        errors.append("scenario_id: missing value")
    if expected_type == "html" and not payload.get("channel_username"):
        errors.append("channel_username: missing value")
    return errors


def _validate_message_fixture(payload: dict[str, Any], ai: OpenClawAI, risk: RiskEngine) -> list[str]:
    errors: list[str] = []
    message = NormalizedMessage.from_public_web(
        str(payload["channel_username"]),
        str(payload["event_type"]),
        {
            "channel_username": payload["channel_username"],
            "message_id": payload["message_id"],
            "date": payload["date"],
            "text": payload.get("text", ""),
            "caption": payload.get("caption", ""),
        },
        version=int(payload["version"]),
        edit_date=payload.get("edit_date"),
    )
    normalized_expected = dict(payload.get("expected_normalized_message", {}))
    actual_normalized = {
        "adapter": message.adapter,
        "chat_id": message.chat_id,
        "event_type": message.event_type,
        "version": message.version,
        "content_text": message.content_text(),
    }
    errors.extend(_compare_subset(actual_normalized, normalized_expected, "expected_normalized_message"))

    intent = ai.parse(message, [], {})
    expected_parser = dict(payload.get("expected_parser", {}))
    actual_intent = intent.to_dict()
    errors.extend(_compare_subset(actual_intent, expected_parser.get("exact", {}), "expected_parser.exact"))
    errors.extend(_compare_subset(actual_intent, expected_parser.get("contains", {}), "expected_parser.contains"))
    errors.extend(_validate_absent_fields(actual_intent, expected_parser.get("absent", []), "expected_parser.absent"))
    errors.extend(_validate_non_exact_fields(actual_intent, expected_parser.get("non_exact", []), "expected_parser.non_exact"))
    errors.extend(_compare_subset(intent.raw, expected_parser.get("raw_exact", {}), "expected_parser.raw_exact"))

    risk_result = risk.evaluate(message, intent, duplicate_exists=False)
    actual_risk = {
        "approved": risk_result.approved,
        "code": risk_result.code,
    }
    errors.extend(_compare_subset(actual_risk, payload.get("expected_risk", {}), "expected_risk"))
    return errors


def _validate_scenario_fixture(payload: dict[str, Any], message_index: dict[str, dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    referenced_messages = _scenario_referenced_messages(payload, message_index)
    if any(message is None for message in referenced_messages):
        for fixture_id, message_payload in referenced_messages.items():
            if message_payload is None:
                errors.append(f"Unknown message fixture reference: {fixture_id}")
        return errors

    with tempfile.TemporaryDirectory(prefix="fixture-suite-") as temp_dir:
        temp_root = Path(temp_dir)
        runtime, config_path = _build_scenario_runtime(temp_root, payload, referenced_messages)
        try:
            for event_index, event in enumerate(payload.get("events", [])):
                message_payload = referenced_messages[str(event["fixture_id"])]
                replay_count = int(event.get("replay_count", 1))
                for _ in range(replay_count):
                    runtime.process_message(_message_from_fixture(message_payload))
                for step in payload.get("reconcile_steps", []):
                    if int(step.get("after_event_index", -1)) != event_index:
                        continue
                    for fixture_id in step.get("buffered_fixture_ids", []):
                        buffer_payload = referenced_messages.get(str(fixture_id))
                        if buffer_payload is None:
                            errors.append(f"Unknown reconcile buffer fixture reference: {fixture_id}")
                            continue
                        _buffer_fixture_for_reconcile(runtime, buffer_payload)
                    runtime.reconcile_now()
                if event_index in {int(item) for item in payload.get("restart_after_event_indexes", [])}:
                    runtime.stop()
                    runtime = Runtime(config_path)
        finally:
            snapshot = runtime.snapshot()
            runtime.stop()

    actual = {
        "message_versions_seen": sorted({int(item["version"]) for item in snapshot["messages"]}),
        "distinct_orders": len(snapshot["orders"]),
        "latest_action": snapshot["orders"][0]["action"] if snapshot["orders"] else "",
        "latest_message_status": snapshot["messages"][0]["status"] if snapshot["messages"] else "",
        "total_messages_persisted": len(snapshot["messages"]),
    }
    errors.extend(_compare_subset(actual, payload.get("expected_chain_outcome", {}), "expected_chain_outcome"))
    actual_last_reconcile = dict(snapshot["operator_state"]["last_reconcile"])
    errors.extend(
        _compare_subset(actual_last_reconcile, payload.get("expected_last_reconcile", {}), "expected_last_reconcile")
    )
    actual_open_positions = [
        {
            "symbol": str(item["symbol"]),
            "side": str(item["payload"].get("side", "")),
            "qty": float(item["payload"].get("qty", 0.0)),
        }
        for item in snapshot["positions"]
        if float(item["payload"].get("qty", 0.0)) > 0
        and str(item["payload"].get("side", "")) in {"long", "short"}
    ]
    errors.extend(_compare_subset(actual_open_positions, payload.get("expected_open_positions", []), "expected_open_positions"))
    return errors


def _validate_html_fixture(payload: dict[str, Any], html_path: Path) -> list[str]:
    errors: list[str] = []
    if not html_path.is_file():
        return [f"Missing HTML companion file: {html_path.name}"]
    html = html_path.read_text(encoding="utf-8")
    actual_posts = parse_public_channel_html(str(payload["channel_username"]), html)
    expected_posts = payload.get("expected_posts", [])
    if actual_posts != expected_posts:
        errors.append(
            "expected_posts mismatch: "
            f"expected={json.dumps(expected_posts, sort_keys=True)} "
            f"actual={json.dumps(actual_posts, sort_keys=True)}"
        )
    return errors


def _suite_summary(
    fixture_type: str,
    fixtures_dir: Path,
    results: list[dict[str, Any]],
    failures: int,
) -> dict[str, Any]:
    return {
        "suite_status": "failed" if failures else "passed",
        "fixture_type": fixture_type,
        "fixtures_dir": str(fixtures_dir),
        "passed_count": len(results) - failures,
        "failed_count": failures,
        "results": results,
    }


def _build_manifest(base_dir: Path) -> dict[str, Any]:
    base_dir = Path(base_dir)
    fixture_channels = {
        str(payload["fixture_id"]): str(payload["channel_username"])
        for payload in SEED_MESSAGE_FIXTURES
    }
    message_ids = [payload["fixture_id"] for payload in SEED_MESSAGE_FIXTURES]
    scenario_ids = [payload["fixture_id"] for payload in SEED_SCENARIO_FIXTURES]
    html_ids = [payload["fixture_id"] for payload in SEED_HTML_FIXTURES]
    action_fixture_ids = [payload["fixture_id"] for payload in SEED_MESSAGE_FIXTURES if str(payload["scenario_id"]).startswith("action-")]
    noise_fixture_ids = [payload["fixture_id"] for payload in SEED_MESSAGE_FIXTURES if str(payload["scenario_id"]).startswith("noise-")]
    edit_fixture_ids = [payload["fixture_id"] for payload in SEED_MESSAGE_FIXTURES if str(payload["scenario_id"]).startswith("edit-")]
    edit_chains = _edit_chain_manifest_entries()
    scenario_categories = _scenario_category_index()
    tag_counts = _message_tag_counts(SEED_MESSAGE_FIXTURES)
    coverage_targets = {
        "action_coverage": 66,
        "noise_ignore_coverage": 24,
        "edit_version_coverage": 18,
        "replay_reconcile_dedup_coverage": 12,
    }
    bucket_actuals = {
        "action_coverage": len(action_fixture_ids),
        "noise_ignore_coverage": len(noise_fixture_ids),
        "edit_version_coverage": len(edit_fixture_ids),
        "replay_reconcile_dedup_coverage": len(scenario_ids),
    }
    coverage_summary = {
        bucket: {
            "target": target,
            "actual": bucket_actuals[bucket],
            "delta": bucket_actuals[bucket] - target,
            "meets_target": bucket_actuals[bucket] >= target,
        }
        for bucket, target in coverage_targets.items()
    }
    action_distribution = {
        action: {
            "count": len([payload for payload in SEED_MESSAGE_FIXTURES if action in payload["tags"]]),
            "fixture_ids": [payload["fixture_id"] for payload in SEED_MESSAGE_FIXTURES if action in payload["tags"]],
        }
        for action in ACTION_FAMILIES
    }
    return {
        "corpus_version": CORPUS_VERSION,
        "generated_at": SEED_GENERATED_AT,
        "source_type": SOURCE_TYPE,
        "generator": "scripts/generate_public_web_fixtures.py",
        "root": str(base_dir),
        "counts": {
            "messages": len(message_ids),
            "scenarios": len(scenario_ids),
            "html": len(html_ids),
            "total_samples": len(message_ids) + len(scenario_ids) + len(html_ids),
            "milestone_bucket_total": sum(bucket_actuals.values()),
        },
        "coverage": {
            "summary": coverage_summary,
            "bucket_allocations": {
                "action_coverage": {
                    "actual": bucket_actuals["action_coverage"],
                    "fixture_ids": action_fixture_ids,
                    "action_family_minimum": 6,
                    "families": {
                        action: {
                            "actual": len(
                                [
                                    payload
                                    for payload in SEED_MESSAGE_FIXTURES
                                    if payload["fixture_id"] in action_fixture_ids and action in payload["tags"]
                                ]
                            ),
                            "fixture_ids": [
                                payload["fixture_id"]
                                for payload in SEED_MESSAGE_FIXTURES
                                if payload["fixture_id"] in action_fixture_ids and action in payload["tags"]
                            ],
                        }
                        for action in ACTION_FAMILIES
                    },
                },
                "noise_ignore_coverage": {
                    "actual": bucket_actuals["noise_ignore_coverage"],
                    "fixture_ids": noise_fixture_ids,
                    "categories": {
                        tag: {
                            "count": len(
                                [
                                    payload
                                    for payload in SEED_MESSAGE_FIXTURES
                                    if payload["fixture_id"] in noise_fixture_ids and tag in payload["tags"]
                                ]
                            ),
                            "fixture_ids": [
                                payload["fixture_id"]
                                for payload in SEED_MESSAGE_FIXTURES
                                if payload["fixture_id"] in noise_fixture_ids and tag in payload["tags"]
                            ],
                        }
                        for tag in ("promo", "hold", "tp_text", "sl_text", "breakeven")
                    },
                },
                "edit_version_coverage": {
                    "actual": bucket_actuals["edit_version_coverage"],
                    "fixture_ids": edit_fixture_ids,
                    "chains": edit_chains,
                },
                "replay_reconcile_dedup_coverage": {
                    "actual": bucket_actuals["replay_reconcile_dedup_coverage"],
                    "scenario_ids": scenario_ids,
                    "categories": scenario_categories,
                },
            },
        },
        "channels": {
            channel_username: {
                "messages": len([payload for payload in SEED_MESSAGE_FIXTURES if payload["channel_username"] == channel_username]),
                "scenarios": len(
                    [
                        payload
                        for payload in SEED_SCENARIO_FIXTURES
                        if any(
                            fixture_channels.get(str(event.get("fixture_id"))) == channel_username
                            for event in payload.get("events", [])
                        )
                    ]
                ),
                "html": len([payload for payload in SEED_HTML_FIXTURES if payload["channel_username"] == channel_username]),
            }
            for channel_username in CHANNEL_FIXTURE_ORDER
        },
        "message_tag_counts": tag_counts,
        "message_action_distribution": action_distribution,
        "fixtures": {
            "messages": message_ids,
            "scenarios": scenario_ids,
            "html": html_ids,
        },
    }


def _build_offline_config() -> AppConfig:
    config = AppConfig()
    config.ai.provider = "heuristic"
    config.ai.model = "fixture-suite"
    config.ai.thinking = "off"
    config.trading.mode = "demo"
    config.trading.execution_mode = "automatic"
    config.okx.enabled = False
    return config


def _build_scenario_runtime(
    temp_root: Path,
    payload: dict[str, Any],
    referenced_messages: dict[str, dict[str, Any] | None],
) -> tuple[Runtime, Path]:
    config = _build_offline_config()
    config.runtime.data_dir = str(temp_root / "data")
    config.runtime.sqlite_path = str(temp_root / "data" / "app.db")
    if payload.get("reconcile_steps"):
        config.telegram.bot_token = "fixture-buffer-token"
    config.telegram.channels = _scenario_channels(referenced_messages)
    config_path = temp_root / "config.json"
    save_config(config, config_path)
    return Runtime(config_path), config_path


def _scenario_referenced_messages(
    payload: dict[str, Any],
    message_index: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any] | None]:
    fixture_ids = {str(event["fixture_id"]) for event in payload.get("events", [])}
    for step in payload.get("reconcile_steps", []):
        fixture_ids.update(str(item) for item in step.get("buffered_fixture_ids", []))
    return {fixture_id: message_index.get(fixture_id) for fixture_id in sorted(fixture_ids)}


def _scenario_channels(referenced_messages: dict[str, dict[str, Any] | None]) -> list[ChannelConfig]:
    channels: list[ChannelConfig] = []
    seen: set[str] = set()
    for payload in referenced_messages.values():
        if payload is None:
            continue
        channel_username = str(payload["channel_username"])
        if channel_username in seen:
            continue
        seen.add(channel_username)
        channels.append(
            ChannelConfig(
                id=f"fixture-buffer-{channel_username}",
                name=f"Fixture Buffer {channel_username}",
                source_type="bot_api",
                chat_id=f"public:{channel_username}",
                channel_username=channel_username,
                enabled=True,
                listen_new_messages=True,
                listen_edits=True,
            )
        )
    return channels


def _buffer_fixture_for_reconcile(runtime: Runtime, payload: dict[str, Any]) -> None:
    channel = next(
        (
            channel
            for channel in runtime.config_manager.get().telegram.channels
            if channel.chat_id == f"public:{payload['channel_username']}"
        ),
        None,
    )
    if channel is None:
        raise ValueError(f"Scenario runtime is missing reconcile channel config for {payload['channel_username']}")
    runtime.telegram._remember_message(channel, _telegram_buffer_message(payload))


def _telegram_buffer_message(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "message_id": int(payload["message_id"]),
        "date": _iso_to_timestamp(str(payload["date"])),
        "edit_date": _iso_to_timestamp(str(payload["edit_date"])) if payload.get("edit_date") else None,
        "text": str(payload.get("text", "")),
        "caption": str(payload.get("caption", "")),
        "chat": {
            "id": f"public:{payload['channel_username']}",
            "username": str(payload["channel_username"]),
        },
    }


def _message_from_fixture(payload: dict[str, Any]) -> NormalizedMessage:
    return NormalizedMessage.from_public_web(
        str(payload["channel_username"]),
        str(payload["event_type"]),
        {
            "channel_username": payload["channel_username"],
            "message_id": payload["message_id"],
            "date": payload["date"],
            "text": payload.get("text", ""),
            "caption": payload.get("caption", ""),
        },
        version=int(payload["version"]),
        edit_date=payload.get("edit_date"),
    )


def _load_message_index(messages_dir: Path) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for path in sorted(Path(messages_dir).glob("*.json")):
        payload = _read_json(path)
        index[str(payload["fixture_id"])] = payload
    return index


def _compare_subset(actual: Any, expected: Any, path: str) -> list[str]:
    errors: list[str] = []
    if expected in ({}, []):
        return errors
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{path}: expected dict, got {type(actual).__name__}"]
        for key, value in expected.items():
            if key not in actual:
                errors.append(f"{path}.{key}: missing key")
                continue
            errors.extend(_compare_subset(actual[key], value, f"{path}.{key}"))
        return errors
    if isinstance(expected, list):
        if actual != expected:
            errors.append(f"{path}: expected {expected!r}, got {actual!r}")
        return errors
    if actual != expected:
        errors.append(f"{path}: expected {expected!r}, got {actual!r}")
    return errors


def _validate_absent_fields(actual: dict[str, Any], absent_fields: list[str], path: str) -> list[str]:
    errors: list[str] = []
    for field_name in absent_fields:
        value = actual.get(str(field_name))
        if value not in (None, [], {}, ""):
            errors.append(f"{path}.{field_name}: expected a falsy/empty value, got {value!r}")
    return errors


def _validate_non_exact_fields(actual: dict[str, Any], fields: list[str], path: str) -> list[str]:
    expected_types = {
        "confidence": (int, float),
        "reason": (str,),
        "raw": (dict,),
    }
    errors: list[str] = []
    for field_name in fields:
        key = str(field_name)
        if key not in actual:
            errors.append(f"{path}.{key}: missing key")
            continue
        value = actual[key]
        type_options = expected_types.get(key)
        if type_options and not isinstance(value, type_options):
            type_names = ", ".join(option.__name__ for option in type_options)
            errors.append(f"{path}.{key}: expected type {type_names}, got {type(value).__name__}")
            continue
        if key == "reason" and not str(value).strip():
            errors.append(f"{path}.{key}: expected a non-empty string")
        if key == "raw" and not value:
            errors.append(f"{path}.{key}: expected a non-empty mapping")
    return errors


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _reset_fixture_dir(directory: Path, pattern: str) -> None:
    for path in sorted(directory.glob(pattern)):
        if path.is_file():
            path.unlink()


def _message_tag_counts(fixtures: list[dict[str, Any]]) -> dict[str, int]:
    counts = {tag: 0 for tag in sorted(CONTROLLED_TAGS)}
    for payload in fixtures:
        for tag in payload.get("tags", []):
            counts[str(tag)] += 1
    return counts


def _edit_chain_manifest_entries() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    chains: dict[str, list[str]] = {}
    for payload in SEED_MESSAGE_FIXTURES:
        scenario_id = str(payload["scenario_id"])
        if not scenario_id.startswith("edit-"):
            continue
        chains.setdefault(scenario_id, []).append(str(payload["fixture_id"]))
    for scenario_id in sorted(chains):
        fixture_ids = sorted(chains[scenario_id], key=lambda item: (int(item.split("-v")[1].split("-")[0]), item))
        entries.append(
            {
                "scenario_id": scenario_id,
                "fixture_ids": fixture_ids,
            }
        )
    return entries


def _scenario_category_index() -> dict[str, dict[str, Any]]:
    categories = {
        "dedup_same_version": [],
        "edit_chain": [],
        "mixed_channel_same_message_id": [],
        "reconcile_buffer_replay": [],
        "restart_persisted_state": [],
    }
    for payload in SEED_SCENARIO_FIXTURES:
        category = str(payload.get("coverage_category", ""))
        if category in categories:
            categories[category].append(str(payload["fixture_id"]))
    return {
        key: {
            "count": len(value),
            "scenario_ids": value,
        }
        for key, value in categories.items()
    }


def _iso_at(minute_offset: int) -> str:
    return (BASE_MESSAGE_DATE + timedelta(minutes=minute_offset)).replace(microsecond=0).isoformat()


def _iso_plus(value: str, minutes: int) -> str:
    return (
        datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc) + timedelta(minutes=minutes)
    ).replace(microsecond=0).isoformat()


def _iso_to_timestamp(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp())


def _normalize_tags(action: str, *extra_tags: str) -> list[str]:
    tags = [action, "public_web", *extra_tags]
    normalized: list[str] = []
    for item in tags:
        if item and item not in normalized:
            normalized.append(item)
    return normalized


def _message_fixture(
    *,
    fixture_id: str,
    scenario_id: str,
    channel_username: str,
    message_id: int,
    event_type: str,
    version: int,
    date: str,
    edit_date: str | None,
    text: str,
    tags: list[str],
    action: str,
    symbol: str,
    leverage: int = 20,
    size_value: float = 100.0,
    tp: list[dict[str, Any]] | None = None,
    sl: dict[str, Any] | None = None,
    trailing: dict[str, Any] | None = None,
    executable: bool = True,
    risk_approved: bool | None = None,
    risk_code: str | None = None,
    notes: str = "",
) -> dict[str, Any]:
    exact = {
        "executable": executable,
        "action": action,
        "symbol": symbol,
        "market_type": "swap",
        "side": ACTION_SIDE[action],
        "entry_type": "market",
        "size_mode": "fixed_usdt",
        "size_value": float(size_value),
        "leverage": leverage,
        "margin_mode": "isolated",
        "require_manual_confirmation": False,
    }
    if tp is not None:
        exact["tp"] = tp
    if sl is not None:
        exact["sl"] = sl
    if trailing is not None:
        exact["trailing"] = trailing
    derived_risk_approved = executable and action != "ignore"
    derived_risk_code = "approved" if derived_risk_approved else "not_executable"
    return {
        "fixture_id": fixture_id,
        "scenario_id": scenario_id,
        "source_type": SOURCE_TYPE,
        "channel_username": channel_username,
        "channel_id": channel_username,
        "message_id": message_id,
        "event_type": event_type,
        "version": version,
        "date": date,
        "edit_date": edit_date,
        "text": text,
        "caption": "",
        "html_fragment_path": None,
        "tags": tags,
        "expected_normalized_message": {
            "adapter": "public_web",
            "chat_id": f"public:{channel_username}",
            "event_type": event_type,
            "version": version,
            "content_text": text,
        },
        "expected_parser": {
            "provider_mode": "heuristic_or_openclaw",
            "exact": exact,
            "contains": {},
            "absent": [],
            "non_exact": ["confidence", "reason", "raw"],
            "raw_exact": {
                "parser_source": "heuristic",
                "requested_provider": "heuristic",
            },
        },
        "expected_risk": {
            "approved": derived_risk_approved if risk_approved is None else risk_approved,
            "code": derived_risk_code if risk_code is None else risk_code,
        },
        "expected_execution_path": {
            "default_manual_inject": "simulated_demo",
            "configured_okx_demo": "real_demo_rest",
        },
        "notes": notes,
    }


def _fixture_id(channel_username: str, message_id: int, version: int, label: str) -> str:
    return f"pw-{channel_username}-{message_id}-v{version}-{label}"


def _message_action(
    *,
    channel_username: str,
    message_id: int,
    label: str,
    scenario_id: str,
    date: str,
    text: str,
    action: str,
    symbol: str,
    tags: list[str],
    leverage: int = 20,
    size_value: float = 100.0,
    tp: list[dict[str, Any]] | None = None,
    sl: dict[str, Any] | None = None,
    trailing: dict[str, Any] | None = None,
    risk_approved: bool | None = None,
    risk_code: str | None = None,
    notes: str = "",
) -> dict[str, Any]:
    return _message_fixture(
        fixture_id=_fixture_id(channel_username, message_id, 1, label),
        scenario_id=scenario_id,
        channel_username=channel_username,
        message_id=message_id,
        event_type="new",
        version=1,
        date=date,
        edit_date=None,
        text=text,
        tags=tags,
        action=action,
        symbol=symbol,
        leverage=leverage,
        size_value=size_value,
        tp=tp,
        sl=sl,
        trailing=trailing,
        executable=action != "ignore",
        risk_approved=risk_approved,
        risk_code=risk_code,
        notes=notes,
    )


def _message_edit_pair(
    *,
    channel_username: str,
    message_id: int,
    scenario_id: str,
    label_v1: str,
    label_v2: str,
    text_v1: str,
    text_v2: str,
    action_v1: str,
    action_v2: str,
    symbol_v1: str,
    symbol_v2: str,
    base_date: str,
    tags_v1: list[str],
    tags_v2: list[str],
    leverage_v1: int = 20,
    leverage_v2: int = 20,
    size_value_v1: float = 100.0,
    size_value_v2: float = 100.0,
    tp_v1: list[dict[str, Any]] | None = None,
    tp_v2: list[dict[str, Any]] | None = None,
    sl_v1: dict[str, Any] | None = None,
    sl_v2: dict[str, Any] | None = None,
    trailing_v1: dict[str, Any] | None = None,
    trailing_v2: dict[str, Any] | None = None,
    notes_v1: str = "",
    notes_v2: str = "",
) -> list[dict[str, Any]]:
    return [
        _message_fixture(
            fixture_id=_fixture_id(channel_username, message_id, 1, label_v1),
            scenario_id=scenario_id,
            channel_username=channel_username,
            message_id=message_id,
            event_type="new",
            version=1,
            date=base_date,
            edit_date=None,
            text=text_v1,
            tags=tags_v1,
            action=action_v1,
            symbol=symbol_v1,
            leverage=leverage_v1,
            size_value=size_value_v1,
            tp=tp_v1,
            sl=sl_v1,
            trailing=trailing_v1,
            executable=action_v1 != "ignore",
            notes=notes_v1,
        ),
        _message_fixture(
            fixture_id=_fixture_id(channel_username, message_id, 2, label_v2),
            scenario_id=scenario_id,
            channel_username=channel_username,
            message_id=message_id,
            event_type="edit",
            version=2,
            date=base_date,
            edit_date=_iso_plus(base_date, 5),
            text=text_v2,
            tags=tags_v2,
            action=action_v2,
            symbol=symbol_v2,
            leverage=leverage_v2,
            size_value=size_value_v2,
            tp=tp_v2,
            sl=sl_v2,
            trailing=trailing_v2,
            executable=action_v2 != "ignore",
            notes=notes_v2,
        ),
    ]


def _build_action_messages() -> list[dict[str, Any]]:
    fixtures: list[dict[str, Any]] = []
    for payload in (
        _build_open_long_messages()
        + _build_open_short_messages()
        + _build_add_long_messages()
        + _build_add_short_messages()
        + _build_reduce_long_messages()
        + _build_reduce_short_messages()
        + _build_close_all_messages()
        + _build_reverse_to_long_messages()
        + _build_reverse_to_short_messages()
        + _build_cancel_messages()
        + _build_update_protection_messages()
    ):
        fixtures.append(payload)
    return fixtures


def _build_open_long_messages() -> list[dict[str, Any]]:
    rows = [
        ("cryptoninjas_trading_ann", 5101, "open-long-plain", "action-open-long-001", _iso_at(0), "LONG BTCUSDT", "BTC-USDT-SWAP", _normalize_tags("open_long", "en"), 20, 100.0, None, None, None),
        ("feiyangkanbi", 5102, "open-long-size", "action-open-long-002", _iso_at(1), "LONG ETHUSDT $250", "ETH-USDT-SWAP", _normalize_tags("open_long", "en"), 20, 250.0, None, None, None),
        ("fixture_public_alpha", 5103, "open-long-hashtag", "action-open-long-003", _iso_at(2), "#DOGE BUY", "DOGE-USDT-SWAP", _normalize_tags("open_long", "en", "hashtag"), 20, 100.0, None, None, None),
        ("cryptoninjas_trading_ann", 5104, "open-long-leverage", "action-open-long-004", _iso_at(3), "BUY SUIUSDT 12X", "SUI-USDT-SWAP", _normalize_tags("open_long", "en"), 12, 100.0, None, None, None),
        ("feiyangkanbi", 5105, "open-long-cn", "action-open-long-005", _iso_at(4), "做多 XRPUSDT", "XRP-USDT-SWAP", _normalize_tags("open_long", "cn"), 20, 100.0, None, None, None),
        ("fixture_public_alpha", 5901, "open-long-protection", "action-open-long-006", _iso_at(5), "LONG ADAUSDT TP 1.2 SL 0.9", "ADA-USDT-SWAP", _normalize_tags("open_long", "en", "tp_text", "sl_text", "dedup"), 20, 100.0, [{"trigger": 1.2}], {"trigger": 0.9}, None),
    ]
    return [
        _message_action(
            channel_username=channel_username,
            message_id=message_id,
            label=label,
            scenario_id=scenario_id,
            date=date,
            text=text,
            action="open_long",
            symbol=symbol,
            tags=tags,
            leverage=leverage,
            size_value=size_value,
            tp=tp,
            sl=sl,
        )
        for channel_username, message_id, label, scenario_id, date, text, symbol, tags, leverage, size_value, tp, sl, _trailing in rows
    ]


def _build_open_short_messages() -> list[dict[str, Any]]:
    rows = [
        ("cryptoninjas_trading_ann", 5201, "open-short-plain", "action-open-short-001", _iso_at(6), "SHORT SOLUSDT", "SOL-USDT-SWAP", _normalize_tags("open_short", "en", "replay"), 20, 100.0, None, None, None),
        ("feiyangkanbi", 5202, "open-short-size", "action-open-short-002", _iso_at(7), "SHORT AVAXUSDT $180", "AVAX-USDT-SWAP", _normalize_tags("open_short", "en"), 20, 180.0, None, None, None),
        ("fixture_public_alpha", 5203, "open-short-hashtag", "action-open-short-003", _iso_at(8), "#ARB SELL", "ARB-USDT-SWAP", _normalize_tags("open_short", "en", "hashtag"), 20, 100.0, None, None, None),
        ("cryptoninjas_trading_ann", 5204, "open-short-leverage", "action-open-short-004", _iso_at(9), "SELL OPUSDT 15X", "OP-USDT-SWAP", _normalize_tags("open_short", "en"), 15, 100.0, None, None, None),
        ("feiyangkanbi", 5205, "open-short-cn", "action-open-short-005", _iso_at(10), "开空 LINKUSDT", "LINK-USDT-SWAP", _normalize_tags("open_short", "cn"), 20, 100.0, None, None, None),
        ("cryptoninjas_trading_ann", 5901, "open-short-protection", "action-open-short-006", _iso_at(11), "SHORT TIAUSDT TP 8.6 SL 9.4", "TIA-USDT-SWAP", _normalize_tags("open_short", "en", "tp_text", "sl_text"), 20, 100.0, [{"trigger": 8.6}], {"trigger": 9.4}, None),
    ]
    return [
        _message_action(
            channel_username=channel_username,
            message_id=message_id,
            label=label,
            scenario_id=scenario_id,
            date=date,
            text=text,
            action="open_short",
            symbol=symbol,
            tags=tags,
            leverage=leverage,
            size_value=size_value,
            tp=tp,
            sl=sl,
        )
        for channel_username, message_id, label, scenario_id, date, text, symbol, tags, leverage, size_value, tp, sl, _trailing in rows
    ]


def _build_add_long_messages() -> list[dict[str, Any]]:
    rows = [
        ("cryptoninjas_trading_ann", 5301, "add-long-plain", "action-add-long-001", _iso_at(12), "ADD LONG BTCUSDT", "BTC-USDT-SWAP", _normalize_tags("add_long", "en"), 20, 100.0),
        ("feiyangkanbi", 5302, "add-long-size", "action-add-long-002", _iso_at(13), "ADD LONG ETHUSDT $150", "ETH-USDT-SWAP", _normalize_tags("add_long", "en"), 20, 150.0),
        ("fixture_public_alpha", 5303, "add-long-hashtag", "action-add-long-003", _iso_at(14), "ADD LONG #DOGE", "DOGE-USDT-SWAP", _normalize_tags("add_long", "en", "hashtag"), 20, 100.0),
        ("cryptoninjas_trading_ann", 5304, "add-long-leverage", "action-add-long-004", _iso_at(15), "ADD BUY SUIUSDT 8X", "SUI-USDT-SWAP", _normalize_tags("add_long", "en"), 8, 100.0),
        ("feiyangkanbi", 5305, "add-long-usdt", "action-add-long-005", _iso_at(16), "ADD LONG XRPUSDT USDT 175", "XRP-USDT-SWAP", _normalize_tags("add_long", "en"), 20, 175.0),
        ("fixture_public_alpha", 5306, "add-long-target", "action-add-long-006", _iso_at(17), "ADD LONG ADAUSDT TARGET 1.4", "ADA-USDT-SWAP", _normalize_tags("add_long", "en", "tp_text"), 20, 100.0),
    ]
    return [
        _message_action(
            channel_username=channel_username,
            message_id=message_id,
            label=label,
            scenario_id=scenario_id,
            date=date,
            text=text,
            action="add_long",
            symbol=symbol,
            tags=tags,
            leverage=leverage,
            size_value=size_value,
            tp=([{"trigger": 1.4}] if "TARGET 1.4" in text else None),
        )
        for channel_username, message_id, label, scenario_id, date, text, symbol, tags, leverage, size_value in rows
    ]


def _build_add_short_messages() -> list[dict[str, Any]]:
    rows = [
        ("cryptoninjas_trading_ann", 5401, "add-short-plain", "action-add-short-001", _iso_at(18), "ADD SHORT SOLUSDT", "SOL-USDT-SWAP", _normalize_tags("add_short", "en"), 20, 100.0),
        ("feiyangkanbi", 5402, "add-short-size", "action-add-short-002", _iso_at(19), "ADD SHORT AVAXUSDT $140", "AVAX-USDT-SWAP", _normalize_tags("add_short", "en"), 20, 140.0),
        ("fixture_public_alpha", 5403, "add-short-hashtag", "action-add-short-003", _iso_at(20), "ADD SHORT #ARB", "ARB-USDT-SWAP", _normalize_tags("add_short", "en", "hashtag"), 20, 100.0),
        ("cryptoninjas_trading_ann", 5404, "add-short-leverage", "action-add-short-004", _iso_at(21), "ADD SELL OPUSDT 9X", "OP-USDT-SWAP", _normalize_tags("add_short", "en"), 9, 100.0),
        ("feiyangkanbi", 5405, "add-short-usdt", "action-add-short-005", _iso_at(22), "ADD SHORT LINKUSDT USDT 165", "LINK-USDT-SWAP", _normalize_tags("add_short", "en"), 20, 165.0),
        ("fixture_public_alpha", 5406, "add-short-target", "action-add-short-006", _iso_at(23), "ADD SHORT TIAUSDT TP 8.1", "TIA-USDT-SWAP", _normalize_tags("add_short", "en", "tp_text"), 20, 100.0),
    ]
    return [
        _message_action(
            channel_username=channel_username,
            message_id=message_id,
            label=label,
            scenario_id=scenario_id,
            date=date,
            text=text,
            action="add_short",
            symbol=symbol,
            tags=tags,
            leverage=leverage,
            size_value=size_value,
            tp=([{"trigger": 8.1}] if "TP 8.1" in text else None),
        )
        for channel_username, message_id, label, scenario_id, date, text, symbol, tags, leverage, size_value in rows
    ]


def _build_reduce_long_messages() -> list[dict[str, Any]]:
    rows = [
        ("cryptoninjas_trading_ann", 5501, "reduce-long-plain", "action-reduce-long-001", _iso_at(24), "REDUCE LONG BTCUSDT", "BTC-USDT-SWAP", _normalize_tags("reduce_long", "en"), 20, 100.0),
        ("feiyangkanbi", 5502, "reduce-long-partial", "action-reduce-long-002", _iso_at(25), "PARTIAL LONG ETHUSDT", "ETH-USDT-SWAP", _normalize_tags("reduce_long", "en"), 20, 100.0),
        ("fixture_public_alpha", 5503, "reduce-long-size", "action-reduce-long-003", _iso_at(26), "REDUCE BUY DOGEUSDT $60", "DOGE-USDT-SWAP", _normalize_tags("reduce_long", "en"), 20, 60.0),
        ("cryptoninjas_trading_ann", 5504, "reduce-long-hashtag", "action-reduce-long-004", _iso_at(27), "REDUCE LONG #SUI", "SUI-USDT-SWAP", _normalize_tags("reduce_long", "en", "hashtag"), 20, 100.0),
        ("feiyangkanbi", 5505, "reduce-long-leverage", "action-reduce-long-005", _iso_at(28), "REDUCE LONG XRPUSDT 5X", "XRP-USDT-SWAP", _normalize_tags("reduce_long", "en"), 5, 100.0),
        ("fixture_public_alpha", 5506, "reduce-long-sized", "action-reduce-long-006", _iso_at(29), "PARTIAL LONG ADAUSDT SIZE 40", "ADA-USDT-SWAP", _normalize_tags("reduce_long", "en"), 20, 40.0),
    ]
    return [
        _message_action(
            channel_username=channel_username,
            message_id=message_id,
            label=label,
            scenario_id=scenario_id,
            date=date,
            text=text,
            action="reduce_long",
            symbol=symbol,
            tags=tags,
            leverage=leverage,
            size_value=size_value,
        )
        for channel_username, message_id, label, scenario_id, date, text, symbol, tags, leverage, size_value in rows
    ]


def _build_reduce_short_messages() -> list[dict[str, Any]]:
    rows = [
        ("cryptoninjas_trading_ann", 5601, "reduce-short-plain", "action-reduce-short-001", _iso_at(30), "REDUCE SHORT SOLUSDT", "SOL-USDT-SWAP", _normalize_tags("reduce_short", "en"), 20, 100.0),
        ("feiyangkanbi", 5602, "reduce-short-partial", "action-reduce-short-002", _iso_at(31), "PARTIAL SHORT AVAXUSDT", "AVAX-USDT-SWAP", _normalize_tags("reduce_short", "en"), 20, 100.0),
        ("fixture_public_alpha", 5603, "reduce-short-size", "action-reduce-short-003", _iso_at(32), "REDUCE SELL ARBUSDT $55", "ARB-USDT-SWAP", _normalize_tags("reduce_short", "en"), 20, 55.0),
        ("cryptoninjas_trading_ann", 5604, "reduce-short-hashtag", "action-reduce-short-004", _iso_at(33), "REDUCE SHORT #OP", "OP-USDT-SWAP", _normalize_tags("reduce_short", "en", "hashtag"), 20, 100.0),
        ("feiyangkanbi", 5605, "reduce-short-leverage", "action-reduce-short-005", _iso_at(34), "REDUCE SHORT LINKUSDT 6X", "LINK-USDT-SWAP", _normalize_tags("reduce_short", "en"), 6, 100.0),
        ("fixture_public_alpha", 5606, "reduce-short-sized", "action-reduce-short-006", _iso_at(35), "PARTIAL SHORT TIAUSDT SIZE 35", "TIA-USDT-SWAP", _normalize_tags("reduce_short", "en"), 20, 35.0),
    ]
    return [
        _message_action(
            channel_username=channel_username,
            message_id=message_id,
            label=label,
            scenario_id=scenario_id,
            date=date,
            text=text,
            action="reduce_short",
            symbol=symbol,
            tags=tags,
            leverage=leverage,
            size_value=size_value,
        )
        for channel_username, message_id, label, scenario_id, date, text, symbol, tags, leverage, size_value in rows
    ]


def _build_close_all_messages() -> list[dict[str, Any]]:
    rows = [
        ("cryptoninjas_trading_ann", 5701, "close-all-plain", "action-close-all-001", _iso_at(36), "CLOSE BTCUSDT", "BTC-USDT-SWAP", _normalize_tags("close_all", "en"), 20, 100.0),
        ("feiyangkanbi", 5702, "close-all-now", "action-close-all-002", _iso_at(37), "CLOSE ETHUSDT now", "ETH-USDT-SWAP", _normalize_tags("close_all", "en"), 20, 100.0),
        ("fixture_public_alpha", 5703, "close-all-hashtag", "action-close-all-003", _iso_at(38), "#DOGE CLOSE", "DOGE-USDT-SWAP", _normalize_tags("close_all", "en", "hashtag"), 20, 100.0),
        ("cryptoninjas_trading_ann", 5704, "close-all-leverage", "action-close-all-004", _iso_at(39), "CLOSE SUIUSDT 5X", "SUI-USDT-SWAP", _normalize_tags("close_all", "en"), 5, 100.0),
        ("feiyangkanbi", 5705, "close-all-capitalized", "action-close-all-005", _iso_at(40), "Close XRPUSDT asap", "XRP-USDT-SWAP", _normalize_tags("close_all", "en"), 20, 100.0),
        ("fixture_public_alpha", 5706, "close-all-target", "action-close-all-006", _iso_at(41), "CLOSE ADAUSDT TP 1.1", "ADA-USDT-SWAP", _normalize_tags("close_all", "en", "tp_text"), 20, 100.0),
    ]
    return [
        _message_action(
            channel_username=channel_username,
            message_id=message_id,
            label=label,
            scenario_id=scenario_id,
            date=date,
            text=text,
            action="close_all",
            symbol=symbol,
            tags=tags,
            leverage=leverage,
            size_value=size_value,
            tp=([{"trigger": 1.1}] if "TP 1.1" in text else None),
        )
        for channel_username, message_id, label, scenario_id, date, text, symbol, tags, leverage, size_value in rows
    ]


def _build_reverse_to_long_messages() -> list[dict[str, Any]]:
    rows = [
        ("cryptoninjas_trading_ann", 5801, "reverse-long-plain", "action-reverse-long-001", _iso_at(42), "REVERSE BTCUSDT LONG", "BTC-USDT-SWAP", _normalize_tags("reverse_to_long", "en"), 20, 100.0),
        ("feiyangkanbi", 5802, "reverse-long-flip", "action-reverse-long-002", _iso_at(43), "FLIP ETHUSDT TO LONG", "ETH-USDT-SWAP", _normalize_tags("reverse_to_long", "en"), 20, 100.0),
        ("fixture_public_alpha", 5803, "reverse-long-hashtag", "action-reverse-long-003", _iso_at(44), "#DOGE REVERSE LONG", "DOGE-USDT-SWAP", _normalize_tags("reverse_to_long", "en", "hashtag"), 20, 100.0),
        ("cryptoninjas_trading_ann", 5804, "reverse-long-leverage", "action-reverse-long-004", _iso_at(45), "REVERSE LONG SUIUSDT 7X", "SUI-USDT-SWAP", _normalize_tags("reverse_to_long", "en"), 7, 100.0),
        ("feiyangkanbi", 5805, "reverse-long-size", "action-reverse-long-005", _iso_at(46), "REVERSE LONG XRPUSDT $180", "XRP-USDT-SWAP", _normalize_tags("reverse_to_long", "en"), 20, 180.0),
        ("fixture_public_alpha", 5806, "reverse-long-mixed", "action-reverse-long-006", _iso_at(47), "REVERSE 做多 ADAUSDT", "ADA-USDT-SWAP", _normalize_tags("reverse_to_long", "mixed"), 20, 100.0),
    ]
    return [
        _message_action(
            channel_username=channel_username,
            message_id=message_id,
            label=label,
            scenario_id=scenario_id,
            date=date,
            text=text,
            action="reverse_to_long",
            symbol=symbol,
            tags=tags,
            leverage=leverage,
            size_value=size_value,
        )
        for channel_username, message_id, label, scenario_id, date, text, symbol, tags, leverage, size_value in rows
    ]


def _build_reverse_to_short_messages() -> list[dict[str, Any]]:
    rows = [
        ("cryptoninjas_trading_ann", 5902, "reverse-short-plain", "action-reverse-short-001", _iso_at(48), "REVERSE BTCUSDT SHORT", "BTC-USDT-SWAP", _normalize_tags("reverse_to_short", "en"), 20, 100.0),
        ("feiyangkanbi", 5903, "reverse-short-flip", "action-reverse-short-002", _iso_at(49), "FLIP ETHUSDT TO SHORT", "ETH-USDT-SWAP", _normalize_tags("reverse_to_short", "en"), 20, 100.0),
        ("fixture_public_alpha", 5904, "reverse-short-hashtag", "action-reverse-short-003", _iso_at(50), "#DOGE REVERSE SHORT", "DOGE-USDT-SWAP", _normalize_tags("reverse_to_short", "en", "hashtag"), 20, 100.0),
        ("cryptoninjas_trading_ann", 5905, "reverse-short-leverage", "action-reverse-short-004", _iso_at(51), "REVERSE SHORT SUIUSDT 11X", "SUI-USDT-SWAP", _normalize_tags("reverse_to_short", "en"), 11, 100.0),
        ("feiyangkanbi", 5906, "reverse-short-size", "action-reverse-short-005", _iso_at(52), "REVERSE SHORT XRPUSDT $175", "XRP-USDT-SWAP", _normalize_tags("reverse_to_short", "en"), 20, 175.0),
        ("fixture_public_alpha", 5907, "reverse-short-mixed", "action-reverse-short-006", _iso_at(53), "REVERSE 开空 ADAUSDT", "ADA-USDT-SWAP", _normalize_tags("reverse_to_short", "mixed"), 20, 100.0),
    ]
    return [
        _message_action(
            channel_username=channel_username,
            message_id=message_id,
            label=label,
            scenario_id=scenario_id,
            date=date,
            text=text,
            action="reverse_to_short",
            symbol=symbol,
            tags=tags,
            leverage=leverage,
            size_value=size_value,
        )
        for channel_username, message_id, label, scenario_id, date, text, symbol, tags, leverage, size_value in rows
    ]


def _build_cancel_messages() -> list[dict[str, Any]]:
    rows = [
        ("cryptoninjas_trading_ann", 6001, "cancel-plain", "action-cancel-001", _iso_at(54), "CANCEL BTCUSDT", "BTC-USDT-SWAP", _normalize_tags("cancel_orders", "en"), 20),
        ("feiyangkanbi", 6002, "cancel-entry", "action-cancel-002", _iso_at(55), "#ETH cancel entry limit", "ETH-USDT-SWAP", _normalize_tags("cancel_orders", "en", "hashtag"), 20),
        ("fixture_public_alpha", 6003, "cancel-pending", "action-cancel-003", _iso_at(56), "CANCEL DOGEUSDT pending", "DOGE-USDT-SWAP", _normalize_tags("cancel_orders", "en"), 20),
        ("cryptoninjas_trading_ann", 6004, "cancel-leverage", "action-cancel-004", _iso_at(57), "CANCEL SUIUSDT 8X", "SUI-USDT-SWAP", _normalize_tags("cancel_orders", "en"), 8),
        ("feiyangkanbi", 6005, "cancel-orders", "action-cancel-005", _iso_at(58), "cancel XRPUSDT orders", "XRP-USDT-SWAP", _normalize_tags("cancel_orders", "en"), 20),
        ("fixture_public_alpha", 6006, "cancel-ladder", "action-cancel-006", _iso_at(59), "CANCEL ADAUSDT ladder", "ADA-USDT-SWAP", _normalize_tags("cancel_orders", "en"), 20),
    ]
    return [
        _message_action(
            channel_username=channel_username,
            message_id=message_id,
            label=label,
            scenario_id=scenario_id,
            date=date,
            text=text,
            action="cancel_orders",
            symbol=symbol,
            tags=tags,
            leverage=leverage,
            size_value=100.0,
        )
        for channel_username, message_id, label, scenario_id, date, text, symbol, tags, leverage in rows
    ]


def _build_update_protection_messages() -> list[dict[str, Any]]:
    rows = [
        ("cryptoninjas_trading_ann", 6101, "update-protection-sl", "action-update-protection-001", _iso_at(60), "BTCUSDT STOP LOSS 82000", "BTC-USDT-SWAP", _normalize_tags("update_protection", "en", "sl_text"), 20, 100.0, None, {"trigger": 82000.0}, None),
        ("feiyangkanbi", 6102, "update-protection-tp", "action-update-protection-002", _iso_at(61), "ETHUSDT TAKE PROFIT 2600", "ETH-USDT-SWAP", _normalize_tags("update_protection", "en", "tp_text"), 20, 100.0, [{"trigger": 2600.0}], None, None),
        ("fixture_public_alpha", 6103, "update-protection-both", "action-update-protection-003", _iso_at(62), "DOGEUSDT PROTECTION TP 0.24 SL 0.18", "DOGE-USDT-SWAP", _normalize_tags("update_protection", "en", "tp_text", "sl_text"), 20, 100.0, [{"trigger": 0.24}], {"trigger": 0.18}, None),
        ("cryptoninjas_trading_ann", 6104, "update-protection-trailing", "action-update-protection-004", _iso_at(63), "SUIUSDT TRAILING 3.2", "SUI-USDT-SWAP", _normalize_tags("update_protection", "en"), 20, 100.0, None, None, {"trigger": 3.2}),
        ("feiyangkanbi", 6105, "update-protection-update-sl", "action-update-protection-005", _iso_at(64), "XRPUSDT UPDATE SL 0.55", "XRP-USDT-SWAP", _normalize_tags("update_protection", "en", "sl_text"), 20, 100.0, None, {"trigger": 0.55}, None),
        ("fixture_public_alpha", 6106, "update-protection-multi-target", "action-update-protection-006", _iso_at(65), "ADAUSDT UPDATE TP 1.15 TARGET 1.25", "ADA-USDT-SWAP", _normalize_tags("update_protection", "en", "tp_text"), 20, 100.0, [{"trigger": 1.15}, {"trigger": 1.25}], None, None),
    ]
    return [
        _message_action(
            channel_username=channel_username,
            message_id=message_id,
            label=label,
            scenario_id=scenario_id,
            date=date,
            text=text,
            action="update_protection",
            symbol=symbol,
            tags=tags,
            leverage=leverage,
            size_value=size_value,
            tp=tp,
            sl=sl,
            trailing=trailing,
        )
        for channel_username, message_id, label, scenario_id, date, text, symbol, tags, leverage, size_value, tp, sl, trailing in rows
    ]


def _build_noise_messages() -> list[dict[str, Any]]:
    rows = [
        ("cryptoninjas_trading_ann", 7001, "ignore-promo-vip", "noise-promo-001", _iso_at(66), "IGNORE VIP promo only, no entry in this post", _normalize_tags("ignore", "noise", "promo", "en"), ""),
        ("feiyangkanbi", 7002, "ignore-promo-referral", "noise-promo-002", _iso_at(67), "IGNORE referral drop, signal comes later", _normalize_tags("ignore", "noise", "promo", "en"), ""),
        ("fixture_public_alpha", 7003, "ignore-promo-subscribe", "noise-promo-003", _iso_at(68), "IGNORE subscribe promo, trade details later", _normalize_tags("ignore", "noise", "promo", "en"), ""),
        ("cryptoninjas_trading_ann", 7004, "ignore-wait-no-chase", "noise-wait-001", _iso_at(69), "Wait for confirmation, no chase here", _normalize_tags("ignore", "noise", "en"), ""),
        ("feiyangkanbi", 7005, "ignore-wait-standby", "noise-wait-002", _iso_at(70), "Stand by, no fresh trigger yet", _normalize_tags("ignore", "noise", "en"), ""),
        ("fixture_public_alpha", 7006, "ignore-wait-flat", "noise-wait-003", _iso_at(71), "Still waiting on structure, no fresh trade now", _normalize_tags("ignore", "noise", "en"), ""),
        ("cryptoninjas_trading_ann", 7007, "ignore-hold-runners", "noise-hold-001", _iso_at(72), "Hold runners, already in profit", _normalize_tags("ignore", "noise", "hold", "en"), ""),
        ("feiyangkanbi", 7008, "ignore-hold-dont-chase", "noise-hold-002", _iso_at(73), "Hold only, do not chase this candle", _normalize_tags("ignore", "noise", "hold", "en"), ""),
        ("fixture_public_alpha", 7009, "ignore-hold-manage", "noise-hold-003", _iso_at(74), "Manage what you have, no new signal here", _normalize_tags("ignore", "noise", "hold", "en"), ""),
        ("cryptoninjas_trading_ann", 7010, "ignore-tp-btc", "noise-tp-001", _iso_at(75), "BTCUSDT 止盈拿下", _normalize_tags("ignore", "noise", "tp_text", "cn"), "BTC-USDT-SWAP"),
        ("feiyangkanbi", 7011, "ignore-tp-eth", "noise-tp-002", _iso_at(76), "ETHUSDT 止盈触发", _normalize_tags("ignore", "noise", "tp_text", "cn"), "ETH-USDT-SWAP"),
        ("fixture_public_alpha", 7012, "ignore-tp-sol", "noise-tp-003", _iso_at(77), "SOLUSDT 止盈已到", _normalize_tags("ignore", "noise", "tp_text", "cn"), "SOL-USDT-SWAP"),
        ("cryptoninjas_trading_ann", 7013, "ignore-sl-btc", "noise-sl-001", _iso_at(78), "BTCUSDT 止损触发", _normalize_tags("ignore", "noise", "sl_text", "cn"), "BTC-USDT-SWAP"),
        ("feiyangkanbi", 7014, "ignore-sl-eth", "noise-sl-002", _iso_at(79), "ETHUSDT 止损已出", _normalize_tags("ignore", "noise", "sl_text", "cn"), "ETH-USDT-SWAP"),
        ("fixture_public_alpha", 7015, "ignore-sl-sol", "noise-sl-003", _iso_at(80), "SOLUSDT 止损结束", _normalize_tags("ignore", "noise", "sl_text", "cn"), "SOL-USDT-SWAP"),
        ("cryptoninjas_trading_ann", 7016, "ignore-breakeven-btc", "noise-breakeven-001", _iso_at(81), "BTCUSDT 保本继续拿", _normalize_tags("ignore", "noise", "breakeven", "cn"), "BTC-USDT-SWAP"),
        ("feiyangkanbi", 7017, "ignore-breakeven-eth", "noise-breakeven-002", _iso_at(82), "ETHUSDT 浮盈中 底仓继续拿", _normalize_tags("ignore", "noise", "breakeven", "cn"), "ETH-USDT-SWAP"),
        ("fixture_public_alpha", 7018, "ignore-breakeven-sol", "noise-breakeven-003", _iso_at(83), "SOLUSDT 保本中 继续拿着", _normalize_tags("ignore", "noise", "breakeven", "cn"), "SOL-USDT-SWAP"),
        ("cryptoninjas_trading_ann", 7019, "ignore-explicit-ada", "noise-explicit-ignore-001", _iso_at(84), "IGNORE ADAUSDT update only", _normalize_tags("ignore", "noise", "en"), "ADA-USDT-SWAP"),
        ("feiyangkanbi", 7020, "ignore-explicit-op", "noise-explicit-ignore-002", _iso_at(85), "IGNORE OPUSDT management note", _normalize_tags("ignore", "noise", "en"), "OP-USDT-SWAP"),
        ("fixture_public_alpha", 7021, "ignore-explicit-link", "noise-explicit-ignore-003", _iso_at(86), "IGNORE LINKUSDT recap only", _normalize_tags("ignore", "noise", "en"), "LINK-USDT-SWAP"),
        ("cryptoninjas_trading_ann", 7022, "ignore-ambiguous-standby", "noise-ambiguous-001", _iso_at(87), "No fresh signal yet, standby", _normalize_tags("ignore", "noise", "en"), ""),
        ("feiyangkanbi", 7023, "ignore-ambiguous-quiet", "noise-ambiguous-002", _iso_at(88), "Quiet session, wait for the next setup", _normalize_tags("ignore", "noise", "en"), ""),
        ("fixture_public_alpha", 7024, "ignore-ambiguous-plan", "noise-ambiguous-003", _iso_at(89), "Plan only, no executable signal in this note", _normalize_tags("ignore", "noise", "en"), ""),
    ]
    fixtures: list[dict[str, Any]] = []
    for channel_username, message_id, label, scenario_id, date, text, tags, symbol in rows:
        fixtures.append(
            _message_action(
                channel_username=channel_username,
                message_id=message_id,
                label=label,
                scenario_id=scenario_id,
                date=date,
                text=text,
                action="ignore",
                symbol=symbol,
                tags=tags,
                size_value=0.0,
                risk_code="invalid_symbol" if not symbol else None,
                notes="Noise or management-only text should not create an executable order.",
            )
        )
    return fixtures


def _build_edit_messages() -> list[dict[str, Any]]:
    fixtures: list[dict[str, Any]] = []
    fixtures.extend(
        _message_edit_pair(
            channel_username="fixture_public_alpha",
            message_id=8101,
            scenario_id="edit-chain-001",
            label_v1="open-btc-long",
            label_v2="close-btc-edit",
            text_v1="LONG BTCUSDT",
            text_v2="CLOSE BTCUSDT now",
            action_v1="open_long",
            action_v2="close_all",
            symbol_v1="BTC-USDT-SWAP",
            symbol_v2="BTC-USDT-SWAP",
            base_date=_iso_at(90),
            tags_v1=_normalize_tags("open_long", "public_web", "en", "edit"),
            tags_v2=_normalize_tags("close_all", "public_web", "en", "edit"),
            notes_v2="Edited signal flips from fresh long entry to close-all management.",
        )
    )
    fixtures.extend(
        _message_edit_pair(
            channel_username="cryptoninjas_trading_ann",
            message_id=8102,
            scenario_id="edit-chain-002",
            label_v1="open-sol-short",
            label_v2="add-sol-short-edit",
            text_v1="SHORT SOLUSDT 10X",
            text_v2="ADD SHORT SOLUSDT $150",
            action_v1="open_short",
            action_v2="add_short",
            symbol_v1="SOL-USDT-SWAP",
            symbol_v2="SOL-USDT-SWAP",
            base_date=_iso_at(91),
            tags_v1=_normalize_tags("open_short", "public_web", "en", "edit"),
            tags_v2=_normalize_tags("add_short", "public_web", "en", "edit"),
            leverage_v1=10,
            size_value_v2=150.0,
        )
    )
    fixtures.extend(
        _message_edit_pair(
            channel_username="feiyangkanbi",
            message_id=8103,
            scenario_id="edit-chain-003",
            label_v1="open-doge-long",
            label_v2="reduce-doge-long-edit",
            text_v1="#DOGE BUY",
            text_v2="REDUCE LONG DOGEUSDT",
            action_v1="open_long",
            action_v2="reduce_long",
            symbol_v1="DOGE-USDT-SWAP",
            symbol_v2="DOGE-USDT-SWAP",
            base_date=_iso_at(92),
            tags_v1=_normalize_tags("open_long", "public_web", "en", "hashtag", "edit"),
            tags_v2=_normalize_tags("reduce_long", "public_web", "en", "edit"),
        )
    )
    fixtures.extend(
        _message_edit_pair(
            channel_username="fixture_public_alpha",
            message_id=8104,
            scenario_id="edit-chain-004",
            label_v1="open-eth-short",
            label_v2="reverse-eth-long-edit",
            text_v1="SELL ETHUSDT",
            text_v2="REVERSE ETHUSDT LONG",
            action_v1="open_short",
            action_v2="reverse_to_long",
            symbol_v1="ETH-USDT-SWAP",
            symbol_v2="ETH-USDT-SWAP",
            base_date=_iso_at(93),
            tags_v1=_normalize_tags("open_short", "public_web", "en", "edit"),
            tags_v2=_normalize_tags("reverse_to_long", "public_web", "en", "edit"),
        )
    )
    fixtures.extend(
        _message_edit_pair(
            channel_username="cryptoninjas_trading_ann",
            message_id=8105,
            scenario_id="edit-chain-005",
            label_v1="cancel-towns-entry",
            label_v2="open-towns-long-edit",
            text_v1="#TOWNS cancel entry limit",
            text_v2="LONG TOWNSUSDT",
            action_v1="cancel_orders",
            action_v2="open_long",
            symbol_v1="TOWNS-USDT-SWAP",
            symbol_v2="TOWNS-USDT-SWAP",
            base_date=_iso_at(94),
            tags_v1=_normalize_tags("cancel_orders", "public_web", "en", "hashtag", "edit"),
            tags_v2=_normalize_tags("open_long", "public_web", "en", "edit"),
        )
    )
    fixtures.extend(
        _message_edit_pair(
            channel_username="feiyangkanbi",
            message_id=8106,
            scenario_id="edit-chain-006",
            label_v1="ignore-sui-wait",
            label_v2="open-sui-short-edit",
            text_v1="IGNORE SUIUSDT wait",
            text_v2="SHORT SUIUSDT",
            action_v1="ignore",
            action_v2="open_short",
            symbol_v1="SUI-USDT-SWAP",
            symbol_v2="SUI-USDT-SWAP",
            base_date=_iso_at(95),
            tags_v1=_normalize_tags("ignore", "public_web", "noise", "en", "edit"),
            tags_v2=_normalize_tags("open_short", "public_web", "en", "edit"),
            size_value_v1=0.0,
        )
    )
    fixtures.extend(
        _message_edit_pair(
            channel_username="fixture_public_alpha",
            message_id=8107,
            scenario_id="edit-chain-007",
            label_v1="update-btc-sl",
            label_v2="close-btc-edit",
            text_v1="BTCUSDT STOP LOSS 82000",
            text_v2="CLOSE BTCUSDT",
            action_v1="update_protection",
            action_v2="close_all",
            symbol_v1="BTC-USDT-SWAP",
            symbol_v2="BTC-USDT-SWAP",
            base_date=_iso_at(96),
            tags_v1=_normalize_tags("update_protection", "public_web", "en", "sl_text", "edit"),
            tags_v2=_normalize_tags("close_all", "public_web", "en", "edit"),
            sl_v1={"trigger": 82000.0},
        )
    )
    fixtures.extend(
        _message_edit_pair(
            channel_username="cryptoninjas_trading_ann",
            message_id=8108,
            scenario_id="edit-chain-008",
            label_v1="open-xrp-long",
            label_v2="reverse-xrp-short-edit",
            text_v1="做多 XRPUSDT",
            text_v2="FLIP XRPUSDT TO SHORT",
            action_v1="open_long",
            action_v2="reverse_to_short",
            symbol_v1="XRP-USDT-SWAP",
            symbol_v2="XRP-USDT-SWAP",
            base_date=_iso_at(97),
            tags_v1=_normalize_tags("open_long", "public_web", "cn", "edit"),
            tags_v2=_normalize_tags("reverse_to_short", "public_web", "en", "edit"),
        )
    )
    fixtures.extend(
        _message_edit_pair(
            channel_username="feiyangkanbi",
            message_id=8109,
            scenario_id="edit-chain-009",
            label_v1="add-ada-long",
            label_v2="update-ada-protection-edit",
            text_v1="ADD LONG ADAUSDT $120",
            text_v2="ADAUSDT TAKE PROFIT 1.2 STOP LOSS 0.9",
            action_v1="add_long",
            action_v2="update_protection",
            symbol_v1="ADA-USDT-SWAP",
            symbol_v2="ADA-USDT-SWAP",
            base_date=_iso_at(98),
            tags_v1=_normalize_tags("add_long", "public_web", "en", "edit"),
            tags_v2=_normalize_tags("update_protection", "public_web", "en", "tp_text", "sl_text", "edit"),
            size_value_v1=120.0,
            tp_v2=[{"trigger": 1.2}],
            sl_v2={"trigger": 0.9},
        )
    )
    return fixtures


def _build_html_fixtures() -> list[dict[str, Any]]:
    return [
        {
            "fixture_id": "html-cryptoninjas-trading-ann-basic-001",
            "channel_username": "cryptoninjas_trading_ann",
            "html": """
<div class="tgme_widget_message" data-post="cryptoninjas_trading_ann/901">
  <div class="tgme_widget_message_text">LONG BTCUSDT<br>TP 98000</div>
  <div class="tgme_widget_message_date"><time datetime="2026-03-18T12:00:00+00:00"></time></div>
</div>
<div class="tgme_widget_message" data-post="cryptoninjas_trading_ann/902">
  <div class="tgme_widget_message_text">ADD LONG BTCUSDT<br>$250</div>
  <div class="tgme_widget_message_date"><time datetime="2026-03-18T12:05:00+00:00"></time></div>
</div>
""".strip(),
            "expected_posts": [
                {
                    "channel_username": "cryptoninjas_trading_ann",
                    "message_id": 901,
                    "date": "2026-03-18T12:00:00+00:00",
                    "text": "LONG BTCUSDT\nTP 98000",
                    "caption": "",
                },
                {
                    "channel_username": "cryptoninjas_trading_ann",
                    "message_id": 902,
                    "date": "2026-03-18T12:05:00+00:00",
                    "text": "ADD LONG BTCUSDT\n$250",
                    "caption": "",
                },
            ],
        },
        {
            "fixture_id": "html-feiyangkanbi-nested-002",
            "channel_username": "feiyangkanbi",
            "html": """
<div class="tgme_widget_message" data-post="feiyangkanbi/903">
  <div class="tgme_widget_message_text"><span>SHORT</span> <b>SOLUSDT</b></div>
  <div class="tgme_widget_message_date"><time datetime="2026-03-18T13:10:00+00:00"></time></div>
</div>
<div class="tgme_widget_message" data-post="feiyangkanbi/904">
  <div class="tgme_widget_message_text">#TOWNS <span>cancel</span> entry limit</div>
  <div class="tgme_widget_message_date"><time datetime="2026-03-18T13:11:00+00:00"></time></div>
</div>
""".strip(),
            "expected_posts": [
                {
                    "channel_username": "feiyangkanbi",
                    "message_id": 903,
                    "date": "2026-03-18T13:10:00+00:00",
                    "text": "SHORT SOLUSDT",
                    "caption": "",
                },
                {
                    "channel_username": "feiyangkanbi",
                    "message_id": 904,
                    "date": "2026-03-18T13:11:00+00:00",
                    "text": "#TOWNS cancel entry limit",
                    "caption": "",
                },
            ],
        },
        {
            "fixture_id": "html-fixture-public-alpha-mixed-003",
            "channel_username": "fixture_public_alpha",
            "html": """
<div class="tgme_widget_message" data-post="fixture_public_alpha/905">
  <div class="tgme_widget_message_text">BTCUSDT STOP LOSS 82000</div>
  <div class="tgme_widget_message_date"><time datetime="2026-03-18T14:00:00+00:00"></time></div>
</div>
<div class="tgme_widget_message" data-post="fixture_public_alpha/906">
  <div class="tgme_widget_message_text">REVERSE 做多 ADAUSDT</div>
  <div class="tgme_widget_message_date"><time datetime="2026-03-18T14:02:00+00:00"></time></div>
</div>
""".strip(),
            "expected_posts": [
                {
                    "channel_username": "fixture_public_alpha",
                    "message_id": 905,
                    "date": "2026-03-18T14:00:00+00:00",
                    "text": "BTCUSDT STOP LOSS 82000",
                    "caption": "",
                },
                {
                    "channel_username": "fixture_public_alpha",
                    "message_id": 906,
                    "date": "2026-03-18T14:02:00+00:00",
                    "text": "REVERSE 做多 ADAUSDT",
                    "caption": "",
                },
            ],
        },
    ]


def _build_scenarios() -> list[dict[str, Any]]:
    return [
        {
            "fixture_id": "scn-dedup-open-long-001",
            "scenario_id": "dedup-open-long-001",
            "coverage_category": "dedup_same_version",
            "description": "Replaying the same open_long public_web version does not create a second order.",
            "events": [
                {"fixture_id": "pw-fixture_public_alpha-5901-v1-open-long-protection", "replay_count": 2},
            ],
            "expected_chain_outcome": {
                "message_versions_seen": [1],
                "distinct_orders": 1,
                "latest_action": "open_long",
                "latest_message_status": "EXECUTED",
                "total_messages_persisted": 1,
            },
        },
        {
            "fixture_id": "scn-dedup-open-short-002",
            "scenario_id": "dedup-open-short-002",
            "coverage_category": "dedup_same_version",
            "description": "A same-version short replay stays deduplicated across three submissions.",
            "events": [
                {"fixture_id": "pw-cryptoninjas_trading_ann-5201-v1-open-short-plain", "replay_count": 3},
            ],
            "expected_chain_outcome": {
                "message_versions_seen": [1],
                "distinct_orders": 1,
                "latest_action": "open_short",
                "latest_message_status": "EXECUTED",
                "total_messages_persisted": 1,
            },
        },
        {
            "fixture_id": "scn-dedup-cancel-003",
            "scenario_id": "dedup-cancel-003",
            "coverage_category": "dedup_same_version",
            "description": "Cancel-order replays do not duplicate local order records.",
            "events": [
                {"fixture_id": "pw-feiyangkanbi-6002-v1-cancel-entry", "replay_count": 2},
            ],
            "expected_chain_outcome": {
                "message_versions_seen": [1],
                "distinct_orders": 1,
                "latest_action": "cancel_orders",
                "latest_message_status": "EXECUTED",
                "total_messages_persisted": 1,
            },
        },
        {
            "fixture_id": "scn-edit-chain-btc-close-004",
            "scenario_id": "edit-chain-btc-close-004",
            "coverage_category": "edit_chain",
            "description": "An edited signal changes from open_long to close_all and executes both versions.",
            "events": [
                {"fixture_id": "pw-fixture_public_alpha-8101-v1-open-btc-long", "replay_count": 1},
                {"fixture_id": "pw-fixture_public_alpha-8101-v2-close-btc-edit", "replay_count": 1},
            ],
            "expected_chain_outcome": {
                "message_versions_seen": [1, 2],
                "distinct_orders": 2,
                "latest_action": "close_all",
                "latest_message_status": "EXECUTED",
                "total_messages_persisted": 2,
            },
        },
        {
            "fixture_id": "scn-edit-chain-sol-add-005",
            "scenario_id": "edit-chain-sol-add-005",
            "coverage_category": "edit_chain",
            "description": "An edited short signal adds size on the second version.",
            "events": [
                {"fixture_id": "pw-cryptoninjas_trading_ann-8102-v1-open-sol-short", "replay_count": 1},
                {"fixture_id": "pw-cryptoninjas_trading_ann-8102-v2-add-sol-short-edit", "replay_count": 1},
            ],
            "expected_chain_outcome": {
                "message_versions_seen": [1, 2],
                "distinct_orders": 2,
                "latest_action": "add_short",
                "latest_message_status": "EXECUTED",
                "total_messages_persisted": 2,
            },
            "expected_open_positions": [
                {"symbol": "SOL-USDT-SWAP", "side": "short", "qty": 250.0},
            ],
        },
        {
            "fixture_id": "scn-edit-chain-ignore-open-006",
            "scenario_id": "edit-chain-ignore-open-006",
            "coverage_category": "edit_chain",
            "description": "An ignored wait note becomes an executable short on edit.",
            "events": [
                {"fixture_id": "pw-feiyangkanbi-8106-v1-ignore-sui-wait", "replay_count": 1},
                {"fixture_id": "pw-feiyangkanbi-8106-v2-open-sui-short-edit", "replay_count": 1},
            ],
            "expected_chain_outcome": {
                "message_versions_seen": [1, 2],
                "distinct_orders": 1,
                "latest_action": "open_short",
                "latest_message_status": "EXECUTED",
                "total_messages_persisted": 2,
            },
        },
        {
            "fixture_id": "scn-mixed-channel-shared-id-007",
            "scenario_id": "mixed-channel-shared-id-007",
            "coverage_category": "mixed_channel_same_message_id",
            "description": "Different channels can reuse the same numeric message id without collisions.",
            "events": [
                {"fixture_id": "pw-fixture_public_alpha-5901-v1-open-long-protection", "replay_count": 1},
                {"fixture_id": "pw-cryptoninjas_trading_ann-5901-v1-open-short-protection", "replay_count": 1},
            ],
            "expected_chain_outcome": {
                "message_versions_seen": [1],
                "distinct_orders": 2,
                "latest_action": "open_short",
                "latest_message_status": "EXECUTED",
                "total_messages_persisted": 2,
            },
        },
        {
            "fixture_id": "scn-mixed-channel-edit-id-008",
            "scenario_id": "mixed-channel-edit-id-008",
            "coverage_category": "mixed_channel_same_message_id",
            "description": "A shared numeric id across channels stays isolated even when one side later edits.",
            "events": [
                {"fixture_id": "pw-fixture_public_alpha-5901-v1-open-long-protection", "replay_count": 1},
                {"fixture_id": "pw-cryptoninjas_trading_ann-8102-v1-open-sol-short", "replay_count": 1},
                {"fixture_id": "pw-cryptoninjas_trading_ann-8102-v2-add-sol-short-edit", "replay_count": 1},
            ],
            "expected_chain_outcome": {
                "message_versions_seen": [1, 2],
                "distinct_orders": 3,
                "latest_action": "add_short",
                "latest_message_status": "EXECUTED",
                "total_messages_persisted": 3,
            },
        },
        {
            "fixture_id": "scn-reconcile-buffer-open-009",
            "scenario_id": "reconcile-buffer-open-009",
            "coverage_category": "reconcile_buffer_replay",
            "description": "Buffered reconcile replays the same version once and dedup keeps order count stable.",
            "events": [
                {"fixture_id": "pw-fixture_public_alpha-5901-v1-open-long-protection", "replay_count": 1},
            ],
            "reconcile_steps": [
                {
                    "after_event_index": 0,
                    "buffered_fixture_ids": ["pw-fixture_public_alpha-5901-v1-open-long-protection"],
                }
            ],
            "expected_chain_outcome": {
                "message_versions_seen": [1],
                "distinct_orders": 1,
                "latest_action": "open_long",
                "latest_message_status": "EXECUTED",
                "total_messages_persisted": 1,
            },
            "expected_last_reconcile": {
                "status": "ok",
                "replayed_messages": 1,
            },
        },
        {
            "fixture_id": "scn-reconcile-buffer-multi-010",
            "scenario_id": "reconcile-buffer-multi-010",
            "coverage_category": "reconcile_buffer_replay",
            "description": "Reconcile can replay multiple buffered versions without producing duplicate orders.",
            "events": [
                {"fixture_id": "pw-cryptoninjas_trading_ann-5201-v1-open-short-plain", "replay_count": 1},
                {"fixture_id": "pw-feiyangkanbi-6002-v1-cancel-entry", "replay_count": 1},
            ],
            "reconcile_steps": [
                {
                    "after_event_index": 1,
                    "buffered_fixture_ids": [
                        "pw-cryptoninjas_trading_ann-5201-v1-open-short-plain",
                        "pw-feiyangkanbi-6002-v1-cancel-entry",
                    ],
                }
            ],
            "expected_chain_outcome": {
                "message_versions_seen": [1],
                "distinct_orders": 2,
                "latest_action": "cancel_orders",
                "latest_message_status": "EXECUTED",
                "total_messages_persisted": 2,
            },
            "expected_last_reconcile": {
                "status": "ok",
                "replayed_messages": 2,
            },
        },
        {
            "fixture_id": "scn-restart-dedup-open-011",
            "scenario_id": "restart-dedup-open-011",
            "coverage_category": "restart_persisted_state",
            "description": "Restart preserves processed message state so a same-version replay remains deduplicated.",
            "events": [
                {"fixture_id": "pw-fixture_public_alpha-5901-v1-open-long-protection", "replay_count": 1},
                {"fixture_id": "pw-fixture_public_alpha-5901-v1-open-long-protection", "replay_count": 1},
            ],
            "restart_after_event_indexes": [0],
            "expected_chain_outcome": {
                "message_versions_seen": [1],
                "distinct_orders": 1,
                "latest_action": "open_long",
                "latest_message_status": "EXECUTED",
                "total_messages_persisted": 1,
            },
            "expected_open_positions": [
                {"symbol": "ADA-USDT-SWAP", "side": "long", "qty": 100.0},
            ],
        },
        {
            "fixture_id": "scn-restart-edit-close-012",
            "scenario_id": "restart-edit-close-012",
            "coverage_category": "restart_persisted_state",
            "description": "Restart restores the open position and the next edit closes it with a new execution attempt.",
            "events": [
                {"fixture_id": "pw-fixture_public_alpha-8101-v1-open-btc-long", "replay_count": 1},
                {"fixture_id": "pw-fixture_public_alpha-8101-v2-close-btc-edit", "replay_count": 1},
            ],
            "restart_after_event_indexes": [0],
            "expected_chain_outcome": {
                "message_versions_seen": [1, 2],
                "distinct_orders": 2,
                "latest_action": "close_all",
                "latest_message_status": "EXECUTED",
                "total_messages_persisted": 2,
            },
            "expected_open_positions": [],
        },
    ]


def _build_seed_corpus() -> dict[str, list[dict[str, Any]]]:
    messages = _build_action_messages() + _build_noise_messages() + _build_edit_messages()
    return {
        "messages": messages,
        "scenarios": _build_scenarios(),
        "html": _build_html_fixtures(),
    }


SEED_CORPUS = _build_seed_corpus()
SEED_MESSAGE_FIXTURES: list[dict[str, Any]] = SEED_CORPUS["messages"]
SEED_SCENARIO_FIXTURES: list[dict[str, Any]] = SEED_CORPUS["scenarios"]
SEED_HTML_FIXTURES: list[dict[str, Any]] = SEED_CORPUS["html"]
