"""Regression checks for the isolated PR-audit launch contract.

These tests run from the workflow's copied validation snapshot, so they assert
launcher behavior without duplicating the audit/reviewer skills' policy text.
"""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (ROOT / ".github/workflows/codex-review-ready.yml").read_text(encoding="utf-8")


class DirectAuditReviewTests(unittest.TestCase):
    def test_prompt_only_supplies_dynamic_identity_and_branch_policy_entrypoint(self):
        expected = 'task_prompt="Audit PR #${PR_NUMBER} for issue #${ISSUE_NUMBER} according to AGENTS.md."'
        self.assertIn(expected, WORKFLOW)
        self.assertNotIn("${state_dir}/control/AGENTS.md", WORKFLOW)
        self.assertNotIn("${state_dir}/control/skills/", WORKFLOW)
        for duplicated_policy in (
            "Apply codex-independent-review directly yourself",
            "do not spawn, delegate to, or wait for another reviewer or subagent",
            "During the technical review phase remain read-only",
            "automatic merge -> completed -> close sequence",
            "Recheck BOTH head and base before recording",
        ):
            self.assertNotIn(duplicated_policy, WORKFLOW)

    def test_audited_head_owns_required_agent_policy(self):
        self.assertIn('for path in AGENTS.md skills/codex-pr-audit/SKILL.md', WORKFLOW)
        self.assertIn('[ ! -f "${worktree}/${path}" ]', WORKFLOW)
        self.assertIn("Audited PR head lacks required workflow instruction", WORKFLOW)
        self.assertNotIn('show "${GITHUB_SHA}:AGENTS.md"', WORKFLOW)
        self.assertNotIn('show "${GITHUB_SHA}:skills/codex-pr-audit/SKILL.md"', WORKFLOW)

    def test_prelaunch_guard_rechecks_current_control_plane_before_model_turn(self):
        self.assertIn("Pre-launch guard: issue is no longer solely review-ready", WORKFLOW)
        self.assertIn("Pre-launch guard: expected exactly one open PR", WORKFLOW)
        self.assertIn('current["head"]["sha"] != expected_head', WORKFLOW)
        self.assertIn('current["base"]["sha"] != expected_base', WORKFLOW)
        self.assertIn("require a fresh audit launch", WORKFLOW)

    def test_actions_token_is_available_only_to_guard_and_removed_from_codex(self):
        self.assertIn("GITHUB_TOKEN: ${{ github.token }}", WORKFLOW)
        self.assertIn("env -u GH_TOKEN -u GITHUB_TOKEN", WORKFLOW)


if __name__ == "__main__":
    unittest.main()
