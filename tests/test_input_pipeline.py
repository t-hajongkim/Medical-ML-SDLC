"""Synthetic registered packages exercise mapping-before-EDA integration."""

from copy import deepcopy
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app import research as cloud
from app import input_contracts as inputs
from app import planning_eval as planning, research
from app.asset_contract import canonical_hash, read_json, write_json
from app.executor_eda import InputMappingRequired, load_context, report
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

    def test_unmapped_source_is_handed_off_without_preanalysis_pr(self):
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
                patch.object(research, "write_payload", return_value=pending) as publish, \
                patch.dict(os.environ, {"GITHUB_RUN_ID": "123"}):
            outcome = research.run_workflow(
                {}, "issues", REPO, "fixture-owner", "main", work)
        self.assertEqual(outcome["ai_dispatch"]["trigger"], "workflow_run")
        self.assertEqual(outcome["ai_dispatch"]["target_kind"], "issue")
        self.assertFalse(outcome["pull_request_created"])
        publish.assert_not_called()
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
        request["issue_number"] = 2
        request["issue_url"] = f"https://github.com/{REPO}/issues/2"
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
            "target_kind": "pull_request", "target_number": 7,
            "head_branch": "research/review-issue-2", "default_branch": "main",
            "data_image": IMAGE, "model_image": IMAGE,
            "coordination_schema": {**schema, "feature_columns": ["synthetic_f1", "synthetic_f2"],
                                    "model_ref": IMAGE, "current_bindings": None},
        }
        from app.planning_eda import build_context
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
            with self.assertRaisesRegex(ValueError, "최종 EDA 이후"):
                cloud.apply_ai(7, work)
            outcome = cloud.apply_input_mapping(7, work)
        self.assertEqual(outcome["status"], "input_mapping_validated")
        self.assertFalse((work / "AI_REQUEST.json").exists())
        self.assertEqual(read_json(work / "session.json")["phase"], "coordination")
        self.assertTrue((folder / "input-mapping.json").is_file())
        context = read_json(work / "RESEARCH_CONTEXT.json")
        write_json(work / "coordination-proposal.json", {
            "schema_version": 1, "context_hash": context["context_hash"], "eda_add": [],
            "objectives": {"eda": [], "evaluation": []}, "feedback_ids": [],
            "selection_updates": {}, "reason": "공개 연구 목적과 입력 계약을 확인해 기본 점검을 수행합니다.",
        })
        with patch.object(cloud, "ROOT", self.root), \
                patch.object(cloud, "_runtime", return_value=(research, REPO)), \
                patch.object(cloud, "_pull", return_value=(pull, "research/review-issue-2", 2, "main")):
            planned = cloud.plan_eda(7, work)
            checked = cloud.execute_eda(7, work, planned["plan_hash"])
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


if __name__ == "__main__":
    unittest.main()
