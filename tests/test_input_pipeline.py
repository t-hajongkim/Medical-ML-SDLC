"""Synthetic registered packages exercise mapping-before-EDA integration."""

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app import cloud_recommendations as cloud
from app import input_contracts as inputs
from app import planning, research, review
from app.asset_contract import canonical_hash, read_json, write_json
from app.eda import InputMappingRequired, load_context, report
from app.input_review import write_pending
import test_input_contracts as contract_fixtures
from test_research_workflow import issue


REPO = "fixture-owner/workshop"
SHA = "a" * 40
IMAGE = "ghcr.io/fixture-owner/data@sha256:" + "b" * 64


class InputPipelineTests(unittest.TestCase):
    def setUp(self):
        self.fixture = contract_fixtures.InputContractTests(
            "test_default_diabetes_profile_preserves_nine_registered_names")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        temporary = tempfile.TemporaryDirectory(prefix="input-pipeline-", dir=research.ROOT.parent)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def package(self, *, known):
        assets, profile, mapping, *_ = self.fixture.package(known=known)
        profile["probability_output_planned"] = True
        self.fixture.save_config()
        return assets, inputs.load_profile(), mapping

    def test_config_binds_different_names_before_generic_checks_and_plans(self):
        assets, profile, _ = self.package(known=True)
        context = load_context(assets, input_profile=profile)
        aggregate = report(context)
        research.validate_aggregate(aggregate, context["data_manifest"]["prepared_source"])
        by_name = {item["block"]: item for item in aggregate["results"]}
        self.assertEqual(by_name["patient_overlap"]["details"]["overlaps"],
                         {"train_validation": 1, "train_test": 1, "validation_test": 1})
        self.assertEqual(aggregate["dataset_context"]["legacy"], False)
        self.assertEqual(len(aggregate["input_mapping_hash"]), 64)
        plans = planning.build_plans(aggregate, context["data_manifest"],
                                    planning.default_selection(), None)
        self.assertTrue(plans["evaluation"]["planned_inputs"]["y_true"])
        self.assertTrue(plans["evaluation"]["planned_inputs"]["y_probability"])
        text = research.markdown_report(
            {"issue_url": f"https://github.com/{REPO}/issues/2"}, aggregate,
            {"data_image": IMAGE, "code_commit": SHA,
             "workflow_run_url": f"https://github.com/{REPO}/actions/runs/1"}, plans)
        self.assertNotIn("30일 이내", text)
        self.assertNotIn("UCI", text)
        self.assertIn("등록 입력", text)

    def test_unmapped_source_creates_pending_pr_without_running_eda(self):
        assets, _, _ = self.package(known=False)
        candidate = issue()
        request = research.parse_issue(candidate, REPO)
        pending = {"status": "created", "ai_status": "pending_input_mapping",
                   "pull_url": f"https://github.com/{REPO}/pull/7"}
        work = self.root / "runtime"
        with patch.object(research, "intake", return_value=(request, None)), \
                patch.object(research, "existing_pull", return_value=(None, None)), \
                patch.object(research, "command", return_value=SimpleNamespace(stdout=SHA)), \
                patch.object(research, "image_assets",
                             side_effect=lambda ref, target: shutil.copytree(assets, target)), \
                patch.object(research, "report", side_effect=AssertionError("EDA before mapping")), \
                patch.object(research, "publish_payload", return_value=pending) as publish, \
                patch.dict(os.environ, {"GITHUB_RUN_ID": "123"}):
            outcome = research.run_workflow(
                {}, "issues", REPO, "fixture-owner", "main", work)
        self.assertEqual(outcome["ai_dispatch"]["trigger"], "workflow_run")
        self.assertIsNone(publish.call_args.args[2])
        self.assertEqual(publish.call_args.kwargs["input_request"]["stage"], "input_mapping")
        self.assertFalse(work.exists())

    def test_ai_mapping_is_validated_then_reusable_without_reinferring(self):
        assets, profile, mapping = self.package(known=False)
        with self.assertRaises(InputMappingRequired) as required:
            load_context(assets, input_profile=profile)
        proposal = self.fixture.recommendation(required.exception.schema_request, mapping)
        bundle = inputs.apply_mapping(assets, proposal, profile=profile)
        context = load_context(assets, input_profile=profile, input_mapping=bundle["binding"])
        self.assertEqual(report(context)["input_mapping_hash"],
                         report({**context, "input_contract": deepcopy(bundle["binding"])})["input_mapping_hash"])
        invalid = deepcopy(bundle["binding"])
        invalid["context_hash"] = "f" * 64
        with self.assertRaises(InputMappingRequired):
            load_context(assets, input_profile=profile, input_mapping=invalid)

    def test_pending_payload_removes_stale_results_and_preserves_human_selection(self):
        assets, profile, _ = self.package(known=False)
        bundle = inputs.inspect_inputs(assets, profile=profile)
        request = research.parse_issue(issue(), REPO)
        directory = research.report_directory(self.root, request["issue_number"])
        directory.mkdir(parents=True)
        original = b'{"schema_version":1,"evaluation":{"primary_metric":"roc_auc"}}\n'
        (directory / "selection.json").write_bytes(original)
        for name in ("eda.json", "ai-recommendations.json", "input-mapping.json"):
            (directory / name).write_text('{"stale":true}', encoding="utf-8")
        paths = write_pending(
            self.root, request, bundle["schema_request"],
            {"data_image": IMAGE, "code_commit": SHA}, planning.default_selection())
        self.assertEqual((directory / "selection.json").read_bytes(), original)
        self.assertFalse((directory / "eda.json").exists())
        self.assertFalse((directory / "input-mapping.json").exists())
        self.assertIn("실행하지 않았습니다", (directory / "report.md").read_text(encoding="utf-8"))
        self.assertTrue(any(path.endswith("/eda.json") for path in paths))

    def test_cloud_mapping_step_produces_aggregate_request_not_patient_rows(self):
        assets, profile, mapping = self.package(known=False)
        schema = inputs.inspect_inputs(assets, profile=profile)["schema_request"]
        work = self.root / "cloud"
        work.mkdir()
        shutil.copytree(assets, work / "assets")
        folder = research.report_directory(self.root, 2)
        folder.mkdir(parents=True)
        selection = planning.default_selection()
        write_json(folder / "selection.json", selection)
        request = research.parse_issue(issue(), REPO)
        write_json(folder / "request.json", request)
        original_selection = (folder / "selection.json").read_bytes()
        session = {
            "schema_version": 1, "repository": REPO, "pull_request": 7, "issue_number": 2,
            "head_sha": SHA, "phase": "input_mapping", "input_profile": profile,
            "selection_hash": canonical_hash(selection), "report_dir": "research/reports/issue-2",
            "request_hash": canonical_hash(request),
            "policy": planning.load_policy(),
            "catalogs": {stage: planning.load_catalog(stage) for stage in ("eda", "evaluation")},
            "initial_selection_hash": canonical_hash(selection), "applied_feedback_ids": [],
            "code_commit": SHA, "has_coordination": True, "has_input_mapping": True,
            "coordination_schema": {**schema, "feature_columns": ["synthetic_f1", "synthetic_f2"],
                                    "model_ref": IMAGE, "current_bindings": None},
        }
        from app.pr1_intent import build_context
        context = build_context(
            research.parse_issue(issue(), REPO), session["coordination_schema"], selection, [],
            catalogs=session["catalogs"], policy=session["policy"])
        write_json(work / "RESEARCH_CONTEXT.json", context)
        write_json(work / "session.json", session)
        write_json(work / "input-mapping-proposal.json", self.fixture.recommendation(schema, mapping))
        pull = {"head": {"sha": SHA}}
        with patch.object(cloud, "ROOT", self.root), \
                patch.object(cloud, "_runtime", return_value=(research, REPO)), \
                patch.object(cloud, "_pull", return_value=(pull, "research/review-issue-2", 2, "main")):
            with self.assertRaisesRegex(ValueError, "먼저 입력 매핑"):
                cloud.apply_ai(7, work)
            outcome = cloud.apply_input_mapping(7, work)
        self.assertEqual(outcome["status"], "input_mapping_validated")
        self.assertFalse((work / "AI_REQUEST.json").exists())
        self.assertEqual(read_json(work / "session.json")["phase"], "coordination")
        self.assertTrue((folder / "input-mapping.json").is_file())
        write_json(work / "coordination-proposal.json", {
            "schema_version": 1, "context_hash": context["context_hash"], "eda_add": [],
            "objectives": {"eda": [], "evaluation": []}, "feedback_ids": [],
            "selection_updates": {}, "reason": "공개 연구 목적과 입력 계약을 확인해 기본 점검을 수행합니다.",
        })
        with patch.object(cloud, "ROOT", self.root), \
                patch.object(cloud, "_runtime", return_value=(research, REPO)), \
                patch.object(cloud, "_pull", return_value=(pull, "research/review-issue-2", 2, "main")):
            checked = cloud.run_data_checks(7, work)
        self.assertEqual(checked["status"], "data_checks_completed")
        self.assertEqual(read_json(work / "session.json")["phase"], "recommendation")
        request = (work / "AI_REQUEST.json").read_text(encoding="utf-8")
        self.assertNotIn("private-shared-patient", request)
        self.assertNotIn("private-admission", request)
        self.assertEqual((folder / "selection.json").read_bytes(), original_selection)

    def test_registered_model_output_declaration_is_required_for_probability_plan(self):
        assets, profile, _ = self.package(known=True)
        del self.fixture.config["profiles"]["synthetic_a"]["probability_output_planned"]
        self.fixture.save_config()
        context = load_context(assets, input_profile=inputs.load_profile())
        plans = planning.build_plans(report(context), context["data_manifest"],
                                    planning.default_selection(), None)
        self.assertTrue(plans["evaluation"]["planned_inputs"]["y_true"])
        self.assertFalse(plans["evaluation"]["planned_inputs"]["y_probability"])

    def test_corrupt_numeric_input_cannot_pass_eda_after_coordination(self):
        assets, profile, _ = self.package(known=True)
        context = load_context(assets, input_profile=profile)
        original = context["feature_rows"]
        def damaged_rows():
            for row in original():
                row["feature_values"].pop()
                yield row
        context["feature_rows"] = damaged_rows
        aggregate = report(context)
        with self.assertRaisesRegex(ValueError, "damaged"):
            research.validate_aggregate(aggregate, context["data_manifest"]["prepared_source"])

    def approval_bundle(self, *, coordinated=False):
        assets, profile, _ = self.package(known=True)
        context = load_context(assets, input_profile=profile)
        aggregate = report(context)
        candidate = issue()
        candidate["number"] = 2
        request = research.parse_issue(candidate, REPO)
        folder = research.report_directory(self.root, 2)
        folder.mkdir(parents=True)
        selection = planning.default_selection()
        selection["evaluation"].update(primary_metric="roc_auc", add=["roc_auc"])
        write_json(folder / "selection.json", selection)
        model_image = "ghcr.io/fixture-owner/model@sha256:" + "c" * 64
        if coordinated:
            from app.pr1_intent import apply_proposal, build_context
            schema = {**context["_input_schema_request"], "feature_columns": context["feature_columns"],
                      "model_ref": model_image, "current_bindings": context["input_contract"]["bindings"]}
            transient = build_context(request, schema, selection, [])
            intent = apply_proposal(selection, {
                "schema_version": 1, "context_hash": transient["context_hash"], "eda_add": [],
                "objectives": {"eda": [], "evaluation": []}, "feedback_ids": [],
                "selection_updates": {}, "reason": "합성 검증용 목적·입력 조율"}, transient)
            wrapper = {
                "record": intent["record"], "code_commit": SHA, "schema": schema,
                "eda_evidence": {item["block"]: {"status": item["status"], "details": item["details"]}
                                 for item in aggregate["results"]}}
            wrapper["coordination_hash"] = canonical_hash(wrapper)
            context["_pr1_coordination"] = wrapper
            aggregate = report(context)
            write_json(folder / "feedback-applied.json", {
                "schema_version": 1, "repository": REPO, "pull_request": 7, "comment_ids": [],
                "coordination_hash": wrapper["coordination_hash"]})
        initial = planning.build_plans(aggregate, context["data_manifest"], selection, None)
        write_json(folder / "ai-recommendations.json", {
            "schema_version": 1, "provider": "github-copilot-cloud-agent", "status": "provided",
            "context_hash": initial["recommendation_request"]["context_hash"],
            "eda": [{"id": "schema", "reason": "합성 검증용 구조 점검",
                     "evidence": ["data_id"]}], "evaluation": [],
        })
        research.write_payload(self.root, request, aggregate, {
            "data_image": IMAGE, "code_commit": SHA,
            "workflow_run_url": f"https://github.com/{REPO}/actions/runs/1",
        }, context=context)
        files = {name: (folder / name).read_bytes() for name in review.REPORT_FILES}
        for name in ("input-schema.json", "input-mapping.json"):
            files[name] = (folder / name).read_bytes()
        if coordinated:
            for name in ("coordination-plan.json", "feedback-applied.json"):
                files[name] = (folder / name).read_bytes()
        for name in review.CONFIG_FILES:
            files[name] = (research.ROOT / name).read_bytes()
        files["config/assets.json"] = json.dumps({
            "schema_version": 1, "data_image": IMAGE,
            "model_image": model_image,
        }).encode()
        files["config/input-profiles.json"] = self.fixture.config_path.read_bytes()
        pull = {"number": 7, "head": {"sha": "d" * 40},
                "merge_commit_sha": "e" * 40, "merged_by": {"login": "fixture-owner"},
                "merged_at": "2026-10-16T01:00:00Z"}
        return files, pull

    def test_generic_approval_freezes_validated_mapping_and_unknown_history(self):
        files, pull = self.approval_bundle()
        approved = review._snapshot(REPO, pull, 2, files)
        self.assertEqual(approved["input_contract"]["status"], "validated")
        self.assertIsNone(approved["limitations"]["validation_development_reuse"])
        self.assertEqual(approved["limitations"]["patient_overlap"]["overlaps"]["train_test"], 1)
        self.assertFalse(approved["clinical_use_approved"])
        self.assertNotIn("private-admission", json.dumps(approved))
        self.assertNotIn("private-shared-patient", json.dumps(approved))

    def test_approval_rejects_mapping_tamper_and_stale_mapping_hash(self):
        files, pull = self.approval_bundle()
        changed = dict(files)
        binding = json.loads(changed["input-mapping.json"])
        binding["bindings"]["patient_key"] = binding["bindings"]["row_key"]
        changed["input-mapping.json"] = json.dumps(binding).encode()
        with self.assertRaises(ValueError):
            review._snapshot(REPO, pull, 2, changed)
        changed = dict(files)
        eda = json.loads(changed["eda.json"])
        eda["input_mapping_hash"] = "f" * 64
        changed["eda.json"] = json.dumps(eda).encode()
        with self.assertRaisesRegex(ValueError, "data_id/hash"):
            review._snapshot(REPO, pull, 2, changed)

    def test_approval_freezes_coordination_and_rejects_changed_goal_or_audit(self):
        files, pull = self.approval_bundle(coordinated=True)
        approved = review._snapshot(REPO, pull, 2, files)
        self.assertEqual(approved["coordination"]["schema"]["model_ref"], approved["model_image"])
        for name in ("coordination-plan.json", "feedback-applied.json"):
            changed = {key: value for key, value in files.items() if key != name}
            with self.subTest(missing=name), self.assertRaises(ValueError):
                review._snapshot(REPO, pull, 2, changed)
        changed = dict(files)
        request = json.loads(files["request.json"])
        request["research_goal"] = "조율 이후 바뀐 합성 목적"
        changed["request.json"] = json.dumps(request).encode()
        with self.assertRaises(ValueError):
            review._snapshot(REPO, pull, 2, changed)
        changed = dict(files)
        wrapper = json.loads(files["coordination-plan.json"])
        wrapper["record"]["reason"] = "변경된 조율 기록"
        changed["coordination-plan.json"] = json.dumps(wrapper).encode()
        with self.assertRaises(ValueError):
            review._snapshot(REPO, pull, 2, changed)

    def test_pending_natural_feedback_blocks_freezing_even_legacy_plan(self):
        files, pull = self.approval_bundle()
        files["feedback-requests.json"] = json.dumps({
            "schema_version": 1, "kind": "pr1-natural-feedback", "repository": REPO,
            "pull_request": 7, "issue_number": 2, "comments": [{
                "comment_id": 17, "actor": "fixture-owner", "body_sha256": "f" * 64}]}).encode()
        with self.assertRaisesRegex(ValueError, "미반영"):
            review._snapshot(REPO, pull, 2, files)


if __name__ == "__main__":
    unittest.main()
