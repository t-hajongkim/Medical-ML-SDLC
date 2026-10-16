"""Synthetic events and real ZIP parsing; no GitHub, model or Docker execution."""

import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from app import recommendation_events as events
from app import research


REPO = "fixture-owner/workshop"
RUN = "38770131074"
SHA = "a" * 40
PR = 7
ISSUE = 2
ACTOR = "fixture-maintainer"


def zip_bytes(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


class EventHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve()
        checkout = self.directory / "checkout"
        checkout.mkdir()
        root = patch.object(research, "ROOT", checkout)
        root.start()
        self.addCleanup(root.stop)
        environment = {"GITHUB_RUN_ID": RUN, "GITHUB_RUN_ATTEMPT": "2",
                       "GITHUB_SHA": SHA, "RUNNER_TEMP": str(self.directory)}
        self.environment = patch.dict(os.environ, environment)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.target = {"status": "requested", "workflow": "research-plan.lock.yml",
                       "pull_request": PR}

    def test_normal_review_and_forced_review_emit_the_exact_pr_only(self):
        for outcome in (
            {"ai_dispatch": self.target, "private_note": "PRIVATE_SYNTHETIC_NOTE"},
            {"recommendation": self.target},
        ):
            with self.subTest(outcome=outcome):
                path = events.write_target(outcome, REPO)
                raw = path.read_text(encoding="utf-8")
                value = json.loads(raw)
                self.assertEqual(value["schema_version"], 2)
                self.assertEqual((value["target_kind"], value["target_number"]), ("pull_request", PR))
                self.assertEqual(path, self.directory / events.TARGET_FILE)
                self.assertEqual(value["pull_request"], PR)
                self.assertEqual(value["source_run_id"], RUN)
                self.assertEqual(value["source_run_attempt"], "2")
                self.assertEqual(value["source_head_sha"], SHA)
                self.assertTrue(value["requested"])
                self.assertNotIn("PRIVATE_SYNTHETIC_NOTE", raw)

    def test_prepared_initial_issue_emits_no_pr_or_private_inputs(self):
        target = {"status": "requested", "workflow": "research-plan.lock.yml",
                  "target_kind": "issue", "target_number": ISSUE, "issue": ISSUE}
        value = json.loads(events.write_target({
            "status": "prepared", "ai_dispatch": target, "patient_rows": ["PRIVATE"]},
            REPO).read_text(encoding="utf-8"))
        self.assertEqual(value["schema_version"], 2)
        self.assertEqual((value["target_kind"], value["target_number"], value["issue_number"]),
                         ("issue", ISSUE, ISSUE))
        self.assertNotIn("pull_request", value)
        self.assertNotIn("patient_rows", value)
        for changed in (
            {**target, "issue": ISSUE + 1},
            {**target, "pull_request": PR},
            {**target, "target_kind": "other"},
        ):
            with self.subTest(target=changed), self.assertRaises(ValueError):
                events.write_target({"ai_dispatch": changed}, REPO)
        with self.assertRaisesRegex(ValueError, "서로 다른"):
            events.write_target({"ai_dispatch": target, "recommendation": self.target}, REPO)

    def test_noop_and_provided_emit_explicit_no_request(self):
        for outcome in (
            {"status": "not_applicable"},
            {"ai_dispatch": {"status": "not_needed"}},
        ):
            value = json.loads(events.write_target(outcome, REPO).read_text(encoding="utf-8"))
            self.assertFalse(value["requested"])
            self.assertNotIn("pull_request", value)

    def test_ambiguous_prs_and_unregistered_workflows_are_rejected(self):
        second = {**self.target, "pull_request": PR + 1}
        with self.assertRaisesRegex(ValueError, "서로 다른"):
            events.write_target({"ai_dispatch": self.target, "recommendation": second}, REPO)
        with self.assertRaisesRegex(ValueError, "등록되지"):
            events.write_target({"ai_dispatch": {**self.target, "workflow": "other.yml"}}, REPO)

    def test_bad_provenance_and_checkout_destination_are_rejected(self):
        with patch.dict(os.environ, {"GITHUB_SHA": "not-a-sha"}):
            with self.assertRaises(ValueError):
                events.write_target({}, REPO)
        with patch.dict(os.environ, {"RUNNER_TEMP": str(research.ROOT)}):
            with self.assertRaisesRegex(ValueError, "체크아웃 외부"):
                events.write_target({}, REPO)

    def test_outputs_contain_exact_target_and_pr_compatibility(self):
        output = self.directory / "output.txt"
        with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}):
            events.write_outputs({"ready": True, "pull_request": PR})
        self.assertEqual(output.read_text(encoding="utf-8"),
                         "ready=true\ntarget_kind=pull_request\ntarget_number=7\npull_request=7\n")

    def test_issue_outputs_include_authorized_actor_but_never_pr(self):
        output = self.directory / "issue-output.txt"
        with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}):
            events.write_outputs({"ready": True, "target_kind": "issue", "target_number": ISSUE,
                                  "issue_number": ISSUE, "request_actor": ACTOR})
        self.assertEqual(output.read_text(encoding="utf-8"),
                         f"ready=true\ntarget_kind=issue\ntarget_number=2\nissue_number=2\nrequest_actor={ACTOR}\n")


