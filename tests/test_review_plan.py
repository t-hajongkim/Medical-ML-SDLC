import ast
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app import planning_eval as planning, research, review_eval_plan as review


ROOT = Path(__file__).resolve().parents[1]
REPO = "example/workshop"
PR = 42
ISSUE = 7
HEAD = "a" * 40
MERGE = "b" * 40
MAIN = "c" * 40
CODE = "d" * 40
PREFIX = f"research/reports/issue-{ISSUE}/"
BRANCH = f"research/review-issue-{ISSUE}"


def blob_sha(content):
    return hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()


def encoded(value):
    return review._json(value).encode("utf-8")


def comment(identifier, body, login="maintainer", kind="User"):
    return {
        "id": identifier, "body": body, "user": {"login": login, "type": kind},
        "issue_url": f"https://api.github.com/repos/{REPO}/issues/{PR}",
    }


def pull(merged=False):
    return {
        "number": PR, "state": "closed" if merged else "open",
        "title": f"연구검토 #{ISSUE}: 교육용 계획",
        "head": {"ref": BRANCH, "sha": HEAD, "repo": {"full_name": REPO}},
        "base": {"ref": "main", "repo": {"full_name": REPO}},
        "merged": merged, "merge_commit_sha": MERGE,
        "merged_by": {"login": "maintainer", "type": "User"},
        "merged_at": "2026-10-16T01:00:00Z",
    }


def event(item):
    return {
        "action": "created", "repository": {"full_name": REPO, "default_branch": "main"},
        "issue": {"number": PR, "pull_request": {}}, "comment": copy.deepcopy(item),
    }


def merged_event():
    return {"action": "closed", "repository": {"full_name": REPO, "default_branch": "main"},
            "pull_request": pull(True)}


def request_marker():
    return {"schema_version": 1, "repository": REPO, "issue_number": ISSUE,
            "issue_url": f"https://github.com/{REPO}/issues/{ISSUE}"}


class FakeGitHub:
    """In-memory Git data and paginated comments; no network/model process exists."""

    def __init__(self):
        self.pull = pull()
        self.permissions = {"maintainer": "write", "other": "maintain", "reader": "read"}
        self.comments = []
        self.refs = {BRANCH: HEAD, "main": MAIN}
        self.trees = {}
        self.blobs = {}
        self.commits = {}
        self.writes = []
        self.reads = []
        self.count = 100
        self.block_ref = False
        self.race_ref = False
        self.set_files(HEAD, {
            PREFIX + "request.json": encoded(request_marker()),
            PREFIX + "selection.json": encoded(planning.default_selection())})
        self.set_files(MAIN, {"preserved.txt": b"concurrent main change\n"})

    def set_files(self, commit, files):
        tree = {}
        for path, content in files.items():
            sha = blob_sha(content)
            self.blobs[sha] = content
            tree[path] = {"path": path, "mode": "100644", "type": "blob", "sha": sha,
                          "size": len(content)}
        tree_sha = self.new_sha()
        self.trees[tree_sha] = tree
        self.commits[commit] = tree_sha

    def new_sha(self):
        self.count += 1
        return f"{self.count:040x}"

    def read(self, *arguments):
        self.reads.append(arguments)
        path = next(argument for argument in arguments if argument.startswith("repos/"))
        path = path[len(f"repos/{REPO}/"):]
        if path == f"pulls/{PR}":
            result = copy.deepcopy(self.pull)
            result["head"]["sha"] = self.refs[BRANCH]
            return result
        if path.startswith("collaborators/"):
            login = path.split("/")[1]
            return {"permission": self.permissions.get(login, "none")}
        if path.startswith("issues/comments/"):
            identifier = int(path.rsplit("/", 1)[1])
            return copy.deepcopy(next(item for item in self.comments if item["id"] == identifier))
        if path == f"issues/{PR}/comments":
            page = int(next(item.split("=")[1] for item in arguments if item.startswith("page=")))
            return copy.deepcopy(self.comments[(page - 1) * 100:page * 100])
        if path.startswith("git/trees/"):
            sha = path.split("/")[2].split("?")[0]
            return {"truncated": False, "tree": list(self.trees[self.commits.get(sha, sha)].values())}
        if path.startswith("git/blobs/"):
            sha = path.rsplit("/", 1)[1]
            return {"encoding": "base64", "content": base64.b64encode(self.blobs[sha]).decode()}
        if path.startswith("git/ref/heads/"):
            branch = path[len("git/ref/heads/"):]
            return {"object": {"sha": self.refs[branch]}}
        if path.startswith("git/commits/"):
            sha = path.rsplit("/", 1)[1]
            return {"tree": {"sha": self.commits[sha]}}
        if path == "":
            return {"default_branch": "main"}
        raise AssertionError(f"Unexpected API read: {arguments}")

    def command(self, arguments, **kwargs):
        if arguments[:2] != ["gh", "api"]:
            raise AssertionError("Real CLI, Docker, AI or git execution is forbidden in this fixture.")
        method = arguments[arguments.index("--method") + 1]
        path = next(arg for arg in arguments if arg.startswith("repos/"))[len(f"repos/{REPO}/"):]
        payload = json.loads(kwargs["input_text"])
        self.writes.append((method, path, copy.deepcopy(payload)))
        sha = self.new_sha()
        if path == "git/blobs":
            content = base64.b64decode(payload["content"])
            sha = blob_sha(content)
            self.blobs[sha] = content
        elif path == "git/trees":
            tree = copy.deepcopy(self.trees[payload["base_tree"]])
            for entry in payload["tree"]:
                tree[entry["path"]] = {**entry, "size": len(self.blobs[entry["sha"]])}
            self.trees[sha] = tree
        elif path == "git/commits":
            self.commits[sha] = payload["tree"]
        elif path.startswith("git/refs/heads/"):
            if self.block_ref or self.race_ref:
                raise RuntimeError("protected branch or simultaneous update")
            self.refs[path[len("git/refs/heads/"):]] = payload["sha"]
        else:
            raise AssertionError(f"Unexpected API write: {path}")
        return SimpleNamespace(stdout=json.dumps({"sha": sha}), returncode=0)

    def content(self, branch, path):
        tree = self.trees[self.commits[self.refs[branch]]]
        return self.blobs[tree[path]["sha"]]

    def json(self, branch, path):
        return json.loads(self.content(branch, path))


