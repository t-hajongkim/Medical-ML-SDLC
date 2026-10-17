"""Synthetic aggregate snapshots: empty -> target -> plan -> real result binding."""

from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from app import dashboard as run, model_evaluation, result_review
from app.asset_contract import canonical_hash, write_json
import test_model_evaluation as fixtures


REPO = fixtures.REPO
CODE = fixtures.CODE


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ModelEvaluationTests(
            "test_only_the_exact_merged_same_repository_research_pr_is_accepted")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.fixture.root
        self.base = self.fixture.fixture.base
        self.reports = self.fixture.reports
        self.pull = deepcopy(self.fixture.pull)
        self.pull.update(html_url=f"https://github.com/{REPO}/pull/43", draft=True)
        self.pull["head"]["sha"] = "d" * 40
        self.result_pull = {
            "number": 44, "state": "open", "draft": True,
            "html_url": f"https://github.com/{REPO}/pull/44",
            "head": {"ref": "research/result-issue-14-validation", "sha": "f" * 40,
                     "repo": {"full_name": REPO}},
            "base": {"ref": "main", "repo": {"full_name": REPO}}}
        self.result = None
        self.source = {
            "id": 987, "run_attempt": 1, "workflow_id": 178,
            "path": ".github/workflows/evaluate-model.yml",
            "name": "Evaluate merged research plan", "event": "pull_request",
            "head_sha": "d" * 40, "head_branch": "research/review-issue-14",
            "status": "completed", "conclusion": "success",
            "repository": {"full_name": REPO}, "head_repository": {"full_name": REPO}}
        self.public_files = {}
        self.ref = None
        self.changed = []
        template = self.root / "dashboard"
        (template / "assets").mkdir(parents=True)
        (template / "index.html").write_text("<!doctype html><title>Fixture dashboard</title>", encoding="utf-8")
        self.addCleanup(patch.stopall)
        self.fixture.fixture.remote.side_effect = self.api
        patch.object(model_evaluation, "read_reports", return_value=self.reports).start()
        patch.object(run, "read_tree", side_effect=lambda *args: self.tree()).start()
        patch.object(run, "read_file", side_effect=self.file).start()
        self.commit = patch.object(run, "_commit_files", side_effect=self.commit_files).start()
        self.write = patch.object(run, "_write_api", side_effect=self.write_api).start()

    def tree(self):
        return {path: {"sha": self.blob(content)} for path, content in self.public_files.items()}

    @staticmethod
    def blob(content):
        return hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()

    def api(self, path, *args):
        if path == f"repos/{REPO}":
            return {"default_branch": "main", "private": False}
        if path == f"repos/{REPO}/issues/14":
            return self.fixture.fixture.original_issue
        if path == f"repos/{REPO}/pulls/43":
            return self.pull
        if path.startswith(f"repos/{REPO}/pulls?"):
            return [[self.result_pull] if self.result and "result-issue" in path else [self.pull]
                    if "review-issue" in path else []]
        if path.endswith("/git/matching-refs/heads/dashboard-data"):
            return [{"ref": "refs/heads/dashboard-data", "object": {"sha": self.ref}}] if self.ref else []
        if path.endswith("/git/ref/heads/main"):
            return {"object": {"sha": CODE}}
        if path.endswith("/actions/runs/987"):
            return self.source
        if path.endswith("/actions/workflows/evaluate-model.yml"):
            return {"id": 178, "name": self.source["name"], "path": self.source["path"], "state": "active"}
        raise AssertionError("Unexpected dashboard API: " + path)

    def file(self, repository, tree, path, api, **kwargs):
        if path.startswith("research/results/") and self.result:
            return (run.encoded(self.result) if path.endswith(".json")
                    else model_evaluation.markdown(self.result).encode("utf-8"))
        return self.public_files.get(path)

    def write_api(self, repository, endpoint, payload, **kwargs):
        self.assertEqual(endpoint, "git/refs")
        self.assertEqual(payload["ref"], "refs/heads/dashboard-data")
        self.ref = payload["sha"]
        return {"ref": payload["ref"]}

    def commit_files(self, repository, branch, parent, files, message):
        self.assertEqual(branch, "dashboard-data")
        self.assertTrue(all(path.startswith("dashboard/") for path in files))
        self.changed.append(files)
        self.public_files.update(files)
        self.ref = "e" * 40
        return self.ref

    def evaluate(self):
        prediction = {
            "context": {"y_true": [0, 1], "y_probability": [0.1, 0.9]},
            "data_manifest": self.fixture.manifest, "model_manifest": {"model_id": "b" * 64},
            "summary": {"split": "validation", "rows": 2, "feature_count": 2, "positive_class": 1,
                        "model_class": "sklearn.linear_model.LogisticRegression", "runtime_scikit_learn": "1.2.2",
                        "training_performed": False, "preprocessing_performed": False,
                        "inference_performed": True}}
        origin = {"repository": REPO, "pull_request": 43, "issue_number": 14,
                  "merge_commit": "c" * 40, "plan_code_commit": CODE,
                  "execution_code_commit": "c" * 40,
                  "workflow_run_url": f"https://github.com/{REPO}/actions/runs/987"}
        with patch.object(model_evaluation.inference, "predict", return_value=prediction):
            result = model_evaluation.evaluate(
                self.base, self.base, self.reports, self.fixture.config, origin)
        patch.object(result_review, "model_identity", return_value="b" * 64).start()
        return json.loads(json.dumps(result, sort_keys=True))

    def publish(self, issue="14"):
        output = self.base / f"dashboard-output-{len(list(self.base.glob('dashboard-output-*')))}"
        event = {"repository": {"full_name": REPO, "default_branch": "main"}}
        value = run.publish(REPO, event, "workflow_dispatch", output, issue)
        return value, json.loads((output / "assets" / "dashboard-data.json").read_text(encoding="utf-8"))

    def test_empty_template_contains_no_zero_results_or_local_smoke(self):
        value, payload = self.publish("")
        self.assertEqual(value["stage"], "idle")
        self.assertIsNone(payload["target"])
        self.assertEqual(payload["dataset"], {})
        self.assertIsNone(payload["remote_model_evaluation"])
        self.assertIsNone(payload["local_smoke"])

    def test_issue_only_snapshot_does_not_fill_eda_or_model(self):
        with patch.object(run, "find_pull", return_value=None):
            payload = run.build_snapshot(REPO, 14, "main")
        self.assertEqual(payload["pipeline"]["stage"], "target")
        self.assertEqual(payload["target"]["issue_number"], 14)
        self.assertEqual(payload["eda"]["results"], [])
        self.assertIsNone(payload["remote_model_evaluation"])

    def test_open_pr_fills_eda_and_plan_without_inventing_metrics(self):
        self.pull.update(state="open", merged=False)
        _, payload = self.publish()
        self.assertEqual(payload["pipeline"]["stage"], "evaluation-plan")
        self.assertEqual(payload["eda"]["results"], self.reports["eda.json"]["results"])
        self.assertEqual(payload["dataset"]["feature_count"], 2)
        self.assertIsNone(payload["remote_model_evaluation"])
        self.assertIsNone(payload["remote_pr2"])
        run.check_public(payload)

    def test_merged_plan_has_no_model_results_until_execution(self):
        _, payload = self.publish()
        self.assertEqual(payload["pipeline"]["stage"], "model")
        self.assertIsNone(payload["remote_model_evaluation"])

    def test_exact_model_artifact_fills_evaluation_before_pr2_exists(self):
        result = self.evaluate()
        payload = run.build_snapshot(REPO, 14, "main", evaluation=result)
        self.assertEqual(payload["pipeline"]["stage"], "evaluation")
        self.assertEqual(payload["remote_model_evaluation"], result)
        self.assertIsNone(payload["remote_pr2"])

    def test_pr2_fills_actual_values_and_preserves_draft_state(self):
        self.result = self.evaluate()
        _, payload = self.publish()
        self.assertEqual(payload["pipeline"]["stage"], "result")
        self.assertEqual(payload["remote_model_evaluation"], self.result)
        self.assertTrue(payload["remote_pr2"]["isDraft"])
        self.assertEqual(payload["remote_pr2"]["number"], 44)

    def test_previous_verified_evaluation_is_retained_while_publisher_is_pending(self):
        result = self.evaluate()
        previous = {"remote_model_evaluation": result}
        payload = run.build_snapshot(REPO, 14, "main", previous)
        self.assertEqual(payload["remote_model_evaluation"], result)

    def test_different_issue_plan_or_dataset_cannot_supply_results(self):
        original = self.evaluate()
        for field, value in (("data_id", "0" * 64), ("plan_hash", "0" * 64)):
            result = deepcopy(original)
            result[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                run.build_snapshot(REPO, 14, "main", evaluation=result)
        result = deepcopy(original)
        result["provenance"]["issue_number"] = 13
        with self.assertRaises(ValueError):
            run.build_snapshot(REPO, 14, "main", previous={"remote_model_evaluation": result})

    def test_failed_or_foreign_source_cannot_claim_model_execution(self):
        result = self.evaluate()
        for change in ({"conclusion": "failure"},
                       {"head_repository": {"full_name": "attacker/fork"}},
                       {"head_sha": "9" * 40}, {"workflow_id": 999}):
            with self.subTest(change=change), patch.object(
                    run.research, "gh_json", side_effect=lambda path, *args:
                    {**self.source, **change} if "/actions/runs/" in path else self.api(path, *args)):
                with self.assertRaises(ValueError):
                    run.validate_evaluation(
                        result, self.reports, self.fixture.manifest, REPO, self.pull, "c" * 40)

    def test_missing_metrics_wrong_runtime_and_stale_code_are_rejected(self):
        original = self.evaluate()
        changes = [
            lambda value: value.update(metrics=[]),
            lambda value: value["inference"].update(runtime_scikit_learn="1.6.1"),
            lambda value: value["inference"].update(positive_class=True),
            lambda value: value["provenance"].update(plan_code_commit="9" * 40),
            lambda value: value.update(data_image="ghcr.io/other/data@sha256:" + "0" * 64),
        ]
        for change in changes:
            result = deepcopy(original)
            change(result)
            with self.subTest(change=change), self.assertRaises(ValueError):
                run.build_snapshot(REPO, 14, "main", evaluation=result)

    def test_foreign_active_snapshot_cannot_be_republished(self):
        active = run.empty_snapshot("other/repository")
        self.public_files[run.DATA_PATH] = run.encoded(active)
        with self.assertRaises(ValueError):
            self.publish()

    def test_individual_prediction_keys_are_rejected_recursively(self):
        for key in run.PRIVATE_KEYS:
            with self.subTest(key=key), self.assertRaises(ValueError):
                run.check_public({"nested": [{key: [123]}]})

    def test_identical_replay_changes_no_commit_and_retains_timestamp(self):
        self.result = self.evaluate()
        _, first = self.publish()
        self.commit.reset_mock()
        second, payload = self.publish()
        self.assertEqual(second["status"], "unchanged")
        self.assertEqual(first, payload)
        self.commit.assert_not_called()

    def test_empty_replay_also_is_a_true_noop(self):
        _, first = self.publish("")
        self.commit.reset_mock()
        second, payload = self.publish("")
        self.assertEqual(second["status"], "unchanged")
        self.assertEqual(first, payload)
        self.commit.assert_not_called()

    def test_late_older_issue_cannot_replace_newer_active_issue(self):
        active = run.empty_snapshot(REPO)
        active["pipeline"].update(issue_number=99, stage="target")
        active["generated_at"] = "2026-01-01T00:00:00+00:00"
        self.public_files[run.DATA_PATH] = run.encoded(active)
        self.ref = "e" * 40
        _, payload = self.publish()
        self.assertEqual(payload, active)
        self.assertIn("dashboard/data/issue-14.json", self.public_files)

    def test_blank_dispatch_cannot_clear_existing_results(self):
        self.result = self.evaluate()
        _, first = self.publish()
        _, payload = self.publish("")
        self.assertEqual(first, payload)

    def test_templates_are_only_written_to_dashboard_branch(self):
        self.publish()
        self.assertTrue(all(path.startswith("dashboard/") for path in self.changed[0]))
        self.assertNotIn("research/", " ".join(self.changed[0]))
        with self.assertRaises(ValueError):
            run.existing_state(REPO, "dashboard-data")

    def test_not_requested_intake_does_not_publish_a_success_shaped_snapshot(self):
        event = {"repository": {"full_name": REPO},
                 "workflow_run": {"id": 123, "run_attempt": 1}}
        source = {**self.source, "id": 123, "path": ".github/workflows/prepare-eda-pr.yml"}
        with patch.object(run, "source_run", return_value=source), \
                patch.object(run.recommendation_events, "resolve_target", return_value={"ready": False}):
            result = run.publish(REPO, event, "workflow_run", self.base / "not-created")
        self.assertEqual(result["status"], "not_applicable")
        self.commit.assert_not_called()

    def test_failed_model_is_explicit_and_does_not_invent_results(self):
        source = {**self.source, "conclusion": "failure"}
        event = {"repository": {"full_name": REPO},
                 "workflow_run": {"id": 987, "run_attempt": 1}}
        with patch.object(run, "source_run", return_value=source):
            issue, trigger, result = run.resolve_event(event, "workflow_run", REPO)
        self.assertEqual(issue, 14)
        self.assertEqual(trigger["conclusion"], "failure")
        self.assertIsNone(result)

    def test_control_metadata_supports_real_long_run_ids_without_private_text(self):
        runner = self.base / "runner"
        runner.mkdir()
        with patch.dict(os.environ, {"RUNNER_TEMP": str(runner), "GITHUB_REPOSITORY": REPO,
                                    "GITHUB_RUN_ID": "39012966915", "GITHUB_RUN_ATTEMPT": "2",
                                    "GITHUB_SHA": CODE}):
            run.write_target("evaluation_run", "39012902610", "1")
        value = json.loads((runner / run.TARGET_FILE).read_text(encoding="utf-8"))
        self.assertEqual(value["target_number"], 39012902610)
        self.assertTrue(value["requested"])
        run.check_public(value)

    def test_not_requested_control_metadata_has_no_target(self):
        runner = self.base / "runner"
        runner.mkdir()
        with patch.dict(os.environ, {"RUNNER_TEMP": str(runner), "GITHUB_REPOSITORY": REPO,
                                    "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1",
                                    "GITHUB_SHA": CODE}):
            run.write_target(None, None, ready=False)
        value = json.loads((runner / run.TARGET_FILE).read_text(encoding="utf-8"))
        self.assertFalse(value["requested"])
        self.assertNotIn("target_number", value)

    def test_routing_metadata_binds_repository_run_attempt_and_target_kind(self):
        source = {**self.source, "path": ".github/workflows/research-plan.lock.yml"}
        value = {
            "schema_version": 1, "repository": REPO, "source_run_id": "987",
            "source_run_attempt": "1", "source_head_sha": self.source["head_sha"],
            "requested": True, "target_kind": "issue", "target_number": 14}
        with patch.object(run, "artifact_files", return_value={run.TARGET_FILE: run.encoded(value)}):
            self.assertEqual(run.read_target(REPO, source), value)
        for change in ({"repository": "other/repo"}, {"source_run_attempt": "2"},
                       {"source_head_sha": "0" * 40}, {"target_kind": "evaluation_run"},
                       {"extra": "unapproved"}):
            altered = {**value, **change}
            with self.subTest(change=change), \
                    patch.object(run, "artifact_files", return_value={run.TARGET_FILE: run.encoded(altered)}), \
                    self.assertRaises(ValueError):
                run.read_target(REPO, source)

    def test_native_declined_target_is_not_an_update(self):
        source = {**self.source, "path": ".github/workflows/research-plan.lock.yml"}
        event = {"repository": {"full_name": REPO},
                 "workflow_run": {"id": 987, "run_attempt": 1}}
        with patch.object(run, "source_run", return_value=source), \
                patch.object(run, "read_target", return_value={"requested": False}):
            self.assertEqual(run.resolve_event(event, "workflow_run", REPO), (None, source, None))

    def test_artifact_zip_rejects_extra_files_and_duplicate_names(self):
        artifact = {"id": 55, "name": "test", "expired": False,
                    "workflow_run": {"id": 987, "head_sha": self.source["head_sha"]}}
        for names in (["dashboard-target.json", "../private.json"],
                      ["dashboard-target.json", "dashboard-target.json"]):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                for name in names:
                    archive.writestr(name, "{}")
            with self.subTest(names=names), \
                    patch.object(run.research, "gh_json", return_value=[{"artifacts": [artifact]}]), \
                    patch.object(run.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=buffer.getvalue())):
                with self.assertRaises(ValueError):
                    run.artifact_files(REPO, self.source, "test", {run.TARGET_FILE: 65536})

    def test_read_only_plan_validation_accepts_incomplete_plan_but_model_does_not(self):
        reports = deepcopy(self.reports)
        reports["evaluation-plan.json"]["planning_complete"] = False
        rebuilt = {"eda": reports["eda-plan.json"], "evaluation": reports["evaluation-plan.json"]}
        with patch.object(model_evaluation.planning_eval, "build_plans", return_value=rebuilt):
            with self.assertRaises(ValueError):
                model_evaluation.validate_reports(reports, self.fixture.config, REPO, 14)
            self.assertEqual(
                model_evaluation.validate_reports(reports, self.fixture.config, REPO, 14, require_complete=False),
                self.fixture.manifest)


if __name__ == "__main__":
    unittest.main()
