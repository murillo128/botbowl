"""Regression tests for review-only sessions, worktrees, and launch guards.

Run without installing the engine: python -m unittest discover -s tests/workflows.
"""

import ast
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (ROOT / ".github/workflows/codex-review-ready.yml").read_text(encoding="utf-8")
EXECUTOR = (ROOT / ".github/workflows/codex-execute-ready.yml").read_text(encoding="utf-8")
SPEC = importlib.util.spec_from_file_location("prepare_pr_audit", ROOT / ".github/scripts/prepare_pr_audit.py")
PREPARE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREPARE)


class FakeClient:
    def __init__(self, _ws):
        self.calls = []

    def request(self, method, params):
        self.calls.append((method, params))
        if method in {"thread/read", "thread/resume", "thread/fork"}:
            raise AssertionError("Audit attempted to inherit a conversation")
        if method == "thread/start":
            return {"thread": {"id": "fresh-review-thread"}}
        if method == "turn/start":
            return {"turn": {"id": "fresh-review-turn"}}
        return {}

    def send_notification(self, _method):
        pass

    def drain_until_turn_complete(self):
        return {"status": "completed"}


class ClientIsolationTests(unittest.TestCase):
    def execute(self, directory, existing_thread=False):
        audit = directory / "pr-review-119/100-1"
        executor = directory / "issue-39"
        audit.mkdir(parents=True)
        executor.mkdir()
        for name in ("app-server-thread-id", "app-server-turn-id", "app-server-ready",
                     "app-server-completed", "app-server-error", "app-server-client.pid"):
            (executor / name).write_text("executor-owned:" + name, encoding="utf-8")
        before = {path.name: path.read_bytes() for path in executor.iterdir()}
        if existing_thread:
            (audit / "app-server-thread-id").write_text("never-resume", encoding="utf-8")
        client = FakeClient(None)
        namespace = {
            "os": os, "json": json, "traceback": type("Traceback", (), {"print_exc": staticmethod(lambda: None)}),
            "WebSocketUnix": lambda _path: None, "AppServerClient": lambda _ws: client,
            "SOCKET_PATH": "/unused/socket", "REPO_ROOT": str(directory / "coordination"),
            "WORKTREE": str(directory / "reviews/pr-119-100-1"),
            "ISSUE_NUMBER": "39", "ISSUE_TITLE": "", "RUN_ID": "100",
            "log": lambda *_args, **_kwargs: None,
            "write_atomic": lambda path, value: Path(path).write_text(value, encoding="utf-8"),
        }
        for key, name in (("THREAD_FILE", "app-server-thread-id"), ("TURN_FILE", "app-server-turn-id"),
                          ("READY_FILE", "app-server-ready"), ("COMPLETED_FILE", "app-server-completed"),
                          ("ERROR_FILE", "app-server-error")):
            namespace[key] = str(audit / name)
        source = PREPARE.build_audit_client(EXECUTOR)
        tree = ast.parse(source)
        main = ast.Module(body=[node for node in tree.body if isinstance(node, ast.Try)], type_ignores=[])
        environment = {"PR_NUMBER": "119", "REVIEW_HEAD_SHA": "a" * 40,
                       "SKILLFORGE_TASK_PROMPT": "Audit only", "GITHUB_REPOSITORY": "owner/repo",
                       "GITHUB_RUN_ATTEMPT": "1"}
        error = None
        with patch.dict(os.environ, environment):
            try:
                exec(compile(main, "audit-client", "exec"), namespace)
            except RuntimeError as exc:
                error = exc
        self.assertEqual(before, {path.name: path.read_bytes() for path in executor.iterdir()})
        return audit, client, namespace, error

    def test_fresh_thread_and_isolated_turn_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            audit, client, namespace, error = self.execute(Path(directory))
            self.assertIsNone(error)
            methods = [method for method, _ in client.calls]
            self.assertEqual(methods.count("thread/start"), 1)
            self.assertFalse(set(methods) & {"thread/read", "thread/resume", "thread/fork"})
            start = next(params for method, params in client.calls if method == "thread/start")
            self.assertEqual(start["cwd"], namespace["WORKTREE"])
            turn = next(params for method, params in client.calls if method == "turn/start")
            self.assertEqual(turn["environments"], [{"environmentId": "local", "cwd": namespace["WORKTREE"]}])
            self.assertEqual(turn["sandboxPolicy"]["writableRoots"], [namespace["WORKTREE"]])
            self.assertEqual(turn["input"], [{"type": "text", "text": "Audit only"}])
            self.assertTrue(turn["clientUserMessageId"].startswith("skillforge-review:"))
            self.assertEqual((audit / "app-server-thread-id").read_text(), "fresh-review-thread")
            self.assertTrue((audit / "app-server-completed").exists())
            name = next(params["name"] for method, params in client.calls if method == "thread/name/set")
            self.assertIn("Review PR #119", name)

    def test_existing_thread_is_not_resumed_or_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            audit, client, _, error = self.execute(Path(directory), existing_thread=True)
            self.assertIsInstance(error, RuntimeError)
            self.assertNotIn("thread/start", [method for method, _ in client.calls])
            self.assertEqual((audit / "app-server-thread-id").read_text(), "never-resume")

    def test_generated_client_compiles_and_drops_executor_entry_points(self):
        source = PREPARE.build_audit_client(EXECUTOR)
        compile(source, "generated-client", "exec")
        for forbidden in ("thread/resume", "thread/read", "thread/fork", "SKILLFORGE_ISSUE_WORKTREE"):
            self.assertNotIn(forbidden, source)
        self.assertIn("SKILLFORGE_REVIEW_WORKTREE", source)
        self.assertNotIn('"cwd": REPO_ROOT', source)
        self.assertIn('"cwd": WORKTREE', source)

    def test_incompatible_published_transport_fails_closed(self):
        for source in ("", EXECUTOR + EXECUTOR,
                       EXECUTOR.replace("    client.thread_id = thread_id", "    client.thread_id = other_id"),
                       EXECUTOR.replace("Execute GitHub issue #", "Changed executor prompt #")):
            with self.subTest(source=source[:40]), self.assertRaises(ValueError):
                PREPARE.build_audit_client(source)