class ResolutionTests(unittest.TestCase):
    def setUp(self):
        self.source = {
            "id": int(RUN), "run_attempt": 2, "head_sha": SHA, "head_branch": "main",
            "repository": {"full_name": REPO}, "head_repository": {"full_name": REPO},
            "path": ".github/workflows/prepare-eda-pr.yml", "event": "issues",
            "status": "completed", "conclusion": "success",
            "actor": {"login": ACTOR, "type": "User"},
        }
        self.event = {"repository": {"full_name": REPO, "default_branch": "main"},
                      "workflow_run": {"id": int(RUN), "run_attempt": 2}}
        self.payload = {
            "schema_version": 2, "repository": REPO, "source_run_id": RUN,
            "source_run_attempt": "2", "source_head_sha": SHA,
            "requested": True, "target_kind": "pull_request", "target_number": PR,
            "pull_request": PR,
        }
        form = research.form_contract()
        self.issue = {
            "number": ISSUE, "state": "open", "title": "합성 연구",
            "user": {"login": ACTOR},
            "body": "\n\n".join(
                f"### {label}\n\n" + (f"- [x] {form['consent']}" if identifier == "public_data_confirmation"
                                      else "Synthetic public research")
                for label, identifier in form["fields"].items()),
        }
        self.permission = "write"
        self.artifacts = [{"id": 19, "name": f"research-ai-target-{RUN}-2", "expired": False}]
        self.pull = {
            "number": PR, "state": "open", "title": "연구검토 #2: 합성 검증",
            "head": {"ref": "research/review-issue-2", "repo": {"full_name": REPO}},
            "base": {"ref": "main"},
        }
        self.api = patch.object(research, "gh_json", side_effect=self.read).start()
        self.process = patch("subprocess.run", side_effect=self.download).start()
        self.addCleanup(patch.stopall)

    def read(self, path, *arguments):
        prefix = f"repos/{REPO}"
        if path == f"{prefix}/actions/runs/{RUN}":
            return copy.deepcopy(self.source)
        if path.startswith(f"{prefix}/actions/runs/{RUN}/artifacts?"):
            self.assertEqual(arguments, ("--paginate", "--slurp"))
            return [{"artifacts": copy.deepcopy(self.artifacts)}]
        if path == f"{prefix}/pulls/{PR}":
            return copy.deepcopy(self.pull)
        if path == f"{prefix}/issues/{ISSUE}":
            return copy.deepcopy(self.issue)
        if path == f"{prefix}/collaborators/{ACTOR}/permission":
            return {"permission": self.permission}
        if path == prefix:
            return {"default_branch": "main"}
        raise AssertionError(f"Unexpected API lookup: {path}")

    def download(self, arguments, **kwargs):
        self.assertEqual(arguments, ["gh", "api", f"repos/{REPO}/actions/artifacts/19/zip"])
        data = zip_bytes({events.TARGET_FILE: json.dumps(self.payload)})
        return subprocess.CompletedProcess(arguments, 0, stdout=data, stderr=b"")

    def resolve(self):
        return events.resolve_target(self.event, "workflow_run", REPO)

    def test_trusted_success_resolves_exact_pr_from_real_zip(self):
        self.assertEqual(self.resolve(), {
            "ready": True, "target_kind": "pull_request", "target_number": PR,
            "pull_request": PR, "trigger": "workflow_run"})
        self.process.assert_called_once()
        self.assertFalse(any("/pulls?" in call.args[0] for call in self.api.call_args_list))

    def initial(self):
        self.payload.update(target_kind="issue", target_number=ISSUE, issue_number=ISSUE)
        self.payload.pop("pull_request", None)

    def test_initial_issue_is_authorized_and_resolved_without_a_pr_lookup(self):
        self.initial()
        self.assertEqual(self.resolve(), {
            "ready": True, "target_kind": "issue", "target_number": ISSUE,
            "issue_number": ISSUE, "request_actor": ACTOR, "trigger": "workflow_run"})
        self.assertFalse(any("/pulls" in call.args[0] for call in self.api.call_args_list))
        self.assertTrue(any("/permission" in call.args[0] for call in self.api.call_args_list))

    def test_initial_rejects_bot_or_missing_or_read_only_source_actor(self):
        self.initial()
        for actor in ({}, {"login": ACTOR, "type": "Bot"}, {"login": "bot[bot]", "type": "User"},
                      {"login": ACTOR + "\nspoof", "type": "User"}):
            with self.subTest(actor=actor), self.assertRaises(ValueError):
                self.source["actor"] = actor
                self.resolve()
        self.source["actor"] = {"login": ACTOR, "type": "User"}
        for permission in ("read", "triage", "none"):
            with self.subTest(permission=permission), self.assertRaises(ValueError):
                self.permission = permission
                self.resolve()

    def test_initial_rejects_wrong_closed_unregistered_or_spoofed_issue(self):
        self.initial()
        original = copy.deepcopy(self.issue)
        for update in (
            {"number": ISSUE + 1}, {"state": "closed"}, {"pull_request": {}},
            {"body": "not a registered research form"}, {"user": {"login": "outsider"}},
        ):
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.issue = {**original, **update}
                self.resolve()
        self.issue = original
        self.source.update(path=".github/workflows/review-eval-plan.yml", event="issue_comment")
        with self.assertRaises(ValueError):
            self.resolve()

    def test_manual_intake_source_can_target_an_existing_open_issue(self):
        self.initial()
        self.source["event"] = "workflow_dispatch"
        self.issue["user"]["login"] = "public-contributor"
        self.assertEqual(self.resolve()["issue_number"], ISSUE)

    def test_version_one_exact_pr_handoffs_remain_supported(self):
        self.payload["schema_version"] = 1
        self.payload.pop("target_kind")
        self.payload.pop("target_number")
        self.assertEqual(self.resolve()["pull_request"], PR)

    def test_manual_human_path_needs_no_source_artifact(self):
        result = events.resolve_target(self.event, "workflow_dispatch", REPO, str(PR))
        self.assertEqual(result["pull_request"], PR)
        self.process.assert_not_called()

    def test_comment_source_is_supported_and_failed_source_does_not_run_ai(self):
        self.source.update(path=".github/workflows/review-eval-plan.yml", event="issue_comment")
        self.assertTrue(self.resolve()["ready"])
        self.source["conclusion"] = "failure"
        self.assertFalse(self.resolve()["ready"])

    def test_removed_merge_source_is_rejected(self):
        self.source.update(path=".github/workflows/review-eval-plan.yml", event="pull_request",
                           head_branch="research/review-issue-2")
        with self.assertRaises(ValueError):
            self.resolve()
        self.process.assert_not_called()

    def test_untrusted_workflow_branch_repo_and_event_are_rejected(self):
        original = copy.deepcopy(self.source)
        for update in (
            {"path": ".github/workflows/unrelated.yml"},
            {"head_branch": "untrusted-feature"},
            {"event": "push"},
            {"head_repository": {"full_name": "outsider/fork"}},
            {"head_repository": None},
            {"repository": {"full_name": "outsider/other"}},
        ):
            with self.subTest(update=update):
                self.source = {**original, **update}
                with self.assertRaises(ValueError):
                    self.resolve()
        self.process.assert_not_called()

    def test_no_request_skips_without_touching_a_pr(self):
        self.payload.update(requested=False)
        self.payload.pop("pull_request")
        self.payload.pop("target_kind")
        self.payload.pop("target_number")
        self.assertFalse(self.resolve()["ready"])
        self.assertFalse(any("/pulls/" in call.args[0] for call in self.api.call_args_list))

    def test_missing_expired_duplicate_artifacts_and_changed_attempt_fail(self):
        original = copy.deepcopy(self.artifacts)
        for artifacts in ([], [{**original[0], "expired": True}], original * 2):
            with self.subTest(artifacts=artifacts):
                self.artifacts = artifacts
                with self.assertRaisesRegex(ValueError, "artifact"):
                    self.resolve()
        self.artifacts = original
        self.source["run_attempt"] = 3
        with self.assertRaisesRegex(ValueError, "회차"):
            self.resolve()

    def test_marker_tampering_unknown_fields_and_wrong_pr_are_rejected(self):
        original = copy.deepcopy(self.payload)
        for update in (
            {"repository": "other/repo"}, {"source_run_id": "10"},
            {"source_run_attempt": "1"}, {"source_head_sha": "b" * 40},
            {"schema_version": True}, {"requested": "true"}, {"private_rows": []},
            {"target_kind": "other"}, {"target_number": PR + 1}, {"pull_request": True},
        ):
            with self.subTest(update=update):
                self.payload = {**original, **update}
                with self.assertRaises(ValueError):
                    self.resolve()
        self.payload = original
        self.pull["head"]["repo"]["full_name"] = "outsider/fork"
        with self.assertRaisesRegex(ValueError, "same-repository"):
            self.resolve()

    def test_spoofed_event_sha_and_api_run_id_are_rejected(self):
        self.event["workflow_run"]["head_sha"] = "b" * 40
        with self.assertRaises(ValueError):
            self.resolve()
        self.event["workflow_run"].pop("head_sha")
        self.source["id"] = int(RUN) + 1
        with self.assertRaises(ValueError):
            self.resolve()
        self.process.assert_not_called()

    def test_download_failure_invalid_zip_and_unexpected_files_fail_closed(self):
        for data in (
            b"not a zip",
            zip_bytes({"../research-ai-target.json": "{}"}),
            zip_bytes({events.TARGET_FILE: "{}", "patient.csv": "synthetic"}),
            zip_bytes({events.TARGET_FILE: "x" * 65537}),
            zip_bytes({events.TARGET_FILE: '{"requested":true,"requested":false}'}),
        ):
            with self.subTest(size=len(data)):
                self.process.side_effect = None
                self.process.return_value = subprocess.CompletedProcess([], 0, stdout=data)
                with self.assertRaises(ValueError):
                    self.resolve()
        self.process.return_value = subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"secret")
        with self.assertRaises(RuntimeError) as error:
            self.resolve()
        self.assertNotIn("secret", str(error.exception))


