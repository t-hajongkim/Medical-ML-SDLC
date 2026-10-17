"""Merged-plan evaluation, immutable source and aggregate-only result contracts."""

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app import model_evaluation as run
from app.asset_contract import canonical_hash, read_json
import test_pr1_flow as fixtures

REPO, CODE = fixtures.REPO, fixtures.CODE


class ModelEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PR1FlowTests("test_basic_eda_is_complete_before_ai_receives_context")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.finish_initial(extra=["prediction_timing"])
        folder = self.fixture.root / "research" / "reports" / "issue-14"
        self.reports = {name: read_json(folder / name) for name in run.REPORT_NAMES}
        self.config = read_json(self.fixture.root / "config" / "assets.json")
        self.manifest = self.reports["input-mapping.json"]["source_manifest"]
        self.pull = {
            "number": 43, "state": "closed", "merged": True, "merge_commit_sha": "c" * 40,
            "title": "연구검토 #14: 합성 계획",
            "head": {"ref": "research/review-issue-14", "repo": {"full_name": REPO}},
            "base": {"ref": "main", "repo": {"full_name": REPO}},
        }

    def test_only_the_exact_merged_same_repository_research_pr_is_accepted(self):
        self.assertEqual(run.validate_pull(self.pull, REPO, 43, "main"), (14, "c" * 40))
        for change in ({"merged": False}, {"state": "open"}, {"number": 44},
                       {"merge_commit_sha": None},
                       {"head": {"ref": "other", "repo": {"full_name": REPO}}},
                       {"base": {"ref": "main", "repo": {"full_name": "other/repo"}}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                run.validate_pull({**self.pull, **change}, REPO, 43, "main")

    def test_plan_is_rebuilt_for_verification_not_replaced_or_replanned(self):
        before = deepcopy(self.reports)
        result = run.validate_reports(self.reports, self.config, REPO, 14)
        self.assertEqual(result, self.manifest)
        self.assertEqual(self.reports, before)
        for name, mutate in (
                ("evaluation-plan.json", lambda v: v.update(threshold=0.4)),
                ("execution-plan.json", lambda v: v.update(data_image="ghcr.io/fake/data@sha256:" + "0" * 64)),
                ("eda.json", lambda v: v.update(execution_result_hash="0" * 64)),
                ("input-mapping.json", lambda v: v["bindings"].update(patient_key="row_key"))):
            reports = deepcopy(self.reports)
            mutate(reports[name])
            with self.subTest(name=name), self.assertRaises(ValueError):
                run.validate_reports(reports, self.config, REPO, 14)

    def test_changed_data_reference_is_rejected_before_inference(self):
        changed = {**self.config, "model_image": "ghcr.io/fake/model@sha256:" + "0" * 64}
        with self.assertRaises(ValueError):
            run.validate_reports(self.reports, changed, REPO, 14)

    def test_aggregate_result_has_no_private_prediction_vectors_or_ids(self):
        prediction = {
            "context": {"y_true": [0, 1, 0, 1], "y_probability": [0.1, 0.8, 0.3, 0.7]},
            "data_manifest": self.manifest, "model_manifest": {"model_id": "d" * 64},
            "summary": {"split": "validation", "rows": 4, "training_performed": False},
        }
        provenance = {"repository": REPO, "pull_request": 43, "merge_commit": "c" * 40}
        with patch.object(run.inference, "predict", return_value=prediction) as predict:
            result = run.evaluate(self.fixture.base, self.fixture.base, self.reports,
                                  self.config, provenance)
        self.assertEqual(result["inference"]["rows"], 4)
        self.assertTrue(all(item["status"] == "completed" for item in result["metrics"]))
        self.assertEqual(result["selected_ids"], self.reports["evaluation-plan.json"]["selected_ids"])
        public = json.dumps(result)
        for name in ("y_true", "y_probability", "row_key", "patient_key", "sample_ids", "selected_indices"):
            self.assertNotIn('"' + name + '"', public)
        self.assertEqual(predict.call_args.kwargs["expected_manifest"], self.manifest)

    def test_report_reader_uses_only_immutable_merge_tree_and_json_allowlist(self):
        content = json.dumps({"fixture": True}).encode()
        api = Mock()
        with patch.object(run, "read_tree", return_value={"fixture": {}}) as tree, \
                patch.object(run, "read_file", return_value=content) as reader:
            result = run.read_reports(REPO, "c" * 40, 14, api)
        self.assertEqual(set(result), set(run.REPORT_NAMES))
        tree.assert_called_once_with(REPO, "c" * 40, api)
        self.assertTrue(all(call.args[2].startswith("research/reports/issue-14/") for call in reader.call_args_list))

    def test_code_comparison_allows_report_only_merge_but_rejects_stale_runtime(self):
        with patch.object(run.research, "command", return_value=SimpleNamespace(returncode=0)) as git:
            run.verify_code(CODE)
        self.assertIn("diff", git.call_args.args[0])
        self.assertIn("app", git.call_args.args[0])
        with patch.object(run.research, "command", return_value=SimpleNamespace(returncode=1)):
            with self.assertRaises(ValueError):
                run.verify_code(CODE)

    def test_full_runner_reads_merged_plan_publishes_two_files_and_removes_private_assets(self):
        from app import executor_eval
        prediction = {
            "context": {"y_true": [0, 1, 0, 1], "y_probability": [0.1, 0.8, 0.3, 0.7]},
            "data_manifest": self.manifest, "model_manifest": {"model_id": "d" * 64},
            "summary": {"split": "validation", "rows": 4, "training_performed": False},
        }
        directories = []
        def extract(reference, target):
            target.mkdir()
            directories.append(target)
        def api(path):
            return self.pull if path.endswith("/pulls/43") else {"default_branch": "main"}
        output = self.fixture.base / "evaluated"
        with patch.object(run.research, "gh_json", side_effect=api) as remote, \
            patch.object(run, "read_reports", return_value=self.reports) as read, \
            patch.object(run, "verify_code") as code, \
            patch.object(run.research, "command", return_value=SimpleNamespace(stdout=CODE, returncode=0)), \
            patch.object(run.research, "image_assets", side_effect=extract), \
            patch.object(run.inference, "predict", return_value=prediction), \
            patch.object(run.executor_eval, "execute_plan", wraps=executor_eval.execute_plan) as execute:
            result = run.run(REPO, 43, output)
        self.assertEqual(result["status"], "completed")
        self.assertEqual({p.name for p in output.iterdir()}, {"evaluation.json", "report.md"})
        read.assert_called_once_with(REPO, "c" * 40, 14, remote)
        code.assert_called_once_with(self.reports["execution-plan.json"]["code_commit"])
        self.assertEqual(execute.call_args.args[0], self.reports["evaluation-plan.json"])
        self.assertEqual(len(directories), 2)
        self.assertTrue(all(not p.parent.exists() for p in directories))

    def test_inference_failure_removes_private_assets_and_does_not_publish_result(self):
        directories = []
        def extract(reference, target):
            target.mkdir()
            directories.append(target)
        output = self.fixture.base / "failed-evaluation"
        with patch.object(run.research, "gh_json", side_effect=lambda path:
                      self.pull if path.endswith("/pulls/43") else {"default_branch": "main"}), \
            patch.object(run, "read_reports", return_value=self.reports), \
            patch.object(run, "verify_code"), \
            patch.object(run.research, "command", return_value=SimpleNamespace(stdout=CODE, returncode=0)), \
            patch.object(run.research, "image_assets", side_effect=extract), \
            patch.object(run.inference, "predict", side_effect=ValueError("model contract")):
            with self.assertRaisesRegex(ValueError, "model contract"):
                run.run(REPO, 43, output)
        self.assertFalse(output.exists())
        self.assertTrue(all(not p.parent.exists() for p in directories))

    def test_workflow_is_post_merge_read_only_and_publishes_only_aggregate_files(self):
        import yaml
        root = self.fixture.original
        data = yaml.load((root / ".github" / "workflows" / "evaluate-model.yml").read_text(encoding="utf-8"),
                         Loader=yaml.BaseLoader)
        self.assertEqual(data["on"]["pull_request"]["types"], ["closed"])
        self.assertNotIn("issue_comment", data["on"])
        self.assertIn("workflow_dispatch", data["on"])
        self.assertEqual(len(data["jobs"]), 1)
        self.assertTrue(all(value == "read" for value in data["permissions"].values()))
        job = next(iter(data["jobs"].values()))
        self.assertIn("merged", job["if"])
        self.assertNotIn("secrets.", json.dumps(job))
        artifact = next(step for step in job["steps"] if "upload-artifact@" in step.get("uses", ""))
        self.assertEqual(len(artifact["with"]["path"].splitlines()), 2)
        self.assertNotIn("**", artifact["with"]["path"])


if __name__ == "__main__":
    unittest.main()
