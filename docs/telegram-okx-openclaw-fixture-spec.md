# Telegram OKX OpenClaw Fixture Specification

Version: M0 planning baseline  
Date: 2026-03-19

## 1. Purpose

The next milestone needs a deterministic public-channel fixture corpus so parser, dedup, edit, reconcile, risk, and execution-intent behavior can be tested without live Telegram traffic.

This spec defines that corpus.

## 2. Scope

Fixtures in this spec are for the narrowed source system only:

- Telegram `public_web`
- public channel pages from `https://t.me/s/<channel>`
- normalized message and parser contract testing
- replay, reconcile, edit, and dedup behavior

Not part of this fixture spec:

- private Telegram channels
- bot token delivery mechanics
- MTProto sessions
- live trading

## 3. Minimum Corpus Size

Minimum distinct samples: `120`

Do not double-count one sample into multiple buckets when tracking the minimum. The target distribution is:

| Bucket | Minimum distinct samples | Notes |
| --- | --- | --- |
| action coverage | `66` | `6` per non-ignore action family across varied phrasing |
| noise / ignore coverage | `24` | promotional text, TP announcements, hold/wait chatter, ambiguous text |
| edit-version coverage | `18` | `9` two-version chains with same `message_id` and changed text |
| replay / reconcile / dedup coverage | `12` | duplicate same-version replay, restart/reconcile replay, mixed-channel same `message_id` |

Action families that must appear in the `66` action samples:

- `open_long`
- `open_short`
- `add_long`
- `add_short`
- `reduce_long`
- `reduce_short`
- `close_all`
- `reverse_to_long`
- `reverse_to_short`
- `cancel_orders`
- `update_protection`

Ignore/noise samples must still include an expected parser contract and expected risk result.

## 4. Planned Layout

Recommended repo-local layout for the next milestone:

```text
tests/fixtures/public_web/
  messages/
    *.json
  html/
    *.html
    *.json
  scenarios/
    *.json
  manifests/
    corpus-index.json
```

Meaning:

- `messages/`
  - one normalized public-web message fixture per file
- `html/`
  - raw page or fragment plus an adjacent expected parsed-post manifest
- `scenarios/`
  - multi-event chains for edit/replay/dedup/reconcile behavior
- `manifests/corpus-index.json`
  - machine-readable summary of counts and coverage tags

## 5. Naming Conventions

### 5.1 Message Fixture File Names

Format:

```text
pw-<channel>-<message_id>-v<version>-<label>.json
```

Examples:

- `pw-cryptoninjas-8777-v1-cancel-entry.json`
- `pw-feiyangkanbi-104-v1-ignore-promo.json`
- `pw-lbeobhpreo-9001-v2-reverse-short-edit.json`

Rules:

- `pw` prefix means `public_web`
- `<channel>` is the normalized channel username without `@`
- `<message_id>` is the source post id from Telegram page markup
- `<version>` is the repo-normalized version, not Telegram edit sequence
- `<label>` is a short semantic label for humans

### 5.2 Scenario Fixture File Names

Format:

```text
scn-<category>-<scenario_id>.json
```

Examples:

- `scn-edit-chain-btc-close-001.json`
- `scn-reconcile-replay-public-web-002.json`
- `scn-dedup-same-version-003.json`

### 5.3 Fixture IDs

Every fixture file must also contain a stable `fixture_id` field that exactly matches the file stem.

## 6. Canonical Message Fixture Schema

Each file under `tests/fixtures/public_web/messages/` should use this schema:

```json
{
  "fixture_id": "pw-cryptoninjas-8777-v1-cancel-entry",
  "scenario_id": "cancel-entry-basic-001",
  "source_type": "public_web",
  "channel_username": "cryptoninjas_trading_ann",
  "channel_id": "cryptoninjas_trading_ann",
  "message_id": 8777,
  "event_type": "new",
  "version": 1,
  "date": "2026-03-18T00:00:00+00:00",
  "edit_date": null,
  "text": "TOWNS cancel entry limit",
  "caption": "",
  "html_fragment_path": null,
  "tags": ["cancel_orders", "en", "public_web"],
  "expected_normalized_message": {
    "adapter": "public_web",
    "chat_id": "public:cryptoninjas_trading_ann",
    "event_type": "new",
    "version": 1,
    "content_text": "TOWNS cancel entry limit"
  },
  "expected_parser": {
    "provider_mode": "heuristic_or_openclaw",
    "exact": {
      "executable": true,
      "action": "cancel_orders",
      "symbol": "TOWNS-USDT-SWAP",
      "market_type": "swap",
      "side": "flat",
      "entry_type": "market",
      "size_mode": "fixed_usdt",
      "leverage": 20,
      "margin_mode": "isolated",
      "require_manual_confirmation": false
    },
    "contains": {},
    "absent": ["trailing"],
    "non_exact": ["confidence", "reason", "raw"]
  },
  "expected_risk": {
    "approved": true,
    "code": "approved"
  },
  "expected_execution_path": {
    "default_manual_inject": "simulated_demo",
    "configured_okx_demo": "real_demo_rest"
  },
  "notes": "Cancel-entry alias should normalize to cancel_orders."
}
```