class ExecutorBranchContextTests(unittest.TestCase):
    def test_executor_thread_and_turn_are_anchored_to_issue_worktree(self):
        self.assertNotIn('"cwd": REPO_ROOT', EXECUTOR)
        self.assertGreaterEqual(EXECUTOR.count('"cwd": WORKTREE'), 3)
        self.assertIn('log("thread_started", threadId=thread_id, name=display_name, cwd=WORKTREE)', EXECUTOR)
        self.assertIn('"writableRoots": [WORKTREE]', EXECUTOR)


class TargetResolutionTests(unittest.TestCase):
    def setUp(self):
        body = WORKFLOW.split("          python3 - <<'PY'\n", 1)[1].split("\n          PY\n", 1)[0]
        self.script = textwrap.dedent(body)
        self.issue = {"state": "open", "labels": [{"name": "review-ready"}]}
        self.pr = {"number": 119, "draft": False,
                   "head": {"ref": "codex/issue-39", "sha": "a" * 40, "repo": {"full_name": "owner/repo"}},
                   "base": {"sha": "b" * 40, "repo": {"full_name": "owner/repo"}}}

    def resolve(self, issue=None, pulls=None, number="39"):
        responses = iter([self.issue if issue is None else issue, [self.pr] if pulls is None else pulls])

        class Response:
            def __enter__(self):
                self.payload = next(responses)
                return self

            def read(self):
                return json.dumps(self.payload).encode()

            def __exit__(self, *_args):
                pass

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "outputs"
            environment = {"GITHUB_REPOSITORY": "owner/repo", "ISSUE_NUMBER": number,
                           "GITHUB_TOKEN": "test-only", "GITHUB_OUTPUT": str(output)}
            with patch.dict(os.environ, environment), patch("urllib.request.urlopen", return_value=Response()):
                exec(compile(self.script, "resolve-target", "exec"), {})
            return output.read_text()

    def test_pins_exact_head_and_base(self):
        result = self.resolve()
        self.assertIn("head_sha=" + "a" * 40, result)
        self.assertIn("base_sha=" + "b" * 40, result)
        self.assertIn("pr_number=119", result)

    def test_rejects_invalid_issue_number(self):
        for number in ("0", "-1", "039", "39\npr_number=1", "../39"):
            with self.subTest(number=number), self.assertRaises(SystemExit):
                self.resolve(number=number)

    def test_rejects_wrong_or_multiple_states(self):
        for states in (("in-progress",), ("review-ready", "execution-ready"), ()):
            with self.subTest(states=states), self.assertRaises(SystemExit):
                self.resolve(issue={"state": "open", "labels": [{"name": name} for name in states]})

    def test_rejects_closed_issue_or_pr_as_controlling_issue(self):
        for issue in (dict(self.issue, state="closed"), dict(self.issue, pull_request={})):
            with self.subTest(issue=issue), self.assertRaises(SystemExit):
                self.resolve(issue=issue)

    def test_rejects_missing_or_ambiguous_pr(self):
        for pulls in ([], [self.pr, self.pr]):
            with self.subTest(count=len(pulls)), self.assertRaises(SystemExit):
                self.resolve(pulls=pulls)

    def test_rejects_draft_foreign_wrong_branch_and_invalid_sha(self):
        variants = []
        pr = copy.deepcopy(self.pr)
        pr["draft"] = True
        variants.append(pr)
        for side, field, value in (("head", "repo", {"full_name": "other/repo"}),
                                   ("base", "repo", {"full_name": "other/repo"}),
                                   ("head", "ref", "codex/issue-40"),
                                   ("head", "sha", "not-a-sha")):
            pr = copy.deepcopy(self.pr)
            pr[side][field] = value
            variants.append(pr)
        for pr in variants:
            with self.subTest(pr=pr), self.assertRaises(SystemExit):
                self.resolve(pulls=[pr])


