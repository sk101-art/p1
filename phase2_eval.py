"""Phase 2 evaluation suite for semantic retrieval metrics across similarity threshold candidates.

PROVISIONAL NOTICE: Evaluation metrics reported here are computed strictly on synthetic query cases.
Production quality certification remains BLOCKED until human-reviewed ground truth labels are provided.
"""

from __future__ import annotations

import hashlib
import json
import math
import tempfile
from pathlib import Path
from uuid import uuid4
from typing import Any

from phase2.config import VALIDATION_ARTIFACTS_DIR, DEFAULT_EMBED_MODEL
from phase2.memory import IncidentMemory

# Synthetic evaluation cases (positive matching and negative distraction cases)
EVAL_CASES: list[dict[str, Any]] = [
    {
        "query": "Database connection pool exhausted under high traffic load",
        "target_service": "payment-service",
        "expected_incident_id": "inc_pay_001",
        "is_positive": True,
    },
    {
        "query": "PostgreSQL connection timeout error 500 in payment gateway",
        "target_service": "payment-service",
        "expected_incident_id": "inc_pay_001",
        "is_positive": True,
    },
    {
        "query": "Redis cache node out of memory error in auth service",
        "target_service": "auth-service",
        "expected_incident_id": "inc_auth_002",
        "is_positive": True,
    },
    {
        "query": "Kafka message consumer group rebalance timeout in order queue",
        "target_service": "order-service",
        "expected_incident_id": "inc_ord_003",
        "is_positive": True,
    },
    {
        "query": "Random benign diagnostic log message without system failure",
        "target_service": "api-gateway",
        "expected_incident_id": None,
        "is_positive": False,
    },
    {
        "query": "User profile avatar image uploaded successfully",
        "target_service": "user-service",
        "expected_incident_id": None,
        "is_positive": False,
    },
    {
        "query": "Scheduled cron job backup completed in 1.2 seconds",
        "target_service": "system-cron",
        "expected_incident_id": None,
        "is_positive": False,
    },
]


def calculate_mrr(rank: int | None) -> float:
    """Reciprocal rank calculation."""
    return 1.0 / rank if rank is not None else 0.0


def calculate_ndcg(rank: int | None) -> float:
    """Normalized Discounted Cumulative Gain for single target item."""
    return 1.0 / math.log2(rank + 1) if rank is not None else 0.0


