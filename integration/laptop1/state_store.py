"""Durable, migration-safe SQLite state for Laptop 1 orchestration."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from .models import IntegrationRunRecord, Phase2Outcome, Phase2ResultSummary, RunStatus

DEFAULT_DB_PATH = Path("runtime") / "state" / "laptop1_pipeline.db"


class StateStore:
    def __init__(self, db_path: Optional[str | Path] = None) -> None:
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), isolation_level=None,
                                     check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        for pragma in ("journal_mode=WAL", "synchronous=FULL", "foreign_keys=ON",
                       "busy_timeout=5000"):
            self._conn.execute(f"PRAGMA {pragma}")
        self._migrate()

    def _migrate(self) -> None:
        self._conn.execute("""CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY, dataset_generated_at TEXT NOT NULL,
            dataset_git_sha TEXT, dataset_incident_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL, started_at TEXT, finished_at TEXT,
            phase2_outcome_counts TEXT NOT NULL DEFAULT '{}', error TEXT,
            recovery_attempts INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)""")
        self._conn.execute("""CREATE TABLE IF NOT EXISTS phase2_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
            event_id TEXT NOT NULL, incident_id TEXT, outcome TEXT NOT NULL,
            decision TEXT, target_service TEXT, severity TEXT,
            matched_incident_id TEXT, similarity REAL,
            reasons TEXT NOT NULL DEFAULT '[]', agent_instruction TEXT,
            FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE)""")
        self._add_missing_columns("runs", {
            "phase1_artifact_path": "TEXT", "phase1_sha256": "TEXT",
            "phase2_output_path": "TEXT", "phase2_sha256": "TEXT",
            "phase3_manifest_path": "TEXT",
            "phase3_payload_count": "INTEGER NOT NULL DEFAULT 0",
        })
        self._add_missing_columns("phase2_results", {"incident_id": "TEXT"})
        self._conn.execute("""DELETE FROM phase2_results WHERE id NOT IN (
            SELECT MIN(id) FROM phase2_results GROUP BY run_id, event_id)""")
        self._conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_results_run_event ON phase2_results(run_id,event_id)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_results_run ON phase2_results(run_id)")
        self._conn.execute(
            "UPDATE runs SET status=? WHERE status=? AND phase2_output_path IS NULL",
            (RunStatus.ARTIFACT_BACKFILL_REQUIRED.value, RunStatus.SUCCEEDED.value),
        )

    def _add_missing_columns(self, table: str, columns: dict[str, str]) -> None:
        existing = {row[1] for row in self._conn.execute(f"PRAGMA table_info({table})")}
        for name, sql_type in columns.items():
            if name not in existing:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("BEGIN")
            try:
                yield cursor
                cursor.execute("COMMIT")
            except Exception:
                cursor.execute("ROLLBACK")
                raise

    def create_run(self, record: IntegrationRunRecord) -> None:
        with self._tx() as cursor:
            cursor.execute("""INSERT OR IGNORE INTO runs (
                run_id,dataset_generated_at,dataset_git_sha,dataset_incident_count,
                status,started_at,finished_at,phase2_outcome_counts,
                phase1_artifact_path,phase1_sha256,phase2_output_path,phase2_sha256,
                phase3_manifest_path,phase3_payload_count,error,recovery_attempts,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                record.run_id, record.dataset_generated_at, record.dataset_git_sha,
                record.dataset_incident_count, record.status.value, record.started_at,
                record.finished_at, json_dumps(record.phase2_outcome_counts),
                record.phase1_artifact_path, record.phase1_sha256,
                record.phase2_output_path, record.phase2_sha256,
                record.phase3_manifest_path, record.phase3_payload_count,
                record.error, record.recovery_attempts, record.created_at))

    def update_run(self, run_id: str, **fields) -> None:
        allowed = set(IntegrationRunRecord.model_fields) - {"run_id", "created_at"}
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        columns: list[str] = []
        values: list[object] = []
        for key, value in updates.items():
            columns.append(f"{key}=?")
            if isinstance(value, RunStatus):
                value = value.value
            if key == "phase2_outcome_counts":
                value = json_dumps(value)
            values.append(value)
        values.append(run_id)
        with self._tx() as cursor:
            cursor.execute(f"UPDATE runs SET {', '.join(columns)} WHERE run_id=?", values)

    def get_run(self, run_id: str) -> Optional[IntegrationRunRecord]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return _row_to_run(row) if row else None

    def get_last_run(self) -> Optional[IntegrationRunRecord]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM runs ORDER BY created_at DESC,rowid DESC LIMIT 1").fetchone()
        return _row_to_run(row) if row else None

    def get_pending_runs(self) -> list[IntegrationRunRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM runs WHERE status IN (?,?) ORDER BY created_at",
                (RunStatus.PENDING.value, RunStatus.RUNNING.value)).fetchall()
        return [_row_to_run(row) for row in rows]

    def count_runs_by_status(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute("SELECT status,COUNT(*) AS c FROM runs GROUP BY status").fetchall()
        return {row["status"]: row["c"] for row in rows}

    def insert_result(self, summary: Phase2ResultSummary) -> None:
        with self._tx() as cursor:
            cursor.execute("""INSERT INTO phase2_results (
                run_id,event_id,incident_id,outcome,decision,target_service,severity,
                matched_incident_id,similarity,reasons,agent_instruction
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(run_id,event_id) DO UPDATE SET
                incident_id=excluded.incident_id,outcome=excluded.outcome,
                decision=excluded.decision,target_service=excluded.target_service,
                severity=excluded.severity,matched_incident_id=excluded.matched_incident_id,
                similarity=excluded.similarity,reasons=excluded.reasons,
                agent_instruction=excluded.agent_instruction""", (
                summary.run_id, summary.event_id, summary.incident_id,
                summary.outcome.value, summary.decision, summary.target_service,
                summary.severity, summary.matched_incident_id, summary.similarity,
                json_dumps(summary.reasons), summary.agent_instruction))

    def get_results_for_run(self, run_id: str) -> list[Phase2ResultSummary]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM phase2_results WHERE run_id=? ORDER BY id", (run_id,)).fetchall()
        return [Phase2ResultSummary(
            event_id=row["event_id"], run_id=row["run_id"],
            incident_id=row["incident_id"], outcome=Phase2Outcome(row["outcome"]),
            decision=row["decision"], target_service=row["target_service"],
            severity=row["severity"], matched_incident_id=row["matched_incident_id"],
            similarity=row["similarity"], reasons=_json_loads(row["reasons"], []),
            agent_instruction=row["agent_instruction"]) for row in rows]

    def ping(self) -> bool:
        try:
            with self._lock:
                return self._conn.execute("SELECT 1").fetchone()[0] == 1
        except sqlite3.Error:
            return False

    def close(self) -> None:
        try:
            with self._lock:
                self._conn.close()
        except Exception:
            pass


def json_dumps(value: object) -> str:
    return json.dumps(value, separators=(",", ":"))


def _json_loads(value: Optional[str], default: object) -> object:
    try:
        return json.loads(value) if value else default
    except (TypeError, json.JSONDecodeError):
        return default


def _row_to_run(row: sqlite3.Row) -> IntegrationRunRecord:
    keys = set(row.keys())
    optional = {
        "phase1_artifact_path": None, "phase1_sha256": None,
        "phase2_output_path": None, "phase2_sha256": None,
        "phase3_manifest_path": None, "phase3_payload_count": 0,
    }
    return IntegrationRunRecord(
        run_id=row["run_id"], dataset_generated_at=row["dataset_generated_at"],
        dataset_git_sha=row["dataset_git_sha"],
        dataset_incident_count=row["dataset_incident_count"],
        status=RunStatus(row["status"]), started_at=row["started_at"],
        finished_at=row["finished_at"],
        phase2_outcome_counts=_json_loads(row["phase2_outcome_counts"], {}),
        error=row["error"], recovery_attempts=row["recovery_attempts"],
        created_at=row["created_at"],
        **{name: row[name] if name in keys else default
           for name, default in optional.items()})


__all__ = ["StateStore", "DEFAULT_DB_PATH"]