class _ApiCase(unittest.TestCase):
    def setUp(self):
        self.api = FakeGitHub()
        self.read = patch.object(research, "gh_json", side_effect=self.api.read).start()
        self.command = patch.object(research, "command", side_effect=self.api.command).start()
        self.dispatch = patch.object(research, "dispatch_ai", return_value={"status": "requested"}).start()
        self.addCleanup(patch.stopall)


class ReviewApiTests(_ApiCase):
    def review(self, identifier=None):
        target = self.api.comments[-1] if identifier is None else next(
            item for item in self.api.comments if item["id"] == identifier)
        return review.run_event(event(target), "issue_comment", REPO, target["user"]["login"])


    def test_natural_feedback_is_queued_without_executing_or_changing_selection(self):
        self.api.comments = [comment(1, "실행해 주세요: Python 또는 SQL 명령")]
        self.assertEqual(self.review()["status"], "feedback_queued")
        ledger = self.api.json(BRANCH, PREFIX + "feedback-requests.json")
        self.assertEqual(ledger["comments"][0]["comment_id"], 1)
        self.assertNotIn("body", ledger["comments"][0])
        self.assertIsNone(self.api.json(BRANCH, PREFIX + "selection.json")["evaluation"]["primary_metric"])
        self.dispatch.assert_called_once()

    def test_natural_feedback_backlog_permissions_duplicates_and_pending_edit(self):
        self.api.comments = [
            comment(1, "외부 요청", "reader"),
            comment(2, "AI 응답", "agent", "Bot"),
            comment(3, "F1을 추가해 주세요."),
            comment(4, "AP를 주지표로 설정해 주세요.", "other")]
        result = self.review()
        self.assertEqual(result["pending_comment_ids"], [3, 4])
        before = len(self.api.writes)
        self.assertEqual(self.review()["pending_comment_ids"], [3, 4])
        self.assertEqual(len(self.api.writes), before)
        old = self.api.json(BRANCH, PREFIX + "feedback-requests.json")["comments"][1]["body_sha256"]
        self.api.comments[-1]["body"] = "AP와 threshold 0.3을 적용해 주세요."
        self.review()
        latest = self.api.json(BRANCH, PREFIX + "feedback-requests.json")["comments"][1]
        self.assertEqual(latest["superseded_hash"], old)
        self.assertNotEqual(latest["body_sha256"], old)

    def test_natural_bot_and_read_only_comments_do_not_dispatch(self):
        for login, kind in (("reader", "User"), ("agent", "Bot"), ("external", "User")):
            with self.subTest(login=login):
                self.api.comments = [comment(1, "F1을 추가해 주세요.", login, kind)]
                self.assertEqual(self.review()["status"], "deferred")
                self.assertEqual(self.api.writes, [])
        self.dispatch.assert_not_called()

    def test_already_applied_natural_feedback_edit_is_not_reapplied(self):
        self.api.comments = [comment(1, "AP를 주지표로 해 주세요.")]
        self.review()
        ledger = self.api.content(BRANCH, PREFIX + "feedback-requests.json")
        selection = self.api.content(BRANCH, PREFIX + "selection.json")
        self.api.set_files(self.api.refs[BRANCH], {
            PREFIX + "request.json": encoded(request_marker()),
            PREFIX + "selection.json": selection,
            PREFIX + "feedback-requests.json": ledger,
            PREFIX + "feedback-applied.json": encoded({
                "schema_version": 1, "repository": REPO, "pull_request": PR,
                "comment_ids": [1], "coordination_hash": "f" * 64})})
        before = len(self.api.writes)
        self.api.comments[0]["body"] = "threshold를 0.9로 바꿔 주세요."
        self.assertEqual(self.review()["status"], "unchanged")
        self.assertEqual(len(self.api.writes), before)

    def test_ordinary_comments_on_unrelated_pr_are_not_failed_research_jobs(self):
        self.api.pull["head"]["ref"] = "feature/ordinary-code"
        self.api.comments = [comment(1, "확인했습니다.")]
        self.assertEqual(self.review()["status"], "not_applicable")
        self.assertEqual(self.api.writes, [])
        self.dispatch.assert_not_called()


