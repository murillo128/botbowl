"""Regression checks for review-vs-controller separation in PR audits."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (ROOT / ".github/workflows/codex-review-ready.yml").read_text(encoding="utf-8")
AUDIT_SKILL = (ROOT / "skills/codex-pr-audit/SKILL.md").read_text(encoding="utf-8")
REVIEW_SKILL = (ROOT / "skills/codex-independent-review/SKILL.md").read_text(encoding="utf-8")


class DirectAuditReviewTests(unittest.TestCase):
    def test_isolated_workflow_reviews_directly_then_returns_to_controller(self):
        self.assertIn("fresh isolated audit session", WORKFLOW)
        self.assertIn("Apply codex-independent-review directly yourself in this same session", WORKFLOW)
        self.assertIn("do not spawn, delegate to, or wait for another reviewer or subagent", WORKFLOW)
        self.assertIn("During the technical review phase remain read-only", WORKFLOW)
        self.assertIn("leave the reviewer role and resume codex-pr-audit controller duties", WORKFLOW)
        self.assertIn("automatic merge -> completed -> close sequence", WORKFLOW)

    def test_audit_controller_owns_positive_merge_and_completion(self):
        self.assertIn("This skill is the audit **controller**", AUDIT_SKILL)
        self.assertIn("`codex-independent-review` remains the technical-review authority and remains read-only", AUDIT_SKILL)
        self.assertIn("Automatically merge the exact audited head", AUDIT_SKILL)
        self.assertIn("replace `review-ready` with `completed`, then close the issue", AUDIT_SKILL)
        self.assertIn("verify GitHub reports it merged", AUDIT_SKILL)
        self.assertIn("intentionally wakes the event-driven epic scheduler", AUDIT_SKILL)

    def test_reviewer_skill_remains_non_mutating(self):
        self.assertIn("It does not implement fixes", REVIEW_SKILL)
        self.assertIn("mutate workflow state", REVIEW_SKILL)
        self.assertIn("authorize/perform merge", REVIEW_SKILL)
        self.assertIn("remain read-only", REVIEW_SKILL)


if __name__ == "__main__":
    unittest.main()
