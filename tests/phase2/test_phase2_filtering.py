import unittest

from phase2.filtering import ActionabilityPolicy
from phase2.models import Phase1Dataset
from tests.phase2_test_support import sample_dataset


def incident_from(raw):
    return Phase1Dataset.model_validate(raw).incidents[0]


class FilteringTests(unittest.TestCase):
    def setUp(self):
        self.policy = ActionabilityPolicy(threshold=0.35)

    def test_high_severity_is_actionable_with_explanation(self):
        result = self.policy.assess(incident_from(sample_dataset()))
        self.assertEqual(result.decision, "ACTIONABLE")
        self.assertIn("severity:HIGH", result.reasons)
        self.assertGreaterEqual(result.score, result.threshold)

    def test_benign_low_severity_operational_noise_is_suppressed(self):
        raw = sample_dataset(
            severity="LOW",
            priority_score=2.0,
            template="Health check passed",
            level="INFO",
        )
        raw["incidents"][0]["incident_event"]["occurrence_count"] = 1
        result = self.policy.assess(incident_from(raw))
        self.assertEqual(result.decision, "NON_ACTIONABLE")
        self.assertIn("known_benign_operational_noise", result.reasons)

    def test_unhealthy_service_escalates_low_severity_signal(self):
        raw = sample_dataset(
            severity="LOW",
            priority_score=10.0,
            template="Worker stalled",
            level="WARN",
        )
        raw["incidents"][0]["service_health_status"] = {
            "docker_status": "exited",
            "health_check": "unhealthy",
            "dependency_states": {},
        }
        result = self.policy.assess(incident_from(raw))
        self.assertEqual(result.decision, "ACTIONABLE")
        self.assertIn("service_not_running", result.reasons)

    def test_blank_template_is_quarantined_not_silently_dropped(self):
        result = self.policy.assess(incident_from(sample_dataset(template=" ")))
        self.assertEqual(result.decision, "QUARANTINED")
        self.assertTrue(result.reasons[0].startswith("missing_required_signal:"))
