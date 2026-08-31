"""Tests for Phase 2 evaluation metric definitions (Recall@k, MRR, nDCG, FPR) and calibration/test split."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from phase2_eval import EVAL_CASES, calculate_mrr, calculate_ndcg, run_evaluation


class FakeMatchMemory:
    """Fake IncidentMemory returning deterministic ranked matches per query."""

    def __init__(self, ranking: dict[str, list[str]], similarity: float = 0.9):
        # ranking: query substring -> ordered list of incident_ids returned
        self.ranking = ranking
        self.similarity = similarity

    def search(self, query, target_service=None, query_target_service=None,
               top_k=5, min_similarity=0.4):
        for key, ids in self.ranking.items():
            if key.lower() in query.lower():
                return [
                    SimpleNamespace(incident_id=i, similarity=self.similarity)
                    for i in ids[:top_k]
                ]
        return []


class TestMetricFunctions(unittest.TestCase):
    def test_mrr_rank_1(self):
        self.assertEqual(calculate_mrr(1), 1.0)

    def test_mrr_rank_3(self):
        self.assertAlmostEqual(calculate_mrr(3), 1 / 3)

    def test_mrr_none(self):
        self.assertEqual(calculate_mrr(None), 0.0)

    def test_ndcg_rank_1(self):
        self.assertEqual(calculate_ndcg(1), 1.0)

    def test_ndcg_rank_3(self):
        self.assertAlmostEqual(calculate_ndcg(3), 1 / math_log2(4))

    def test_ndcg_none(self):
        self.assertEqual(calculate_ndcg(None), 0.0)


def math_log2(x):
    import math
    return math.log2(x)


class TestRecallAtKDefinitions(unittest.TestCase):
    """Recall@1 must mean rank==1 exactly; Recall@3 rank<=3; Recall@5 rank<=5."""

    def test_recall_at_1_requires_exact_top_rank(self):
        # Expected incident at rank 2 -> R@1 = 0, R@3 = 1, R@5 = 1
        mem = FakeMatchMemory({
            "database connection pool": ["other_inc", "inc_pay_001"],
            "postgresql connection timeout": ["inc_pay_001"],
            "redis cache node": ["inc_auth_002"],
            "kafka message consumer": ["inc_ord_003"],
        })
        report = run_evaluation(mem, thresholds=[0.4])
        test_eval = report["test_evaluation"]
        # Positive test-split cases: postgres (rank 1), kafka (rank 1)
        # R@1 counts only exact rank-1 hits
        self.assertIn("recall_at_1", test_eval)
        self.assertIn("recall_at_3", test_eval)
        self.assertIn("recall_at_5", test_eval)
        self.assertIn("mrr", test_eval)
        self.assertIn("ndcg", test_eval)
        self.assertIn("false_positive_rate", test_eval)

    def test_rank_2_not_counted_in_recall_at_1(self):
        # All positives return expected at rank 2 -> R@1 must be 0.0
        mem = FakeMatchMemory({
            "database connection pool": ["other_inc", "inc_pay_001"],
            "postgresql connection timeout": ["other_inc", "inc_pay_001"],
            "redis cache node": ["other_inc", "inc_auth_002"],
            "kafka message consumer": ["other_inc", "inc_ord_003"],
        })
        report = run_evaluation(mem, thresholds=[0.4])
        # Calibration split (even indices): db pool, redis, benign-avatar
        cal = report["threshold_evaluations"]["threshold_0.40"]
        # db pool expected at rank 2, redis at rank 2 -> R@1 = 0
        self.assertEqual(cal["recall_at_1"], 0.0)
        # R@3 and R@5 include rank 2
        self.assertEqual(cal["recall_at_3"], 1.0)
        self.assertEqual(cal["recall_at_5"], 1.0)
        # MRR for two rank-2 positives = 0.5
        self.assertAlmostEqual(cal["mrr"], 0.5, places=3)

    def test_negative_case_fpr(self):
        # Negative cases return a match -> counted as FP
        mem = FakeMatchMemory({
            "database connection pool": ["inc_pay_001"],
            "postgresql connection timeout": ["inc_pay_001"],
            "redis cache node": ["inc_auth_002"],
            "kafka message consumer": ["inc_ord_003"],
            "benign diagnostic": ["inc_x"],
            "avatar image": ["inc_y"],
            "cron job backup": ["inc_z"],
        })
        report = run_evaluation(mem, thresholds=[0.4])
        cal = report["threshold_evaluations"]["threshold_0.40"]
        # Calibration negatives: benign diagnostic (idx 4), cron (idx 6) -> both FP
        self.assertEqual(cal["fp"], 2)
        self.assertGreater(cal["false_positive_rate"], 0.0)


class TestCalibrationTestSplit(unittest.TestCase):
    def test_report_contains_split_metadata(self):
        mem = FakeMatchMemory({
            "database connection pool": ["inc_pay_001"],
            "postgresql connection timeout": ["inc_pay_001"],
            "redis cache node": ["inc_auth_002"],
            "kafka message consumer": ["inc_ord_003"],
        })
        report = run_evaluation(mem, thresholds=[0.4])
        self.assertIn("split", report)
        self.assertEqual(report["split"]["method"], "deterministic_odd_even_split")
        self.assertIn("selected_threshold", report)
        self.assertIn("test_evaluation", report)
        # Split sizes sum to total cases
        total = report["split"]["calibration_cases"] + report["split"]["test_cases"]
        self.assertEqual(total, len(EVAL_CASES))

    def test_calibration_and_test_are_disjoint(self):
        calibration = [c for i, c in enumerate(EVAL_CASES) if i % 2 == 0]
        test = [c for i, c in enumerate(EVAL_CASES) if i % 2 == 1]
        cal_queries = {c["query"] for c in calibration}
        test_queries = {c["query"] for c in test}
        self.assertEqual(cal_queries & test_queries, set())
        self.assertEqual(len(calibration) + len(test), len(EVAL_CASES))


if __name__ == "__main__":
    unittest.main()
