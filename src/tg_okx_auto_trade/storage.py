from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any

from .models import NormalizedMessage, TradingIntent, utc_now


SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
  id TEXT PRIMARY KEY,
  payload_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id TEXT NOT NULL,
  message_id INTEGER NOT NULL,
  version INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(chat_id, message_id, version)
);
CREATE TABLE IF NOT EXISTS ai_decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id TEXT NOT NULL,
  message_id INTEGER NOT NULL,
  version INTEGER NOT NULL,
  model TEXT NOT NULL,
  thinking TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS risk_checks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  idempotency_key TEXT NOT NULL,
  approved INTEGER NOT NULL,
  code TEXT NOT NULL,
  reason TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  idempotency_key TEXT NOT NULL UNIQUE,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL,
  action TEXT NOT NULL,
  status TEXT NOT NULL,
  mode TEXT NOT NULL,
  exchange_order_id TEXT,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS positions_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  category TEXT NOT NULL,
  message TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS system_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  level TEXT NOT NULL,
  category TEXT NOT NULL,
  message TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_sessions (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runtime_meta (
  key TEXT PRIMARY KEY,
  payload_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""


class Storage:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def upsert_channels(self, channels: list[dict[str, Any]]) -> None:
        now = utc_now()
        with self._lock, self._connect() as conn:
            channel_ids = [str(channel["id"]) for channel in channels if channel.get("id")]
            if channel_ids:
                placeholders = ", ".join("?" for _ in channel_ids)
                conn.execute(
                    f"DELETE FROM channels WHERE id NOT IN ({placeholders})",
                    channel_ids,
                )
            else:
                conn.execute("DELETE FROM channels")
            for channel in channels:
                conn.execute(
                    """
                    INSERT INTO channels(id, payload_json, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET payload_json=excluded.payload_json, updated_at=excluded.updated_at
                    """,
                    (channel["id"], json.dumps(channel, sort_keys=True), now),
                )

    def save_message(self, message: NormalizedMessage, status: str = "RECEIVED") -> bool:
        with self._lock, self._connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO messages(chat_id, message_id, version, event_type, payload_json, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message.chat_id,
                        message.message_id,
                        message.version,
                        message.event_type,
                        json.dumps(message.to_dict(), sort_keys=True),
                        status,
                        utc_now(),
                    ),
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def recent_messages(
        self,
        limit: int = 5,
        chat_id: str | None = None,
        exclude: tuple[str, int, int] | None = None,
    ) -> list[dict[str, Any]]:
        where = []
        params: list[Any] = []
        if chat_id is not None:
            where.append("chat_id=?")
            params.append(chat_id)
        if exclude is not None:
            where.append("NOT (chat_id=? AND message_id=? AND version=?)")
            params.extend(list(exclude))
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT payload_json
                FROM messages
                {clause}
                ORDER BY id DESC
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in reversed(rows)]

    def update_message_status(self, chat_id: str, message_id: int, version: int, status: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE messages SET status=? WHERE chat_id=? AND message_id=? AND version=?",
                (status, chat_id, message_id, version),
            )

    def save_ai_decision(self, message: NormalizedMessage, model: str, thinking: str, payload: dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO ai_decisions(chat_id, message_id, version, model, thinking, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.chat_id,
                    message.message_id,
                    message.version,
                    model,
                    thinking,
                    json.dumps(payload, sort_keys=True),
                    utc_now(),
                ),
            )

    def save_risk_check(self, idempotency_key: str, approved: bool, code: str, reason: str, payload: dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO risk_checks(idempotency_key, approved, code, reason, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    idempotency_key,
                    1 if approved else 0,
                    code,
                    reason,
                    json.dumps(payload, sort_keys=True),
                    utc_now(),
                ),
            )

    def order_exists(self, idempotency_key: str) -> bool:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM orders WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            return row is not None

    def save_order(self, idempotency_key: str, intent: TradingIntent, mode: str, status: str, payload: dict[str, Any], exchange_order_id: str | None = None) -> None:
        now = utc_now()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO orders(idempotency_key, symbol, side, action, status, mode, exchange_order_id, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(idempotency_key) DO UPDATE SET status=excluded.status, exchange_order_id=excluded.exchange_order_id, payload_json=excluded.payload_json, updated_at=excluded.updated_at
                """,
                (
                    idempotency_key,
                    intent.symbol,
                    intent.side,
                    intent.action,
                    status,
                    mode,
                    exchange_order_id,
                    json.dumps(payload, sort_keys=True),
                    now,
                    now,
                ),
            )

    def save_position_snapshot(self, symbol: str, payload: dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO positions_snapshots(symbol, payload_json, created_at) VALUES (?, ?, ?)",
                (symbol, json.dumps(payload, sort_keys=True), utc_now()),
            )

    def create_session(self) -> str:
        session_id = uuid.uuid4().hex
        now = utc_now()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO user_sessions(id, created_at, last_seen_at) VALUES (?, ?, ?)",
                (session_id, now, now),
            )
        return session_id

    def touch_session(self, session_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "UPDATE user_sessions SET last_seen_at=? WHERE id=?",
                (utc_now(), session_id),
            )
            return cursor.rowcount > 0

    def set_runtime_meta(self, key: str, payload: dict[str, Any]) -> None:
        now = utc_now()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runtime_meta(key, payload_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET payload_json=excluded.payload_json, updated_at=excluded.updated_at
                """,
                (key, json.dumps(payload, sort_keys=True), now),
            )

    def get_runtime_meta(self, key: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM runtime_meta WHERE key=?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["payload_json"])

    def log(self, level: str, category: str, message: str, payload: dict[str, Any] | None = None, audit: bool = False) -> None:
        table = "audit_logs" if audit else "system_logs"
        with self._lock, self._connect() as conn:
            conn.execute(
                f"INSERT INTO {table}({ 'category, message, payload_json, created_at' if audit else 'level, category, message, payload_json, created_at'}) VALUES ({'?, ?, ?, ?' if audit else '?, ?, ?, ?, ?'})",
                ((category, message, json.dumps(payload or {}, sort_keys=True), utc_now()) if audit else (level, category, message, json.dumps(payload or {}, sort_keys=True), utc_now())),
            )

    def latest_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT created_at, level, category, message, payload_json FROM system_logs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [self._decoded_row(row) for row in rows]

    def latest_messages(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT chat_id, message_id, version, event_type, status, payload_json, created_at
                FROM messages
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [self._decoded_row(row) for row in rows]

    def incomplete_messages(self, limit: int = 20) -> list[dict[str, Any]]:
        terminal_statuses = ("EXECUTED", "OBSERVED", "RISK_REJECTED", "IGNORED", "MANAGEMENT_SKIPPED")
        placeholders = ", ".join("?" for _ in terminal_statuses)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT chat_id, message_id, version, event_type, status, payload_json, created_at
                FROM messages
                WHERE status NOT IN ({placeholders})
                ORDER BY id ASC
                LIMIT ?
                """,
                (*terminal_statuses, limit),
            ).fetchall()
        return [self._decoded_row(row) for row in rows]

    def latest_ai_decisions(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT chat_id, message_id, version, model, thinking, payload_json, created_at
                FROM ai_decisions
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [self._decoded_row(row) for row in rows]

    def latest_audit_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT created_at, category, message, payload_json FROM audit_logs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [self._decoded_row(row) for row in rows]

    def latest_orders(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM orders ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [self._decoded_row(row) for row in rows]

    def latest_positions(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT p1.symbol, p1.payload_json, p1.created_at
                FROM positions_snapshots p1
                JOIN (
                  SELECT symbol, MAX(id) AS max_id FROM positions_snapshots GROUP BY symbol
                ) p2 ON p1.symbol = p2.symbol AND p1.id = p2.max_id
                ORDER BY p1.symbol
                """
            ).fetchall()
            return [self._decoded_row(row) for row in rows]

    def max_demo_order_counter(self) -> int:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT MAX(CAST(SUBSTR(exchange_order_id, 6) AS INTEGER)) AS max_counter
                FROM orders
                WHERE exchange_order_id LIKE 'demo-%'
                """
            ).fetchone()
        value = row["max_counter"] if row else None
        return int(value or 0)

    def dashboard_stats(self) -> dict[str, Any]:
        positions = self.latest_positions()
        open_positions = [
            item for item in positions
            if float(item["payload"].get("qty", 0.0)) > 0
            and item["payload"].get("side") in {"long", "short"}
        ]
        total_unrealized = sum(float(item["payload"].get("unrealized_pnl", 0.0)) for item in positions)
        total_realized = sum(float(item["payload"].get("realized_pnl", 0.0)) for item in positions)
        total_exposure = sum(abs(float(item["payload"].get("qty", 0.0))) for item in positions)
        return {
            "positions_count": len(open_positions),
            "tracked_symbols_count": len(positions),
            "total_unrealized_pnl": round(total_unrealized, 4),
            "total_realized_pnl": round(total_realized, 4),
            "total_exposure": round(total_exposure, 4),
        }

    def reset_runtime_state(self) -> None:
        tables = (
            "messages",
            "ai_decisions",
            "risk_checks",
            "orders",
            "positions_snapshots",
            "audit_logs",
            "system_logs",
            "user_sessions",
            "runtime_meta",
        )
        with self._lock, self._connect() as conn:
            for table in tables:
                conn.execute(f"DELETE FROM {table}")

    def _decoded_row(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        payload["payload"] = json.loads(payload.pop("payload_json"))
        return payload