def run_evaluation(memory: Any = None, thresholds: list[float] | None = None) -> dict:
    """Evaluate retrieval metrics: calibrate threshold on calibration split, report on held-out test split."""
    if thresholds is None:
        thresholds = [0.30, 0.40, 0.50, 0.60, 0.65]

    fixture_path = Path("tests/fixtures/eval_incidents_fixture.json").resolve()
    fixture_data = json.loads(fixture_path.read_text(encoding="utf-8"))
    
    # Calculate dataset hash
    dataset_hash = hashlib.sha256(fixture_path.read_bytes()).hexdigest()

    # Deterministic calibration/test split (odd/even indices) to avoid threshold-selection overfitting
    calibration_cases: list[dict[str, Any]] = [c for i, c in enumerate(EVAL_CASES) if i % 2 == 0]
    test_cases: list[dict[str, Any]] = [c for i, c in enumerate(EVAL_CASES) if i % 2 == 1]
    split_seed = 42  # deterministic split

    report: dict[str, Any] = {
        "status": "PROVISIONAL_SYNTHETIC_ONLY",
        "certification_status": "BLOCKED_PENDING_HUMAN_LABELS",
        "model_name": DEFAULT_EMBED_MODEL,
        "dataset_hash": dataset_hash,
        "split_seed": split_seed,
        "split": {
            "calibration_cases": len(calibration_cases),
            "test_cases": len(test_cases),
            "method": "deterministic_odd_even_split",
        },
        "threshold_evaluations": {},
    }

    def evaluate_cases(mem_store: Any, cases: list[dict], thresh: float) -> dict:
        tp = fp = tn = fn = 0
        r1_hits = r3_hits = r5_hits = 0
        mrr_sum = ndcg_sum = 0.0
        pos_count = 0

        for case in cases:
            matches = mem_store.search(
                case["query"],
                target_service=case["target_service"],
                top_k=5,
                min_similarity=thresh,
            )

            expected_id = case["expected_incident_id"]
            matched_ids = [m.incident_id for m in matches]

            if case["is_positive"]:
                pos_count += 1
                # A true positive requires the expected incident to be retrieved
                if expected_id in matched_ids:
                    tp += 1
                else:
                    fn += 1

                rank = None
                if expected_id and expected_id in matched_ids:
                    rank = matched_ids.index(expected_id) + 1

                if rank == 1:
                    r1_hits += 1
                if rank and rank <= 3:
                    r3_hits += 1
                if rank and rank <= 5:
                    r5_hits += 1

                mrr_sum += calculate_mrr(rank)
                ndcg_sum += calculate_ndcg(rank)
            else:
                if matches:
                    fp += 1
                else:
                    tn += 1

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (2 * precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0
        fpr = fp / (fp + tn) if (fp + tn) > 0 else 0.0
        fnr = fn / (fn + tp) if (fn + tp) > 0 else 0.0

        return {
            "threshold": thresh,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1_score": round(f1, 4),
            "false_positive_rate": round(fpr, 4),
            "false_negative_rate": round(fnr, 4),
            "recall_at_1": round(r1_hits / pos_count, 4) if pos_count else 0.0,
            "recall_at_3": round(r3_hits / pos_count, 4) if pos_count else 0.0,
            "recall_at_5": round(r5_hits / pos_count, 4) if pos_count else 0.0,
            "mrr": round(mrr_sum / pos_count, 4) if pos_count else 0.0,
            "ndcg": round(ndcg_sum / pos_count, 4) if pos_count else 0.0,
            "tp": tp,
            "fp": fp,
            "tn": tn,
            "fn": fn,
        }

    # If memory is injected (from unit tests), evaluate directly
    if memory is not None:
        best_threshold = thresholds[0]
        best_f1 = -1.0
        for thresh in thresholds:
            metrics = evaluate_cases(memory, calibration_cases, thresh)
            report["threshold_evaluations"][f"threshold_{thresh:.2f}"] = {
                **metrics,
                "split": "calibration",
            }
            if metrics["f1_score"] > best_f1:
                best_f1 = metrics["f1_score"]
                best_threshold = thresh

        report["selected_threshold"] = best_threshold
        report["selected_threshold_calibration_f1"] = round(best_f1, 4)
        test_metrics = evaluate_cases(memory, test_cases, best_threshold)
        report["test_evaluation"] = {**test_metrics, "split": "test"}
        return report

    # Otherwise, isolate evaluation DB
    with tempfile.TemporaryDirectory() as temporary_dir:
        eval_path = Path(temporary_dir) / "eval_chroma"
        col_name = f"phase2_eval_{uuid4().hex}"

        memory_store = IncidentMemory(persist_dir=eval_path, collection_name=col_name)
        # Explicitly index committed synthetic evaluation incident fixture
        memory_store.index_dataset(fixture_data)

        # Calibrate threshold on calibration split (select by F1)
        best_threshold = thresholds[0]
        best_f1 = -1.0
        for thresh in thresholds:
            metrics = evaluate_cases(memory_store, calibration_cases, thresh)
            report["threshold_evaluations"][f"threshold_{thresh:.2f}"] = {
                **metrics,
                "split": "calibration",
            }
            if metrics["f1_score"] > best_f1:
                best_f1 = metrics["f1_score"]
                best_threshold = thresh

        report["selected_threshold"] = best_threshold
        report["selected_threshold_calibration_f1"] = round(best_f1, 4)

        # Report final metrics on held-out test split at selected threshold
        test_metrics = evaluate_cases(memory_store, test_cases, best_threshold)
        report["test_evaluation"] = {**test_metrics, "split": "test"}

        memory_store.close()

    VALIDATION_ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    out_file = VALIDATION_ARTIFACTS_DIR / "phase2_eval_report.json"
    out_file.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("Engineering verification complete; production retrieval-quality certification remains blocked pending human-reviewed labels.")
    print(f"Evaluation report written to {out_file}")
    print(f"Selected threshold candidate: {best_threshold} (calibration F1={best_f1:.4f})")
    print(f"Held-out test metrics: {json.dumps(test_metrics, indent=2)}")
    return report


if __name__ == "__main__":
    res = run_evaluation()