class WorkspaceIsolationTests(unittest.TestCase):
    def test_detached_checkout_preserves_dirty_executor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repository"
            repo.mkdir()

            def git(*args):
                return subprocess.check_output(["git", "-C", str(repo), *args], text=True, stderr=subprocess.DEVNULL).strip()

            git("init")
            git("config", "user.email", "test@example.invalid")
            git("config", "user.name", "Test")
            (repo / "source.txt").write_text("published\n")
            git("add", "source.txt")
            git("commit", "-m", "test: fixture")
            head = git("rev-parse", "HEAD")
            git("checkout", "-b", "codex/issue-39")
            (repo / "source.txt").write_text("uncommitted executor work\n")
            (repo / "private-evidence.txt").write_text("executor evidence\n")
            before = git("status", "--porcelain")
            script = WORKFLOW.split('          git -C "$repo_root" worktree add --detach', 1)[1].split("\n\n", 1)[0]
            script = 'git -C "$repo_root" worktree add --detach ' + script.lstrip()
            # Dedent the continuation lines; shell indentation is otherwise immaterial.
            for attempt in ("100-1", "100-2"):
                review = root / ("review-119-" + attempt)
                environment = dict(os.environ, repo_root=str(repo), worktree=str(review), REVIEW_HEAD_SHA=head)
                result = subprocess.run(["bash", "-eu", "-o", "pipefail", "-c", script], env=environment, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((review / "source.txt").read_text(), "published\n")
                self.assertFalse((review / "private-evidence.txt").exists())
                (review / "source.txt").write_text("review-local output\n")
                repeat = subprocess.run(["bash", "-eu", "-o", "pipefail", "-c", script], env=environment, capture_output=True)
                self.assertNotEqual(repeat.returncode, 0)
                self.assertEqual((review / "source.txt").read_text(), "review-local output\n")
            self.assertEqual(git("status", "--porcelain"), before)
            self.assertEqual(git("symbolic-ref", "--short", "HEAD"), "codex/issue-39")
            self.assertEqual((repo / "source.txt").read_text(), "uncommitted executor work\n")

    def test_subscriber_inherits_lock_after_launcher_exits(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = str(Path(directory) / "active.lock")
            # A child retains FD 9 after the launching shell exits, just like the
            # detached WebSocket subscriber. stdin supplies a blocking release.
            command = 'exec 8<&0; exec 9>"$1"; flock -n 9; (echo ready; cat <&8 >/dev/null) & exit'
            child = subprocess.Popen(["bash", "-c", command, "test", lock], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertEqual(child.stdout.readline().strip(), "ready")
                blocked = subprocess.run(["flock", "-n", lock, "true"], capture_output=True)
                self.assertNotEqual(blocked.returncode, 0)
            finally:
                child.communicate("release\n", timeout=5)
            available = subprocess.run(["flock", "-n", lock, "true"], capture_output=True)
            self.assertEqual(available.returncode, 0)

    def test_workflow_has_no_shared_client_or_destructive_reuse(self):
        self.assertNotIn('source_client="${state_dir}/app-server-client.py"', WORKFLOW)
        self.assertNotIn("rm -f", WORKFLOW)
        self.assertNotIn("git reset", WORKFLOW)
        self.assertIn('state_dir="${audit_root}/${attempt}"', WORKFLOW)
        self.assertIn('reviews/pr-${PR_NUMBER}-${attempt}', WORKFLOW)
        self.assertIn('show "${GITHUB_SHA}:.github/workflows/codex-execute-ready.yml"', WORKFLOW)
        self.assertIn("-u SKILLFORGE_ISSUE_WORKTREE", WORKFLOW)
        self.assertIn("-u GITHUB_TOKEN", WORKFLOW)
        self.assertIn("Recheck BOTH head and base", WORKFLOW)


if __name__ == "__main__":
    unittest.main()