## 7. Scenario Fixture Schema

Each file under `tests/fixtures/public_web/scenarios/` should represent a chain:

```json
{
  "scenario_id": "edit-chain-btc-close-001",
  "description": "same public_web post edited from open signal to close signal",
  "events": [
    {
      "fixture_id": "pw-lbeobhpreo-9001-v1-open-long",
      "replay_count": 1
    },
    {
      "fixture_id": "pw-lbeobhpreo-9001-v2-close-all-edit",
      "replay_count": 1
    }
  ],
  "expected_chain_outcome": {
    "message_versions_seen": [1, 2],
    "distinct_orders": 2,
    "latest_action": "close_all",
    "latest_message_status": "EXECUTED"
  }
}
```

Required scenario fields:

- `scenario_id`
- `description`
- `events`
- `expected_chain_outcome`

Useful optional fields:

- `reconcile_after_event_index`
- `reset_runtime_before`
- `expected_last_reconcile`
- `expected_remaining_orders`
- `expected_open_positions`

## 8. HTML Fixture Schema

For `tests/fixtures/public_web/html/`, keep raw HTML separate from expected parsed-post output:

- `<fixture_id>.html`
- `<fixture_id>.json`

The JSON companion should declare:

- `fixture_id`
- `channel_username`
- `expected_posts`

Each `expected_posts` item must include at least:

- `message_id`
- `date`
- `text`
- `caption`

## 9. Required Coverage Tags

Every message fixture must declare tags from this controlled set:

- `open_long`
- `open_short`
- `add_long`
- `add_short`
- `reduce_long`
- `reduce_short`
- `close_all`
- `reverse_to_long`
- `reverse_to_short`
- `cancel_orders`
- `update_protection`
- `ignore`
- `noise`
- `public_web`
- `edit`
- `replay`
- `dedup`
- `reconcile`
- `hashtag`
- `cn`
- `en`
- `mixed`
- `tp_text`
- `sl_text`
- `breakeven`
- `promo`
- `hold`

## 10. Expected Parser Output Contract

The fixture runner should compare parser output like this:

### 10.1 Exact Match Fields

These fields must match exactly unless the fixture explicitly overrides the rule:

- `executable`
- `action`
- `symbol`
- `market_type`
- `side`
- `entry_type`
- `size_mode`
- `leverage`
- `margin_mode`
- `require_manual_confirmation`

### 10.2 Conditionally Exact Fields

These may be exact when the fixture declares them:

- `size_value`
- `tp`
- `sl`
- `trailing`

### 10.3 Non-Exact Fields

These should be checked for presence/type, not full equality:

- `confidence`
- `reason`
- `raw`

### 10.4 Ignore Cases

For `ignore` fixtures, the runner must assert:

- `action == "ignore"`
- `executable == false`
- `side == "flat"`
- ambiguous symbol cases clear `symbol`
- `require_manual_confirmation == false`

## 11. Replay / Reconcile / Dedup Rules To Encode

The corpus must contain explicit fixtures for:

- same channel, same `message_id`, same `version`, same text
  - expected: dedup, no second order
- same channel, same `message_id`, incremented `version`, changed text
  - expected: new AI/risk/execution attempt
- different channels, same numeric `message_id`
  - expected: no collision
- replay of buffered messages during reconcile
  - expected: reconcile counts replayed messages and does not create extra orders for already-seen versions
- runtime restart with persisted local state
  - expected: positions restore and new replay uses current dedup logic

## 12. Channel Set Plan

At minimum, distribute fixtures across these repo-relevant public channels:

- `cryptoninjas_trading_ann`
- `feiyangkanbi`
- one synthetic public-web channel used only for deterministic fixtures, for example `fixture_public_alpha`

Reason:

- two real-style channels align to `config.demo.local.json`
- one synthetic channel avoids overfitting all edge cases to live channel phrasing

## 13. Acceptance Criteria For The Fixture Milestone

The fixture milestone is not complete until:

1. `120` or more distinct samples exist and match the bucket minimums in section 3.
2. Every fixture has a stable `fixture_id`, `tags`, and parser contract.
3. A machine-readable manifest proves coverage counts.
4. The fixture runner produces a per-fixture report and a summary exit code.
5. The runner can execute fully offline against the heuristic parser path.
6. The runner can optionally execute against the OpenClaw path in a credentialed environment without changing fixture definitions.