class FeedbackContractTests(_ApiCase):
    def send(self, body):
        item = comment(1, body)
        self.api.comments = [item]
        return review.run_event(event(item), "issue_comment", REPO, "maintainer")

    def test_free_form_block_choices_do_not_require_commands(self):
        body = "F1은 추가하고 Brier 점수는 빼고 싶어요."
        self.assertEqual(self.send(body)["status"], "feedback_queued")
        ledger = self.api.json(BRANCH, PREFIX + "feedback-requests.json")
        self.assertEqual(ledger["comments"][0]["body_sha256"],
                         hashlib.sha256(body.encode("utf-8")).hexdigest())
        self.assertEqual(self.api.json(BRANCH, PREFIX + "selection.json"),
                         planning.default_selection())
        self.assertFalse(hasattr(review, "apply_commands"))

    def test_command_looking_text_is_only_forwarded_not_directly_applied(self):
        self.assertEqual(self.send("/plan primary roc_auc")["status"], "feedback_queued")
        self.assertIsNone(self.api.json(BRANCH, PREFIX + "selection.json")["evaluation"]["primary_metric"])

    def test_merge_is_not_a_feedback_event_and_creates_no_approval_json(self):
        result = review.run_event(merged_event(), "pull_request", REPO, "maintainer")
        self.assertEqual(result["status"], "not_applicable")
        self.assertEqual(self.api.writes, [])
        self.read.assert_not_called()
        self.assertFalse(hasattr(review, "freeze_merge"))
        self.assertFalse(hasattr(review, "_snapshot"))

    def test_current_api_comment_wins_over_event_text(self):
        item = comment(1, "실제 원본 요청")
        self.api.comments = [item]
        payload = event(item)
        payload["comment"]["body"] = "이벤트에 섞인 다른 요청"
        review.run_event(payload, "issue_comment", REPO, "maintainer")
        record = self.api.json(BRANCH, PREFIX + "feedback-requests.json")["comments"][0]
        self.assertEqual(record["body_sha256"],
                         hashlib.sha256(item["body"].encode("utf-8")).hexdigest())

    def test_actor_and_repository_mismatches_are_rejected(self):
        item = comment(1, "평가 지표를 검토해 주세요.")
        self.api.comments = [item]
        with self.assertRaises(ValueError):
            review.run_event(event(item), "issue_comment", REPO, "other")
        with self.assertRaises(ValueError):
            review.run_event({**event(item), "repository": {"full_name": "other/repo"}},
                             "issue_comment", REPO, "maintainer")
        self.assertEqual(self.api.writes, [])

    def test_concurrent_branch_update_never_forces_feedback_write(self):
        self.api.race_ref = True
        with self.assertRaisesRegex(RuntimeError, "force=false"):
            self.send("F1을 추가해 주세요.")
        update = self.api.writes[-1]
        self.assertEqual(update[0], "PATCH")
        self.assertFalse(update[2]["force"])


