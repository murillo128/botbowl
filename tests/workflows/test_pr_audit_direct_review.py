"""Regression checks for the isolated PR-audit launch contract.

These tests run from the workflow's copied validation snapshot, so they intentionally
assert only launcher behavior and do not depend on repository skills being copied
into that temporary directory.
"""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (ROOT / ".github/workflows/codex-review-ready.yml").read_text(encoding="utf-8")


class DirectAuditReviewTests(unittest.TestCase):
    def test_isolated_workflow_reviews_directly_then_returns_to_controller(self):
        self.assertIn("fresh isolated audit session", WORKFLOW)
        self.assertIn("Apply codex-independent-review directly yourself in this same session", WORKFLOW)
        self.assertIn("do not spawn, delegate to, or wait for another reviewer or subagent", WORKFLOW)
        self.assertIn("During the technical review phase remain read-only", WORKFLOW)
        self.assertIn("leave the reviewer role and resume codex-pr-audit controller duties", WORKFLOW)

    def test_positive_controller_path_is_merge_complete_close(self):
        self.assertIn("automatic merge -> completed -> close sequence", WORKFLOW)
        self.assertIn("positive final-capable audit will be merged and completed by the audit controller", WORKFLOW)
        self.assertIn("Recheck BOTH head and base before recording and again before applying the verdict", WORKFLOW)

    def test_reviewer_phase_itself_has_no_mutation_authority(self):
        self.assertIn(
            "During the technical review phase remain read-only: do not inherit or resume the executor conversation, use its worktree, edit implementation, switch branches, merge, label, or close anything",
            WORKFLOW,
        )


if __name__ == "__main__":
    unittest.main()
