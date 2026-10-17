"""PR2 publishes only verified aggregate results from the successful model run."""

from copy import deepcopy
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app import model_evaluation, result_review as publish
from app.asset_contract import canonical_hash, read_json, write_json
import test_model_evaluation as fixtures


REPO = fixtures.REPO
CODE = fixtures.CODE
RUN = "987"


class ResultReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ModelEvaluationTests(
            "test_only_the_exact_merged_same_repository_research_pr_is_accepted")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.directory = self.fixture.fixture.base / "aggregate-result"
        self.directory.mkdir()
        provenance = {
            "repository": REPO, "pull_request": 43, "issue_number": 14,
            "merge_commit": "c" * 40, "plan_code_commit": CODE, "execution_code_commit": CODE,
            "workflow_run_url": f"https://github.com/{REPO}/actions/runs/{RUN}"}
        prediction = {
            "context": {"y_true": [0, 1], "y_probability": [0.1, 0.9]},
            "data_manifest": self.fixture.manifest, "model_manifest": {"model_id": "d" * 64},
            "summary": {
                "split": "validation", "rows": 2, "feature_count": 2,
                "positive_class": 1, "model_class": "sklearn.linear_model.LogisticRegression",
                "runtime_scikit_learn": "1.2.2", "training_performed": False,
                "preprocessing_performed": False, "inference_performed": True}}
        with patch.object(model_evaluation.inference, "predict", return_value=prediction):
            self.result = model_evaluation.evaluate(
                self.directory, self.directory, self.fixture.reports, self.fixture.config, provenance)
        self.save()
        self.branch = "research/result-issue-14-validation"
        self.report_dir = "research/results/issue-14/validation"
        self.previous = None
        self.previous_markdown = None
        self.ref = None
        self.changed_files = []
        self.pull = None
        self.source = {
            "id": 987, "run_attempt": 1, "workflow_id": 178, "name": publish.WORKFLOW_NAME,
            "path": publish.WORKFLOW_PATH, "status": "completed", "conclusion": "success",
            "event": "pull_request", "head_sha": CODE, "pull_requests": [{"number": 43}],
            "repository": {"full_name": REPO}, "head_repository": {"full_name": REPO}}
        self.fixture.pull["head"]["sha"] = CODE
        self.artifacts = {"total_count": 1, "artifacts": [{
            "id": 12345, "name": f"model-evaluation-{RUN}-1", "expired": False,
            "workflow_run": {"id": int(RUN), "head_sha": CODE}}]}
        self.addCleanup(patch.stopall)
        patch.object(publish.model_evaluation, "read_reports", return_value=self.fixture.reports).start()
        patch.object(publish.model_evaluation, "verify_code").start()
        patch.object(publish.research, "command", return_value=SimpleNamespace(stdout=CODE, returncode=0)).start()
        self.remote = patch.object(publish.research, "gh_json", side_effect=self.api).start()
        self.identity = publish.model_identity
        patch.object(publish, "model_identity", return_value="d" * 64).start()
        patch.object(publish.research, "existing_pull", side_effect=lambda *args: (self.pull, None)).start()
        patch.object(publish, "read_tree", return_value={}).start()
        patch.object(publish, "read_file", side_effect=self.file).start()
        self.commit = patch.object(publish, "_commit_files", side_effect=self.commit_files).start()
        self.write = patch.object(publish, "_write_api", side_effect=self.write_api).start()

    def save(self):
        write_json(self.directory / "evaluation.json", self.result)
        (self.directory / "report.md").write_text(model_evaluation.markdown(self.result), encoding="utf-8")

    def api(self, path, *args):
        if re.fullmatch(rf"repos/{re.escape(REPO)}/actions/runs/[0-9]+", path):
            return {**self.source, "id": int(path.rsplit("/", 1)[1])}
        if path == f"repos/{REPO}/actions/workflows/evaluate-model.yml":
            return {"id": 178, "path": publish.WORKFLOW_PATH,
                    "name": publish.WORKFLOW_NAME, "state": "active"}
        if path == f"repos/{REPO}/actions/runs/{RUN}/artifacts?per_page=100":
            return self.artifacts
        if path == f"repos/{REPO}":
            return {"default_branch": "main"}
        if re.fullmatch(rf"repos/{re.escape(REPO)}/pulls/[0-9]+", path):
            return self.fixture.pull
        if path.endswith("/git/ref/heads/main"):
            return {"object": {"sha": "b" * 40}}
        if path.endswith("/git/ref/heads/" + self.branch):
            return {"object": {"sha": self.ref}}
        if "/git/matching-refs/heads/" in path:
            return [] if self.ref is None else [
                {"ref": "refs/heads/" + self.branch, "object": {"sha": self.ref}}]
        if "/compare/" in path:
            return {"files": [{"filename": name} for name in self.changed_files]}
        raise AssertionError("Unexpected publisher API path: " + path)

    def file(self, repository, tree, path, api, **kwargs):
        if self.previous is None:
            return None
        if path.endswith("/evaluation.json"):
            return (json.dumps(self.previous) + "\n").encode()
        if path.endswith("/report.md"):
            if self.previous_markdown is not None:
                return self.previous_markdown
            return model_evaluation.markdown(self.previous).encode()
        raise AssertionError(path)

    def write_api(self, repository, endpoint, payload, method="POST"):
        if endpoint == "git/refs":
            self.ref = payload["sha"]
            return {"ref": payload["ref"]}
        return {"number": 27, "html_url": f"https://github.com/{REPO}/pull/27"}

    def commit_files(self, repository, branch, parent, files, message):
        self.ref = "e" * 40
        if self.pull:
            self.pull["head"]["sha"] = self.ref
        return self.ref

    def run_publish(self):
        return publish.publish(REPO, self.directory, RUN)

    def existing(self):
        self.ref = "f" * 40
        self.previous = deepcopy(self.result)
        self.changed_files = [self.report_dir + "/evaluation.json", self.report_dir + "/report.md"]
        self.pull = {"number": 27, "body": "", "title": "평가결과 #14: 고정 모델 · validation",
                     "state": "open", "draft": False,
                     "head": {"sha": self.ref, "ref": self.branch, "repo": {"full_name": REPO}},
                     "base": {"ref": "main", "repo": {"full_name": REPO}}}

    def test_creates_draft_pr2_with_exact_two_aggregate_files(self):
        result = self.run_publish()
        self.assertEqual(result["status"], "created")
        self.assertEqual(result["pull_url"], f"https://github.com/{REPO}/pull/27")
        self.assertEqual(set(self.commit.call_args.args[3]), {
            self.report_dir + "/evaluation.json", self.report_dir + "/report.md"})
        creation = next(call for call in self.write.call_args_list if call.args[1] == "pulls")
        self.assertTrue(creation.args[2]["draft"])
        self.assertEqual(creation.args[2]["head"], self.branch)
        self.assertIn("/pull/43", creation.args[2]["body"])
        self.assertNotIn("y_probability", creation.args[2]["body"])

    def test_published_json_and_markdown_roundtrip_into_a_true_noop(self):
        self.run_publish()
        files = self.commit.call_args.args[3]
        creation = next(call for call in self.write.call_args_list if call.args[1] == "pulls")
        self.existing()
        self.previous = publish.read_object(files[self.report_dir + "/evaluation.json"])
        self.previous_markdown = files[self.report_dir + "/report.md"]
        self.assertEqual(self.previous_markdown, model_evaluation.markdown(self.previous).encode("utf-8"))
        self.pull["body"] = creation.args[2]["body"]
        self.commit.reset_mock()
        self.write.reset_mock()
        self.assertEqual(self.run_publish()["status"], "unchanged")
        self.commit.assert_not_called()
        self.write.assert_not_called()

    def test_same_results_reuse_open_pr_without_new_commit_even_on_new_run(self):
        self.existing()
        self.previous["provenance"]["workflow_run_url"] = f"https://github.com/{REPO}/actions/runs/986"
        result = self.run_publish()
        self.assertEqual(result["status"], "updated")
        self.commit.assert_not_called()
        self.assertFalse(any(call.args[1] in ("git/refs", "pulls") for call in self.write.call_args_list))

    def test_existing_result_branch_without_pr_recovers_without_overwrite(self):
        self.existing()
        self.pull = None
        result = self.run_publish()
        self.assertEqual(result["status"], "created")
        self.commit.assert_not_called()
        self.assertFalse(any(call.args[1] == "git/refs" for call in self.write.call_args_list))

    def test_closed_pr2_is_not_reopened_or_recreated(self):
        with patch.object(publish.research, "existing_pull", return_value=(
                None, {"status": "deferred", "reason": "closed"})):
            self.assertEqual(self.run_publish()["status"], "deferred")
        self.commit.assert_not_called()
        self.write.assert_not_called()

    def test_wrong_result_origin_plan_or_unsafe_output_is_rejected_before_writes(self):
        original = deepcopy(self.result)
        changes = [
            lambda v: v["provenance"].update(repository="other/repo"),
            lambda v: v["provenance"].update(merge_commit="f" * 40),
            lambda v: v.update(plan_hash="0" * 64),
            lambda v: v.update(data_image="ghcr.io/fake/data@sha256:" + "0" * 64),
            lambda v: v.update(model_id="e" * 64),
            lambda v: v.update(y_probability=[0.1, 0.9]),
            lambda v: v["metrics"][0]["details"].update(patient_key="synthetic-private"),
            lambda v: v["inference"].update(rows=3),
        ]
        for change in changes:
            with self.subTest(change=str(change)):
                self.result = deepcopy(original)
                change(self.result)
                self.save()
                with self.assertRaises(ValueError):
                    self.run_publish()
        self.commit.assert_not_called()
        self.write.assert_not_called()

    def test_changed_markdown_or_extra_artifact_files_are_rejected(self):
        (self.directory / "report.md").write_text("Unverified text", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.run_publish()
        self.save()
        (self.directory / "private.csv").write_text("synthetic row", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.run_publish()
        self.write.assert_not_called()

    def test_branch_with_extra_code_or_wrong_owner_is_preserved_and_rejected(self):
        self.existing()
        self.changed_files.append("app/research.py")
        with self.assertRaises(ValueError):
            self.run_publish()
        self.changed_files.pop()
        self.previous["provenance"]["issue_number"] = 99
        with self.assertRaises(ValueError):
            self.run_publish()
        self.commit.assert_not_called()
        self.write.assert_not_called()

    def test_publication_preserves_human_body_and_refuses_concurrent_branch_change(self):
        self.existing()
        self.previous["metrics"][0]["value"] = 0.7
        self.pull["body"] = "Human review note."
        result = self.run_publish()
        self.assertEqual(result["status"], "updated")
        body = self.write.call_args.args[2]["body"]
        self.assertIn("Human review note.", body)
        self.commit.assert_called_once()
        self.commit.side_effect = ValueError("branch changed")
        with self.assertRaisesRegex(ValueError, "branch changed"):
            self.run_publish()

    def test_source_requires_registered_successful_same_repository_evaluation_workflow(self):
        self.assertEqual(publish.resolve_source(REPO, RUN, "1")["run_attempt"], 1)
        original = deepcopy(self.source)
        for change in ({"conclusion": "failure"}, {"path": ".github/workflows/ci.yml"},
                       {"head_repository": {"full_name": "other/repo"}}, {"run_attempt": 2},
                       {"workflow_id": 179}, {"name": "Other workflow"}, {"event": "push"}):
            with self.subTest(change=change):
                self.remote.reset_mock()
                self.source = {**original, **change}
                with self.assertRaises(ValueError):
                    publish.resolve_source(REPO, RUN, "1")
                self.assertFalse(any("/artifacts" in call.args[0] for call in self.remote.call_args_list))

    def test_stale_or_untrusted_source_is_rejected_before_artifact_lookup(self):
        for target, value in (
                ("verify_code", ValueError("stale")),
                ("command", SimpleNamespace(stdout=CODE, returncode=1))):
            self.remote.reset_mock()
            owner = publish.model_evaluation if target == "verify_code" else publish.research
            kwargs = {"side_effect": value} if isinstance(value, Exception) else {"return_value": value}
            with patch.object(owner, target, **kwargs), self.assertRaises(ValueError):
                publish.resolve_source(REPO, RUN, "1")
            self.assertFalse(any("/artifacts" in call.args[0] for call in self.remote.call_args_list))

    def test_exact_artifact_attempt_identity_expiry_and_unique_name_are_required(self):
        original = deepcopy(self.artifacts)
        for change in (
                lambda v: v["artifacts"][0].update(expired=True),
                lambda v: v["artifacts"][0].update(name=f"model-evaluation-{RUN}-2"),
                lambda v: v["artifacts"][0]["workflow_run"].update(id=986),
                lambda v: v["artifacts"][0]["workflow_run"].update(head_sha="f" * 40),
                lambda v: (v["artifacts"].append(deepcopy(v["artifacts"][0])), v.update(total_count=2)),
                lambda v: v.update(total_count=101)):
            with self.subTest(change=str(change)):
                self.artifacts = deepcopy(original)
                change(self.artifacts)
                with self.assertRaises(ValueError):
                    self.run_publish()
        self.write.assert_not_called()

    def test_source_pr_link_and_execution_ancestry_cannot_be_forged(self):
        self.source["pull_requests"] = [{"number": 44}]
        with self.assertRaises(ValueError):
            self.run_publish()
        self.source["pull_requests"] = [{"number": 43}]
        self.source["head_sha"] = "f" * 40
        self.artifacts["artifacts"][0]["workflow_run"]["head_sha"] = "f" * 40
        with self.assertRaises(ValueError):
            self.run_publish()
        self.write.assert_not_called()

    def test_model_identity_is_bound_to_frozen_model_data_and_notice_contract(self):
        manifest = deepcopy(self.fixture.manifest)
        spec = read_json(self.fixture.fixture.original / "config" / "prepared-source.json")
        manifest.update(prepared_source=spec, raw_source_sha256="1" * 64)
        manifest["files"]["NOTICE.txt"] = "2" * 64
        expected = {
            "schema_version": 2, "kind": "model-artifact", "data_id": manifest["data_id"],
            "model_source": spec["model"], "raw_source_sha256": manifest["raw_source_sha256"],
            "feature_columns": manifest["feature_columns"],
            "input_contract": "already-standardized ordered prepared features; no additional scaler",
            "target_mapping": spec["target_mapping"], "classes": [0, 1], "positive_class": 1,
            "training_performed": False, "inference_performed_by_publisher": False,
            "files": {"logistic_regression.pkl": spec["model"]["checkpoint"]["sha256"],
                      "MODEL_SOURCE_README.txt": spec["model"]["readme"]["sha256"],
                      "NOTICE.txt": "2" * 64}}
        identity = self.identity(manifest)
        self.assertEqual(identity, canonical_hash(expected))
        for change in (
                lambda v: v.update(data_id="3" * 64),
                lambda v: v["files"].update({"NOTICE.txt": "3" * 64}),
                lambda v: v["prepared_source"]["model"]["checkpoint"].update(sha256="3" * 64)):
            altered = deepcopy(manifest)
            change(altered)
            self.assertNotEqual(self.identity(altered), identity)

    def test_threshold_prerequisites_and_registered_top_k_aggregates_remain_honest(self):
        plan = deepcopy(self.fixture.reports["evaluation-plan.json"])
        plan.update(selected_ids=["f1", "recall", "precision", "categorical_nri"],
                    threshold=None, parameters={})
        result = {"selected_ids": plan["selected_ids"], "inference": {"rows": 2},
                  "metrics": publish.executor_eval.execute_plan(
                      plan, {"y_true": [0, 1], "y_probability": [0.1, 0.9]})}
        publish.validate_metrics(result, plan)
        self.assertTrue(all(item["status"] == "not_applicable" and item["value"] is None
                            for item in result["metrics"]))
        result["metrics"][0].update(status="completed", value=1.0)
        with self.assertRaises(ValueError):
            publish.validate_metrics(result, plan)
        plan.update(selected_ids=["f1"], threshold=0.3)
        result["selected_ids"] = ["f1"]
        result["metrics"] = publish.executor_eval.execute_plan(
            plan, {"y_true": [0, 1], "y_probability": [0.1, 0.9]})
        publish.validate_metrics(result, plan)
        result["metrics"][0]["details"]["threshold"] = 0.5
        with self.assertRaises(ValueError):
            publish.validate_metrics(result, plan)
        plan.update(selected_ids=["precision_at_k"], parameters={"top_fraction": 0.5})
        result["selected_ids"] = plan["selected_ids"]
        result["metrics"] = publish.executor_eval.execute_plan(
            plan, {"y_true": [0, 1], "y_probability": [0.1, 0.9]})
        publish.validate_metrics(result, plan)
        self.assertNotIn("selected_indices", result["metrics"][0]["details"])

    def test_true_noop_preserves_human_title_body_ready_state_and_previous_run_link(self):
        self.existing()
        self.previous["provenance"]["workflow_run_url"] = f"https://github.com/{REPO}/actions/runs/986"
        self.pull["title"] = "Human edited title"
        generated = publish.pr_body(self.previous, REPO, self.report_dir, self.branch)
        self.pull["body"] = "Human prefix\n" + publish.research.merge_auto_body("", generated) + "\nHuman suffix"
        before = deepcopy(self.pull)
        self.assertEqual(self.run_publish()["status"], "unchanged")
        self.assertEqual(self.pull, before)
        self.commit.assert_not_called()
        self.write.assert_not_called()

    def test_existing_markdown_tampering_missing_file_and_invalid_old_run_are_preserved(self):
        self.existing()
        self.previous_markdown = b"Human-edited result file"
        with self.assertRaises(ValueError):
            self.run_publish()
        self.previous_markdown = None
        self.previous["provenance"]["workflow_run_url"] = "https://example.invalid/actions/runs/986"
        with self.assertRaises(ValueError):
            self.run_publish()
        self.commit.assert_not_called()
        self.write.assert_not_called()

    def test_reserved_branch_name_alone_never_proves_publication_ownership(self):
        self.ref = "b" * 40
        with self.assertRaisesRegex(ValueError, "ownership"):
            self.run_publish()
        self.write.assert_not_called()
        self.commit.assert_not_called()

    def test_validation_and_explicit_test_have_distinct_publication_targets(self):
        self.source["event"] = "workflow_dispatch"
        self.result["inference"]["split"] = "test"
        self.save()
        self.branch = "research/result-issue-14-test"
        self.report_dir = "research/results/issue-14/test"
        result = self.run_publish()
        self.assertEqual(result["split"], "test")
        self.assertEqual(set(self.commit.call_args.args[3]), {
            self.report_dir + "/evaluation.json", self.report_dir + "/report.md"})
        self.assertEqual(self.write.call_args.args[2]["head"], self.branch)

    def test_implicit_test_split_and_duplicate_json_keys_fail_without_writes(self):
        self.result["inference"]["split"] = "test"
        self.save()
        with self.assertRaises(ValueError):
            self.run_publish()
        self.result["inference"]["split"] = "validation"
        self.save()
        path = self.directory / "evaluation.json"
        raw = path.read_text(encoding="utf-8")
        path.write_text(raw.replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1'),
                        encoding="utf-8")
        with self.assertRaises(ValueError):
            self.run_publish()
        self.write.assert_not_called()
        self.commit.assert_not_called()

    def test_branch_head_and_pr_closure_are_rechecked_before_committing(self):
        self.existing()
        self.previous["metrics"][0]["value"] = 0.7
        original = self.api
        def api(path, *args):
            if path.endswith("/git/ref/heads/" + self.branch):
                return {"object": {"sha": "e" * 40}}
            return original(path, *args)
        self.remote.side_effect = api
        with self.assertRaisesRegex(ValueError, "branch changed"):
            self.run_publish()
        self.remote.side_effect = original
        with patch.object(publish.research, "existing_pull", side_effect=[
                (self.pull, None), (None, {"status": "deferred", "reason": "closed"})]):
            self.assertEqual(self.run_publish()["status"], "deferred")
        self.commit.assert_not_called()
        self.write.assert_not_called()

    def test_older_source_never_overwrites_a_newer_result(self):
        self.existing()
        self.previous["provenance"]["workflow_run_url"] = f"https://github.com/{REPO}/actions/runs/988"
        with self.assertRaisesRegex(ValueError, "older"):
            self.run_publish()
        self.commit.assert_not_called()
        self.write.assert_not_called()

    def test_squash_merged_pr_head_need_not_be_an_ancestor_of_default_branch(self):
        pr_head = "9" * 40
        self.source["head_sha"] = pr_head
        self.fixture.pull["head"]["sha"] = pr_head
        self.artifacts["artifacts"][0]["workflow_run"]["head_sha"] = pr_head
        def command(arguments, **kwargs):
            disconnected_pr_head = arguments[:4] == [
                "git", "merge-base", "--is-ancestor", pr_head]
            return SimpleNamespace(stdout=CODE, returncode=int(disconnected_pr_head))
        with patch.object(publish.research, "command", side_effect=command):
            self.assertEqual(self.run_publish()["status"], "created")
        self.source["event"] = "workflow_dispatch"
        with patch.object(publish.research, "command", side_effect=command):
            with self.assertRaisesRegex(ValueError, "history"):
                publish.resolve_source(REPO, RUN, "1")

    def test_broken_human_markers_fail_before_any_branch_or_commit_write(self):
        self.existing()
        self.previous["metrics"][0]["value"] = 0.7
        self.pull["body"] = publish.research.AUTO_START + "\nBroken"
        with self.assertRaises(ValueError):
            self.run_publish()
        self.commit.assert_not_called()
        self.write.assert_not_called()

    def test_latest_human_body_is_refetched_and_preserved_before_patch(self):
        self.existing()
        self.previous["metrics"][0]["value"] = 0.7
        original_commit = self.commit_files
        def commit(*args):
            head = original_commit(*args)
            self.pull["body"] = "Human note added while publication was running."
            self.pull["title"] = "Human title added during publication"
            return head
        self.commit.side_effect = commit
        self.run_publish()
        payload = self.write.call_args.args[2]
        self.assertIn("Human note added while publication was running.", payload["body"])
        self.assertNotIn("title", payload)
        self.assertNotIn("draft", payload)

    def test_creation_failure_keeps_verified_branch_for_retry(self):
        original = self.write_api
        def write(repository, endpoint, payload, method="POST"):
            if endpoint == "pulls":
                raise RuntimeError("permission denied")
            return original(repository, endpoint, payload, method)
        self.write.side_effect = write
        with self.assertRaisesRegex(RuntimeError, "branch is retained"):
            self.run_publish()
        self.assertEqual(self.ref, "e" * 40)
        self.assertFalse(any(call.kwargs.get("method") == "DELETE" for call in self.write.call_args_list))

    def test_publisher_workflow_separates_writes_from_inference(self):
        import yaml
        root = self.fixture.fixture.original
        value = yaml.load((root / ".github" / "workflows" / "publish-evaluation.yml").read_text(encoding="utf-8"),
                          Loader=yaml.BaseLoader)
        self.assertEqual(value["on"]["workflow_run"]["workflows"], ["Evaluate merged research plan"])
        job = next(iter(value["jobs"].values()))
        self.assertIn("success", job["if"])
        self.assertEqual(value["permissions"]["contents"], "write")
        self.assertNotIn("pip install", json.dumps(value))
        self.assertNotIn("requirements-inference", json.dumps(value))
        self.assertEqual(len(value["jobs"]), 1)
        self.assertEqual(value["concurrency"]["group"], "publish-evaluation-${{ github.repository }}")
        steps = job["steps"]
        verify = next(index for index, step in enumerate(steps) if step.get("id") == "source")
        download = next(index for index, step in enumerate(steps)
                        if "download-artifact@" in step.get("uses", ""))
        self.assertLess(verify, download)
        self.assertEqual(steps[download]["with"]["artifact-ids"], "${{ steps.source.outputs.artifact_id }}")
        self.assertEqual(steps[download]["with"]["run-id"], "${{ github.event.workflow_run.id }}")
        self.assertEqual(steps[0]["with"]["persist-credentials"], "false")


if __name__ == "__main__":
    unittest.main()
