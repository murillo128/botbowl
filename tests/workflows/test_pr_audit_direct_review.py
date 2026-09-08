"""Regression checks for direct review inside the isolated PR-audit session."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (ROOT / ".github/workflows/codex-review-ready.yml").read_text(encoding="utf-8")
AUDIT_SKILL = (ROOT / "skills/codex-pr-audit/SKILL.md").read_text(encoding="utf-8")


class DirectAuditReviewTests(unittest.TestCase):
    def test_isolated_workflow_makes_current_session_the_reviewer(self):
        self.assertIn("You are already the fresh isolated independent reviewer", WORKFLOW)
        self.assertIn("Apply codex-independent-review directly yourself in this same session", WORKFLOW)
        self.assertIn("do not spawn, delegate to, or wait for another reviewer or subagent", WORKFLOW)
        self.assertNotIn("Invoke a fresh independent reviewer in this isolated worktree", WORKFLOW)

    def test_audit_skill_uses_direct_review_only_when_isolation_is_established(self):
        self.assertIn("the current Codex session **is** the independent reviewer", AUDIT_SKILL)
        self.assertIn("do not create a subagent, nested reviewer, second Codex session", AUDIT_SKILL)
        self.assertIn("otherwise **delegated review**, using exactly one fresh isolated reviewer", AUDIT_SKILL)
        self.assertIn("Merely opening a new turn in the executor's thread", AUDIT_SKILL)


if __name__ == "__main__":
    unittest.main()
