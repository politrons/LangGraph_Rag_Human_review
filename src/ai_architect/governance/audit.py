from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..shared.models import EvaluationResult


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)


class AuditLedger:
    """Append-only, hash-chained audit trail with a lightweight metrics ledger."""

    def __init__(self, database_url: str, hmac_key: str) -> None:
        if not hmac_key or len(hmac_key) < 24:
            raise ValueError("AUDIT_HMAC_KEY must contain at least 24 characters")
        self._database_url = database_url
        self._is_postgres = database_url.startswith("postgresql://")
        if not self._is_postgres and not database_url.startswith("sqlite:///"):
            raise ValueError("AUDIT_DATABASE_URL must use sqlite:/// or postgresql://")
        self._path = (
            Path(database_url.removeprefix("sqlite:///")) if not self._is_postgres else None
        )
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self._hmac_key = hmac_key.encode("utf-8")
        self._lock = threading.RLock()
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        if self._is_postgres:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:  # pragma: no cover - depends on optional runtime extra
                message = "Install ai-architect-insurance[postgres] for PostgreSQL audit storage"
                raise RuntimeError(message) from exc
            connection = psycopg.connect(self._database_url, row_factory=dict_row)
        else:
            if self._path is None:  # pragma: no cover - constructor invariant
                raise RuntimeError("SQLite audit path was not initialized")
            connection = sqlite3.connect(self._path, timeout=15, isolation_level="IMMEDIATE")
            connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._lock, self._connection() as connection:
            sequence = (
                "BIGSERIAL PRIMARY KEY"
                if self._is_postgres
                else "INTEGER PRIMARY KEY AUTOINCREMENT"
            )
            statements = (
                f"""
                CREATE TABLE IF NOT EXISTS audit_events (
                    sequence {sequence}, event_id TEXT NOT NULL UNIQUE, occurred_at TEXT NOT NULL,
                    thread_id TEXT NOT NULL, case_id TEXT NOT NULL, event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL, previous_hash TEXT, event_hash TEXT NOT NULL
                )
                """,
                """
                CREATE INDEX IF NOT EXISTS audit_events_thread_idx
                ON audit_events(thread_id, sequence)
                """,
                """
                CREATE TABLE IF NOT EXISTS metric_events (
                    metric_id TEXT PRIMARY KEY, occurred_at TEXT NOT NULL, thread_id TEXT NOT NULL,
                    case_id TEXT NOT NULL, metric_name TEXT NOT NULL,
                    metric_value DOUBLE PRECISION NOT NULL
                )
                """,
                "CREATE INDEX IF NOT EXISTS metric_events_name_idx ON metric_events(metric_name)",
            )
            for statement in statements:
                connection.execute(statement)

    def _execute(self, connection: Any, statement: str, values: tuple[Any, ...] = ()) -> Any:
        placeholders = statement.replace("?", "%s") if self._is_postgres else statement
        return connection.execute(placeholders, values)

    def append(
        self,
        *,
        thread_id: str,
        case_id: str,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> str:
        if "applicant_email" in payload or "applicant_phone" in payload:
            raise ValueError("raw PII must not enter the audit ledger")

        occurred_at = datetime.now(UTC).isoformat()
        event_id = str(uuid4())
        payload_json = _canonical_json(payload)

        with self._lock, self._connection() as connection:
            previous = self._execute(
                connection,
                """
                SELECT event_hash FROM audit_events
                WHERE thread_id = ?
                ORDER BY sequence DESC LIMIT 1
                """,
                (thread_id,),
            ).fetchone()
            previous_hash = str(previous["event_hash"]) if previous else ""
            material = "|".join(
                [thread_id, case_id, event_type, occurred_at, payload_json, previous_hash]
            ).encode("utf-8")
            event_hash = hmac.new(self._hmac_key, material, hashlib.sha256).hexdigest()
            self._execute(
                connection,
                """
                INSERT INTO audit_events (
                    event_id, occurred_at, thread_id, case_id, event_type,
                    payload_json, previous_hash, event_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    occurred_at,
                    thread_id,
                    case_id,
                    event_type,
                    payload_json,
                    previous_hash or None,
                    event_hash,
                ),
            )
        return event_hash

    def record_metric(
        self,
        *,
        thread_id: str,
        case_id: str,
        metric_name: str,
        metric_value: float,
    ) -> None:
        with self._lock, self._connection() as connection:
            self._execute(
                connection,
                """
                INSERT INTO metric_events (
                    metric_id, occurred_at, thread_id, case_id, metric_name, metric_value
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    datetime.now(UTC).isoformat(),
                    thread_id,
                    case_id,
                    metric_name,
                    metric_value,
                ),
            )

    def record_evaluation(
        self,
        *,
        thread_id: str,
        case_id: str,
        result: EvaluationResult,
    ) -> None:
        self.record_metric(
            thread_id=thread_id,
            case_id=case_id,
            metric_name="groundedness",
            metric_value=result.groundedness,
        )
        self.record_metric(
            thread_id=thread_id,
            case_id=case_id,
            metric_name="policy_compliance",
            metric_value=result.policy_compliance,
        )
        self.record_metric(
            thread_id=thread_id,
            case_id=case_id,
            metric_name="evaluation_passed",
            metric_value=1.0 if result.passed else 0.0,
        )

    def metrics_summary(self) -> dict[str, dict[str, float]]:
        with self._lock, self._connection() as connection:
            rows = self._execute(
                connection,
                """
                SELECT metric_name, COUNT(*) AS samples, AVG(metric_value) AS average
                FROM metric_events
                GROUP BY metric_name
                ORDER BY metric_name
                """,
            ).fetchall()
        return {
            str(row["metric_name"]): {
                "samples": float(row["samples"]),
                "average": float(row["average"]),
            }
            for row in rows
        }

    def events_for_thread(self, thread_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connection() as connection:
            rows = self._execute(
                connection,
                """
                SELECT occurred_at, event_type, payload_json, previous_hash, event_hash
                FROM audit_events WHERE thread_id = ? ORDER BY sequence
                """,
                (thread_id,),
            ).fetchall()
        return [
            {
                "occurred_at": str(row["occurred_at"]),
                "event_type": str(row["event_type"]),
                "payload": json.loads(str(row["payload_json"])),
                "previous_hash": row["previous_hash"],
                "event_hash": str(row["event_hash"]),
            }
            for row in rows
        ]