class TrustedImportTests(unittest.TestCase):
    def test_publication_validation_does_not_execute_report_tree_python(self):
        import yaml
        text = (research.ROOT / ".github" / "workflows" / "research-plan.md").read_text(encoding="utf-8")
        config = yaml.load(text.split("---", 2)[1], Loader=yaml.BaseLoader)
        guard = config["safe-outputs"]["steps"][-1]
        self.assertEqual(guard["env"]["GH_AW_ACTIONS_DIR"], "${{ runner.temp }}/gh-aw/actions")
        self.assertIn("sanitize_content.cjs", guard["with"]["script"])
        self.assertNotIn("app.research", guard["with"]["script"])
        self.assertNotIn("python", guard["with"]["script"])
        tools = (research.ROOT / "app" / "research_tools.py").read_text(encoding="utf-8")
        self.assertNotIn("def post_check(", tools)

    def test_module_entrypoint_keeps_one_trusted_research_identity(self):
        with tempfile.TemporaryDirectory(prefix="trusted-entrypoint-") as folder:
            root = Path(folder)
            shutil.copytree(research.ROOT / "app", root / "app",
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            script = r'''
import importlib, runpy, sys
sys.argv = ["research", "--help"]
try:
    runpy.run_module("app.research", run_name="__main__", alter_sys=True)
except SystemExit as stop:
    assert stop.code == 0
module = importlib.import_module("app.research")
assert module.__name__ == "__main__"
assert module.run_workflow.__globals__ is module.__dict__
'''
            result = subprocess.run([sys.executable, "-S", "-B", "-c", script],
                                    cwd=root, env={**os.environ, "PYTHONPATH": str(root)},
                                    capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)


class SourceWorkflowTests(unittest.TestCase):
    def test_issue_intake_is_read_only_and_feedback_keeps_runtime_authorization(self):
        import yaml
        workflows = Path(__file__).resolve().parents[1] / ".github" / "workflows"
        prepare = (workflows / "prepare-eda-pr.yml").read_text(encoding="utf-8")
        feedback = (workflows / "review-eval-plan.yml").read_text(encoding="utf-8")
        config = yaml.load(prepare, Loader=yaml.BaseLoader)
        self.assertEqual(config["permissions"], {
            "contents": "read", "pull-requests": "read", "issues": "read"})
        self.assertEqual(config["on"]["issues"]["types"], ["opened"])
        self.assertIn("python -B -m app.research workflow", prepare)
        self.assertNotIn("docker", prepare.lower())
        self.assertNotIn("ghcr", prepare.lower())
        review = yaml.load(feedback, Loader=yaml.BaseLoader)
        self.assertEqual(review["on"], {"issue_comment": {"types": ["created"]}})
        self.assertEqual(review["jobs"]["review-plan"]["permissions"]["contents"], "write")
        self.assertIn("python -B -m app.review_eval_plan", feedback)
        for text in (prepare, feedback):
            self.assertNotIn("unittest", text)
            self.assertNotIn("approval.json", text)
            self.assertNotIn("gh workflow run", text)
            self.assertNotIn("repository_dispatch", text)
            self.assertNotIn("PAT", text)
            self.assertIn("research-ai-target-${{ github.run_id }}-${{ github.run_attempt }}", text)
            self.assertIn("if-no-files-found: error", text)


if __name__ == "__main__":
    unittest.main()