class ApprovalReadinessTests(unittest.TestCase):
    def plans(self):
        return {
            "eda": {"ai_status": "provided", "planning_complete": True, "assessment_status": "failed"},
            "evaluation": {
                "ai_status": "provided", "planning_complete": True,
                "primary_metric": "roc_auc", "selected_ids": ["roc_auc"],
                "items": [{"id": "roc_auc", "state": "planned"}],
            },
        }

    def test_ready_is_educational_only_and_keeps_known_eda_failure(self):
        readiness = planning.approval_readiness(self.plans())
        self.assertTrue(readiness["ready"])
        self.assertEqual(readiness["approval_scope"], "educational_demo_only")
        self.assertFalse(readiness["clinical_use_approved"])

    def test_ai_primary_and_blocked_requirements_are_distinct(self):
        plans = self.plans()
        plans["eda"]["ai_status"] = "stale"
        plans["evaluation"]["primary_metric"] = None
        plans["evaluation"]["items"][0]["state"] = "blocked"
        readiness = planning.approval_readiness(plans)
        self.assertFalse(readiness["ready"])
        self.assertEqual(len(readiness["reasons"]), 3)


class IsolationAndWorkflowTests(unittest.TestCase):

    def test_remote_cli_disabled_locally(self):
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}), \
                patch.object(research, "command", side_effect=AssertionError("no CLI")):
            with self.assertRaises(SystemExit):
                review.main()

    def test_no_model_or_metric_imports(self):
        tree = ast.parse((ROOT / "app" / "review_eval_plan.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            self.assertFalse(any(name.startswith(("app.infer", "app.blocks.metrics", "sklearn",
                                                  "torch", "joblib")) for name in names))

    def test_workflow_trusted_ref_native_token_minimal_events_and_concurrency(self):
        import yaml
        workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "review-eval-plan.yml").read_text(encoding="utf-8"))
        trigger = workflow.get("on", workflow.get(True))
        self.assertEqual(set(trigger), {"issue_comment"})
        self.assertEqual(trigger["issue_comment"]["types"], ["created"])
        self.assertFalse(workflow["concurrency"]["cancel-in-progress"])
        self.assertIn("github.event.issue.number",
                      workflow["concurrency"]["group"])
        job = workflow["jobs"]["review-plan"]
        self.assertEqual(job["permissions"],
                         {"contents": "write", "pull-requests": "read", "issues": "read"})
        checkout = job["steps"][0]
        self.assertEqual(checkout["with"]["ref"], "${{ github.event.repository.default_branch }}")
        self.assertFalse(checkout["with"]["persist-credentials"])
        execution = next(step for step in job["steps"] if step.get("run") == "python -B -m app.review_eval_plan")
        self.assertEqual(execution["env"]["GH_TOKEN"], "${{ github.token }}")
        self.assertEqual(job["steps"][-1]["with"]["if-no-files-found"], "error")
        self.assertIn("research-ai-target", job["steps"][-1]["with"]["name"])
        for step in job["steps"]:
            if "run" in step:
                self.assertNotIn("${{ github.event.comment", step["run"])
                self.assertNotIn("${{ github.event.pull_request", step["run"])


if __name__ == "__main__":
    unittest.main()
