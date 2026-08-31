"""Durable SQLite state store for the laptop1 integration service.

All state is persisted under ``runtime/state/laptop1_pipeline.db`` with
WAL journal mode, ``synchronous=FULL`` and ``foreign_keys=ON`` so that runs,
results, and recovery bookkeeping survive crashes.

This module is the single source of truth for run lifecycle state. It must
never import Phase 1 or Phase 2 internals.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from .models import (
    IntegrationRunRecord,
    Phase2Outcome,
    Phase2ResultSummary,
    RunStatus,
)

DEFAULT_DB_PATH = Path("runtime") / "state" / "laptop1_pipeline.db"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class StateStore:
    def __init__(self, db_path: Optional[str | Path] = None) -> None:
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._apply_pragmas()
        self._migrate()

    def _apply_pragmas(self) -> None:
        cur = self._conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("PRAGMA synchronous=FULL;")
        cur.execute("PRAGMA foreign_keys=ON;")
        cur.execute("PRAGMA busy_timeout=5000;")

    def _migrate(self) -> None:
        cur = self._conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                dataset_generated_at TEXT NOT NULL,
                dataset_git_sha TEXT,
                dataset_incident_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                phase2_outcome_counts TEXT NOT NULL DEFAULT '{}',
                error TEXT,
                recovery_attempts INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS phase2_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                incident_id TEXT,
                outcome TEXT NOT NULL,
                decision TEXT,
                target_service TEXT,
                severity TEXT,
                matched_incident_id TEXT,
                similarity REAL,
                reasons TEXT NOT NULL DEFAULT '[]',
                agent_instruction TEXT,
                FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
            );
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_results_run ON phase2_results(run_id);"
        )
        # Migration: add incident_id column if missing (schema drifts from older DBs).
        try:
            cur.execute("ALTER TABLE phase2_results ADD COLUMN incident_id TEXT;")
        except sqlite3.OperationalError:
            pass  # column already exists

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Cursor]:
        cur = self._conn.cursor()
        cur.execute("BEGIN;")
        try:
            yield cur
            cur.execute("COMMIT;")
        except Exception:
            cur.execute("ROLLBACK;")
            raise

    # ---- Run lifecycle -------------------------------------------------

    def create_run(self, record: IntegrationRunRecord) -> None:
        with self._tx() as cur:
            cur.execute(
                """
                INSERT OR REPLACE INTO runs (
                    run_id, dataset_generated_at, dataset_git_sha,
                    dataset_incident_count, status, started_at, finished_at,
                    phase2_outcome_counts, error, recovery_attempts, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    record.run_id,
                    record.dataset_generated_at,
                    record.dataset_git_sha,
                    record.dataset_incident_count,
                    record.status.value,
                    record.started_at,
                    record.finished_at,
                    json_dumps(record.phase2_outcome_counts),
                    record.error,
                    record.recovery_attempts,
                    record.created_at,
                ),
            )

    def update_run(self, run_id: str, **fields) -> None:
        allowed = {
            "status",
            "started_at",
            "finished_at",
            "phase2_outcome_counts",
            "error",
            "recovery_attempts",
            "dataset_generated_at",
            "dataset_git_sha",
            "dataset_incident_count",
        }
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return
        cols = []
        vals = []
        for k, v in sets.items():
            cols.append(f"{k}=?")
            if k == "status" and isinstance(v, RunStatus):
                v = v.value
            if k == "phase2_outcome_counts":
                v = json_dumps(v)
            vals.append(v)
        vals.append(run_id)
        with self._tx() as cur:
            cur.execute(f"UPDATE runs SET {', '.join(cols)} WHERE run_id=?;", vals)

    def get_run(self, run_id: str) -> Optional[IntegrationRunRecord]:
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM runs WHERE run_id=?;", (run_id,))
        row = cur.fetchone()
        return _row_to_run(row) if row else None

    def get_last_run(self) -> Optional[IntegrationRunRecord]:
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM runs ORDER BY created_at DESC, rowid DESC LIMIT 1;")
        row = cur.fetchone()
        return _row_to_run(row) if row else None

    def get_pending_runs(self) -> list[IntegrationRunRecord]:
        cur = self._conn.cursor()
        cur.execute(
            "SELECT * FROM runs WHERE status IN (?, ?) ORDER BY created_at ASC;",
            (RunStatus.PENDING.value, RunStatus.RUNNING.value),
        )
        return [_row_to_run(r) for r in cur.fetchall()]

    def count_runs_by_status(self) -> dict[str, int]:
        cur = self._conn.cursor()
        cur.execute("SELECT status, COUNT(*) AS c FROM runs GROUP BY status;")
        return {row["status"]: row["c"] for row in cur.fetchall()}

    # ---- Phase 2 results ----------------------------------------------

    def insert_result(self, summary: Phase2ResultSummary) -> None:
        with self._tx() as cur:
            cur.execute(
                """
                INSERT INTO phase2_results (
                    run_id, event_id, incident_id, outcome, decision, target_service,
                    severity, matched_incident_id, similarity, reasons,
                    agent_instruction
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    summary.run_id,
                    summary.event_id,
                    summary.incident_id,
                    summary.outcome.value if isinstance(summary.outcome, Phase2Outcome) else summary.outcome,
                    summary.decision,
                    summary.target_service,
                    summary.severity,
                    summary.matched_incident_id,
                    summary.similarity,
                    json_dumps(summary.reasons),
                    summary.agent_instruction,
                ),
            )

    def get_results_for_run(self, run_id: str) -> list[Phase2ResultSummary]:
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM phase2_results WHERE run_id=?;", (run_id,))
        out = []
        for row in cur.fetchall():
            out.append(
                Phase2ResultSummary(
                    event_id=row["event_id"],
                    run_id=row["run_id"],
                    incident_id=row["incident_id"] if "incident_id" in row.keys() else None,
                    outcome=Phase2Outcome(row["outcome"]),
                    decision=row["decision"],
                    target_service=row["target_service"],
                    severity=row["severity"],
                    matched_incident_id=row["matched_incident_id"],
                    similarity=row["similarity"],
                    reasons=_json_loads(row["reasons"]),
                    agent_instruction=row["agent_instruction"],
                )
            )
        return out

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass


def json_dumps(obj) -> str:
    import json

    return json.dumps(obj, separators=(",", ":"))


def _json_loads(s: str) -> list:
    import json

    try:
        return json.loads(s) if s else []
    except Exception:
        return []


def _row_to_run(row: sqlite3.Row) -> IntegrationRunRecord:
    return IntegrationRunRecord(
        run_id=row["run_id"],
        dataset_generated_at=row["dataset_generated_at"],
        dataset_git_sha=row["dataset_git_sha"],
        dataset_incident_count=row["dataset_incident_count"],
        status=RunStatus(row["status"]),
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        phase2_outcome_counts=_json_loads(row["phase2_outcome_counts"]) or {},
        error=row["error"],
        recovery_attempts=row["recovery_attempts"],
        created_at=row["created_at"],
    )


__all__ = ["StateStore", "DEFAULT_DB_PATH"]
