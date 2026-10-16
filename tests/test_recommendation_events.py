"""Synthetic events and real ZIP parsing; no GitHub, model or Docker execution."""

import copy
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
        environment = {"GITHUB_RUN_ID": RUN, "GITHUB_RUN_ATTEMPT": "2",
                       "GITHUB_SHA": SHA, "RUNNER_TEMP": str(self.directory)}
        self.environment = patch.dict(os.environ, environment)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.target = {"status": "requested", "workflow": "recommend-blocks.lock.yml",
                       "pull_request": PR}

    def test_normal_review_and_forced_review_emit_the_exact_pr_only(self):
        for outcome in (
            {"ai_dispatch": self.target, "private_note": "PRIVATE_SYNTHETIC_NOTE"},
            {"refresh": {"ai_dispatch": self.target}},
            {"recommendation": self.target},
        ):
            with self.subTest(outcome=outcome):
                path = events.write_target(outcome, REPO)
                raw = path.read_text(encoding="utf-8")
                value = json.loads(raw)
                self.assertEqual(path, self.directory / events.TARGET_FILE)
                self.assertEqual(value["pull_request"], PR)
                self.assertEqual(value["source_run_id"], RUN)
                self.assertEqual(value["source_run_attempt"], "2")
                self.assertEqual(value["source_head_sha"], SHA)
                self.assertTrue(value["requested"])
                self.assertNotIn("PRIVATE_SYNTHETIC_NOTE", raw)

    def test_noop_provided_and_freeze_emit_explicit_no_request(self):
        for outcome in (
            {"status": "not_applicable"},
            {"ai_dispatch": {"status": "not_needed"}},
            {"status": "frozen", "approved_plan": "research/approved/fixture.json"},
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

    def test_outputs_contain_only_boolean_and_numeric_pr(self):
        output = self.directory / "output.txt"
        with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}):
            events.write_outputs({"ready": True, "pull_request": PR})
        self.assertEqual(output.read_text(encoding="utf-8"), "ready=true\npull_request=7\n")


class ResolutionTests(unittest.TestCase):
    def setUp(self):
        self.source = {
            "id": int(RUN), "run_attempt": 2, "head_sha": SHA, "head_branch": "main",
            "repository": {"full_name": REPO}, "head_repository": {"full_name": REPO},
            "path": ".github/workflows/prepare-eda-pr.yml", "event": "issues",
            "status": "completed", "conclusion": "success",
        }
        self.event = {"repository": {"full_name": REPO, "default_branch": "main"},
                      "workflow_run": {"id": int(RUN), "run_attempt": 2}}
        self.payload = {
            "schema_version": 1, "repository": REPO, "source_run_id": RUN,
            "source_run_attempt": "2", "source_head_sha": SHA,
            "requested": True, "pull_request": PR,
        }
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
        self.assertEqual(self.resolve(), {"ready": True, "pull_request": PR, "trigger": "workflow_run"})
        self.process.assert_called_once()
        self.assertFalse(any("/pulls?" in call.args[0] for call in self.api.call_args_list))

    def test_manual_human_path_needs_no_source_artifact(self):
        result = events.resolve_target(self.event, "workflow_dispatch", REPO, str(PR))
        self.assertEqual(result["pull_request"], PR)
        self.process.assert_not_called()

    def test_comment_source_is_supported_and_failed_source_does_not_run_ai(self):
        self.source.update(path=".github/workflows/review-plan.yml", event="issue_comment")
        self.assertTrue(self.resolve()["ready"])
        self.source["conclusion"] = "failure"
        self.assertFalse(self.resolve()["ready"])

    def test_merge_freeze_does_not_recommend_again(self):
        self.source.update(path=".github/workflows/review-plan.yml", event="pull_request",
                           head_branch="research/review-issue-2")
        self.assertFalse(self.resolve()["ready"])
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
        ):
            with self.subTest(update=update):
                self.payload = {**original, **update}
                with self.assertRaises(ValueError):
                    self.resolve()
        self.payload = original
        self.pull["head"]["repo"]["full_name"] = "outsider/fork"
        with self.assertRaisesRegex(ValueError, "same-repository"):
            self.resolve()

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
    def test_report_tree_code_cannot_replace_cached_helpers_or_checks(self):
        with tempfile.TemporaryDirectory(prefix="trusted-review-code-") as folder:
            root = Path(folder)
            shutil.copytree(research.ROOT / "app", root / "app",
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            script = r'''
import importlib, os
from pathlib import Path
from app import research
research.pin_review_code()
for name in ("app/input_review.py", "app/recommendation_events.py", "app/blocks/checks/cohort.py"):
    (research.ROOT / name).write_text('raise RuntimeError("UNTRUSTED_PR_CODE")', encoding="utf-8")
from app import input_review, recommendation_events
assert input_review.mapping_lines({"provider":"fixture","profile_id":"fixture","bindings":{}})
os.environ["GITHUB_OUTPUT"] = str(research.ROOT / "outputs.txt")
recommendation_events.write_outputs({"ready":False})
assert research.report({"metadata":[]}, identifiers=["cohort"])["results"][0]["status"] == "unverifiable"
'''
            result = subprocess.run([sys.executable, "-S", "-B", "-c", script],
                                    cwd=root, env={**os.environ, "PYTHONPATH": str(root)},
                                    capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)

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


if __name__ == "__main__":
    unittest.main()
