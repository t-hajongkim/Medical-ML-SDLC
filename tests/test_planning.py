import ast
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from app import planning_eval as planning


def fixture_catalogs():
    def item(identifier, directory, inputs=(), parameters=(), context=(), aliases=()):
        return {
            "id": identifier, "name": identifier, "description": "Registered synthetic definition.",
            "module": f"app.blocks.{directory}.{identifier}", "aliases": list(aliases),
            "requires": list(inputs), "requires_parameters": list(parameters),
            "requires_context": list(context),
        }
    checks = [item(identifier, "checks") for identifier in (
        "schema", "patient_overlap", "demographics", "cohort", "prediction_timing",
        "label_observability", "preprocessing_lineage")]
    checks[0]["aliases"] = ["numeric-contract"]
    metrics = [
        item("roc_auc", "metrics", ("y_true", "y_probability"), aliases=("auroc",)),
        item("average_precision", "metrics", ("y_true", "y_probability"), aliases=("ap",)),
        item("brier_score", "metrics", ("y_true", "y_probability"), aliases=("brier",)),
        item("calibration_curve", "metrics", ("y_true", "y_probability")),
        item("recall", "metrics", ("y_true", "y_probability"), ("threshold",),
             aliases=("sensitivity",)),
        item("decision_curve", "metrics", ("y_true", "y_probability"), ("thresholds",),
             ("clinical_action",), ("dca",)),
        item("categorical_nri", "metrics",
             ("y_true", "y_probability", "reference_probabilities", "sample_ids",
              "reference_sample_ids"), ("risk_thresholds",), ("comparison_model",), ("nri",)),
        item("recall_at_k", "metrics", ("y_true", "y_probability"), ("top_fraction",)),
    ]
    return {
        "eda": {"schema_version": 1, "stage": "eda", "checks": checks},
        "evaluation": {"schema_version": 1, "stage": "evaluation", "metrics": metrics},
    }


def fixture_manifest():
    return {
        "data_id": "a" * 64,
        "feature_columns": ["synthetic_scaled"],
        "split_rows": {"train": 4, "validation": 2, "test": 2},
        "files": {"metadata.csv": "b" * 64},
        "prepared_source": {
            "target": "target", "target_mapping": {"negative": 0, "positive": 1},
            "features": ["synthetic_scaled"],
            "model": {
                "classes": [0, 1], "positive_class": 1,
                "local_validation_scikit_learn_version": "1.6.1",
                "checkpoint": {"sha256": "c" * 64},
            },
        },
    }


def fixture_eda():
    return {"results": [
        {"block": "schema", "status": "unverifiable", "details": {
            "numeric_contract": "passed", "actual_count": 1, "expected_count": 1,
            "rows": 8, "duplicate_columns": 0, "schema_match": True}},
        {"block": "patient_overlap", "status": "failed", "details": {
            "overlaps": {"train_validation": 1, "train_test": 1, "validation_test": 0}}},
    ]}


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.catalogs = fixture_catalogs()
        self.policy = planning.load_policy()
        self.selection = planning.default_selection()
        self.eda = fixture_eda()
        self.manifest = fixture_manifest()

    def build(self, ai=None):
        return planning.build_plans(
            self.eda, self.manifest, self.selection, ai,
            catalogs=self.catalogs, policy=self.policy)

    def recommendation(self, eda=None, evaluation=None):
        return {
            "schema_version": 1, "provider": "github-copilot-cloud-agent",
            "context_hash": self.build()["recommendation_request"]["context_hash"],
            "status": "provided", "eda": eda or [], "evaluation": evaluation or [],
        }

    def recommended(self, identifier, reason="Consider this registered diagnostic."):
        return {"id": identifier, "reason": reason, "evidence": ["data_id", "objectives.eda"]}

    def test_default_selection_contract_returns_independent_objects(self):
        self.assertEqual(self.selection, {
            "schema_version": 1,
            "eda": {"objectives": ["data_quality", "population_description", "prediction_readiness"],
                    "add": [], "exclude": []},
            "evaluation": {"objectives": ["discrimination", "probability_quality"],
                           "add": [], "exclude": [], "primary_metric": None,
                           "threshold": None, "parameters": {}, "clinical_action": None,
                           "comparison_model": None},
        })
        self.selection["eda"]["add"].append("prediction_timing")
        self.assertEqual(planning.default_selection()["eda"]["add"], [])

    def test_missing_ai_produces_pending_drafts_and_cloud_agent_request(self):
        result = self.build()
        for stage in ("eda", "evaluation"):
            self.assertEqual(result[stage]["ai_status"], "pending")
            self.assertFalse(result[stage]["planning_complete"])
            self.assertTrue(result[stage]["approval_required"])
            self.assertTrue(result[stage]["draft"])
            self.assertFalse(result[stage]["clinical_use_approved"])
        request = result["recommendation_request"]
        self.assertEqual(request["provider"], "github-copilot-cloud-agent")
        self.assertRegex(request["context_hash"], r"^[a-f0-9]{64}$")
        self.assertFalse(request["authorship_verified"])
        self.assertIn("추천 파일을 자동 작성", request["instructions"])
        self.assertIn("GitHub Agentic Workflows의 Copilot cloud agent", request["instructions"])
        self.assertIn("워크플로가 지정한 JSON 파일", request["instructions"])

    def test_policy_not_code_controls_defaults(self):
        self.policy["eda"]["defaults"] = ["cohort"]
        self.policy["evaluation"]["defaults"] = ["brier_score"]
        result = self.build()
        self.assertEqual(result["eda"]["selected_ids"], ["schema", "patient_overlap", "cohort"])
        self.assertEqual(result["evaluation"]["selected_ids"], ["brier_score"])

    def test_user_choices_change_composition_and_defaults_are_excludable(self):
        original = self.build()
        self.selection["eda"].update(add=["prediction_timing"], exclude=["demographics"])
        self.selection["evaluation"].update(add=["recall"], exclude=["brier"])
        result = self.build()
        self.assertNotEqual(original["eda"]["selected_ids"], result["eda"]["selected_ids"])
        self.assertIn("prediction_timing", result["eda"]["selected_ids"])
        self.assertNotIn("demographics", result["eda"]["selected_ids"])
        self.assertIn("recall", result["evaluation"]["selected_ids"])
        self.assertNotIn("brier_score", result["evaluation"]["selected_ids"])

    def test_union_normalizes_aliases_tracks_all_sources_then_excludes(self):
        self.selection["eda"].update(add=["cohort", "Cohort", "prediction-timing"],
                                     exclude=["DEMOGRAPHICS"])
        self.selection["evaluation"]["add"] = ["AUROC", "roc-auc", "ap"]
        ai = self.recommendation(
            eda=[self.recommended("cohort"), self.recommended("demographics")],
            evaluation=[self.recommended("AUROC"), self.recommended("roc-auc")])
        result = self.build(ai)
        cohort = next(item for item in result["eda"]["items"] if item["id"] == "cohort")
        roc = next(item for item in result["evaluation"]["items"] if item["id"] == "roc_auc")
        self.assertEqual(cohort["sources"], ["default", "ai", "user"])
        self.assertEqual(roc["sources"], ["default", "ai", "user"])
        self.assertEqual(result["evaluation"]["selected_ids"].count("roc_auc"), 1)
        self.assertEqual(result["evaluation"]["ai_recommended_ids"], ["roc_auc"])
        self.assertIn("demographics", result["eda"]["ai_recommended_ids"])
        self.assertNotIn("demographics", result["eda"]["selected_ids"])
        self.assertEqual(cohort["evidence"], ["data_id", "objectives.eda"])

    def test_required_blocks_cannot_be_excluded_even_by_alias(self):
        for identifier in ("schema", "numeric-contract", "patient_overlap"):
            with self.subTest(identifier=identifier):
                self.selection["eda"]["exclude"] = [identifier]
                with self.assertRaisesRegex(ValueError, "required"):
                    self.build()

    def test_unknown_and_cross_stage_ids_are_errors(self):
        for stage, identifier in (("eda", "not_registered"), ("eda", "roc_auc"),
                                  ("evaluation", "schema")):
            with self.subTest(stage=stage, identifier=identifier):
                self.selection = planning.default_selection()
                self.selection[stage]["add"] = [identifier]
                with self.assertRaisesRegex(ValueError, "Unknown"):
                    self.build()
        self.selection = planning.default_selection()
        self.selection["evaluation"]["exclude"] = ["not_registered"]
        with self.assertRaisesRegex(ValueError, "Unknown"):
            self.build()

    def test_eda_diagnostics_stay_selectable_without_timing_metadata(self):
        self.selection["eda"]["add"] = ["prediction_timing", "label_observability"]
        result = self.build()
        timing = next(item for item in result["eda"]["items"] if item["id"] == "prediction_timing")
        self.assertEqual(timing["state"], "selected_for_diagnosis")
        self.assertEqual(timing["blocked_reasons"], [])
        self.assertIn("확인 불가", timing["reason"])

    def test_planned_labels_and_probability_outputs_do_not_need_arrays_yet(self):
        result = self.build()
        self.assertNotIn("y_probability", self.manifest)
        self.assertTrue(result["evaluation"]["planned_inputs"]["y_probability"])
        for item in result["evaluation"]["items"]:
            self.assertEqual(item["state"], "eligible_planned_inputs")
            self.assertIn("아직 계산하지 않았습니다", item["reason"])
        self.assertIn("이 데이터셋에서 확인한", result["evaluation"]["interpretation"])
        self.assertNotIn("validation 재사용", result["evaluation"]["interpretation"])

    def test_known_diabetes_source_keeps_its_documented_validation_limit(self):
        self.manifest["prepared_source"]["dataset"] = "auphong2707/hospital-readmission-risk-data"
        result = self.build()
        self.assertIn("validation 재사용", result["evaluation"]["interpretation"])

    def test_unverified_probability_capability_is_blocked(self):
        self.manifest["prepared_source"]["model"].pop("local_validation_scikit_learn_version")
        result = self.build()
        self.assertTrue(all(item["state"] == "blocked" for item in result["evaluation"]["items"]))
        self.assertIn("y_probability", result["evaluation"]["items"][0]["reason"])

    def test_missing_labels_are_blocked(self):
        self.manifest["prepared_source"].pop("target_mapping")
        result = self.build()
        self.assertIn("y_true", result["evaluation"]["items"][0]["reason"])

    def test_threshold_dca_nri_and_topk_preserve_specific_blocking_reasons(self):
        self.selection["evaluation"]["add"] = ["recall", "dca", "nri", "recall_at_k"]
        items = {item["id"]: item for item in self.build()["evaluation"]["items"]}
        for identifier in ("recall", "decision_curve", "categorical_nri", "recall_at_k"):
            self.assertEqual(items[identifier]["state"], "blocked")
        self.assertIn("threshold", items["recall"]["reason"])
        self.assertIn("clinical_action", items["decision_curve"]["reason"])
        self.assertIn("comparison_model", items["categorical_nri"]["reason"])
        self.assertIn("top_fraction", items["recall_at_k"]["reason"])
        self.selection["evaluation"].update(
            threshold=0.2, clinical_action="Synthetic action definition.",
            comparison_model="Synthetic registered reference model.",
            parameters={"thresholds": [0.1, 0.2], "risk_thresholds": [0.1, 0.3],
                        "top_fraction": 0.1})
        items = self.build()["evaluation"]["items"]
        self.assertTrue(all(item["state"] == "eligible_planned_inputs" for item in items))
        self.assertFalse(self.build()["evaluation"]["clinical_use_approved"])

    def test_primary_metric_must_be_selected_and_alias_is_canonicalized(self):
        self.selection["evaluation"]["primary_metric"] = "AUROC"
        self.assertEqual(self.build()["evaluation"]["primary_metric"], "roc_auc")
        self.selection["evaluation"]["exclude"] = ["roc_auc"]
        with self.assertRaisesRegex(ValueError, "primary_metric"):
            self.build()
        self.selection["evaluation"]["primary_metric"] = "not_registered"
        with self.assertRaisesRegex(ValueError, "Unknown"):
            self.build()

    def test_parameters_are_validated_as_data_only(self):
        invalid = [
            {"threshold": True}, {"threshold": float("nan")}, {"threshold": 1.1},
            {"threshold": 10 ** 1000},
            {"thresholds": []}, {"thresholds": [0.2, 0.2]}, {"thresholds": [0.3, 0.1]},
            {"thresholds": [0, 0.2]}, {"risk_thresholds": [0.2, 1]},
            {"top_fraction": 0}, {"top_fraction": -0.1}, {"n_bins": 0},
            {"n_bins": True}, {"code": "print('never executed')"},
            {"Threshold": 0.2, "threshold": 0.2},
        ]
        for parameters in invalid:
            with self.subTest(parameters=parameters):
                self.selection["evaluation"]["parameters"] = parameters
                with self.assertRaises(ValueError):
                    self.build()
        self.selection["evaluation"].update(threshold=0.2, parameters={"threshold": 0.2})
        with self.assertRaisesRegex(ValueError, "Duplicate threshold"):
            self.build()

    def test_threshold_zero_and_one_are_valid_but_no_default_is_invented(self):
        self.assertIsNone(self.build()["evaluation"]["threshold"])
        for threshold in (0, 1):
            self.selection["evaluation"]["threshold"] = threshold
            self.assertEqual(self.build()["evaluation"]["threshold"], threshold)

    def test_ai_absence_is_not_replaced_with_rule_based_ai(self):
        for stage in ("eda", "evaluation"):
            plan = self.build()[stage]
            self.assertEqual(plan["ai_recommended_ids"], [])
            self.assertTrue(all("ai" not in item["sources"] for item in plan["items"]))

    def test_empty_placeholder_is_not_successful_ai(self):
        with self.assertRaisesRegex(ValueError, "placeholder"):
            self.build(self.recommendation())

    def test_valid_recommendation_completes_planning_but_not_approval(self):
        result = self.build(self.recommendation(eda=[self.recommended("prediction_timing")]))
        for stage in ("eda", "evaluation"):
            self.assertEqual(result[stage]["ai_status"], "provided")
            self.assertTrue(result[stage]["planning_complete"])
            self.assertTrue(result[stage]["approval_required"])
            self.assertFalse(result[stage]["draft"])

    def test_stale_recommendation_is_explicit_incomplete_default_user_draft(self):
        self.selection["eda"]["add"] = ["label_observability"]
        ai = self.recommendation(eda=[self.recommended("prediction_timing")])
        ai["context_hash"] = "f" * 64
        result = self.build(ai)
        self.assertEqual(result["eda"]["ai_status"], "stale")
        self.assertFalse(result["eda"]["planning_complete"])
        self.assertIn("label_observability", result["eda"]["selected_ids"])
        self.assertNotIn("prediction_timing", result["eda"]["selected_ids"])
        self.assertEqual(result["eda"]["ai_recommended_ids"], [])

    def test_ai_structure_and_unauthorized_fields_are_rejected(self):
        valid = self.recommendation()
        invalid = [
            [], "not JSON", {},
            {**valid, "status": "pending"}, {**valid, "status": "completed"},
            {**valid, "provider": "github-models"}, {**valid, "provider": "github-copilot-web"},
            {**valid, "schema_version": True}, {**valid, "eda": {}},
            {**valid, "context_hash": "not-a-hash"}, {**valid, "primary_metric": "roc_auc"},
            {**valid, "threshold": 0.5}, {**valid, "code": "pass"},
            {key: value for key, value in valid.items() if key != "evaluation"},
        ]
        for ai in invalid:
            with self.subTest(ai=ai):
                with self.assertRaises(ValueError):
                    self.build(ai)

    def test_ai_ids_reasons_evidence_and_stage_are_strict(self):
        valid = self.recommended("cohort")
        invalid = [
            {**valid, "id": "not_registered"}, {**valid, "id": "roc_auc"},
            {**valid, "reason": ""}, {**valid, "reason": " "},
            {**valid, "evidence": []}, {**valid, "evidence": ["patient_key"]},
            {**valid, "evidence": "data_id"}, {**valid, "threshold": 0.2},
            {key: value for key, value in valid.items() if key != "reason"},
        ]
        for item in invalid:
            with self.subTest(item=item):
                with self.assertRaises(ValueError):
                    self.build(self.recommendation(eda=[item]))

    def test_stale_invalid_ai_is_still_rejected(self):
        ai = self.recommendation(eda=[self.recommended("not_registered")])
        ai["context_hash"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "Unknown"):
            self.build(ai)

    def test_ai_duplicate_aliases_merge_reasons_and_evidence(self):
        first = self.recommended("AUROC", "Ranking objective.")
        second = self.recommended("roc_auc", "Planned outputs.")
        second["evidence"] = ["probability_output_planned", "data_id"]
        result = self.build(self.recommendation(evaluation=[first, second]))
        item = next(item for item in result["evaluation"]["items"] if item["id"] == "roc_auc")
        self.assertIn("Ranking objective.", item["reason"])
        self.assertIn("Planned outputs.", item["reason"])
        self.assertEqual(item["evidence"], ["data_id", "objectives.eda", "probability_output_planned"])

    def test_context_does_not_contain_free_text_rows_identifiers_or_artifacts(self):
        secret = "SYNTHETIC_PRIVATE_CONTENT_DO_NOT_EXPORT"
        self.eda["rawIssue"] = secret
        self.eda["metadata"] = [{"patient_key": secret, "row_key": secret}]
        self.eda["results"][0]["details"].update(
            reason=secret, feature_columns=[secret], quality={secret: {"min": 1}})
        self.eda["results"][1]["details"]["patient_identifiers"] = [secret]
        self.manifest.update(model_binary=secret, token=secret, code=secret, patient_key=secret)
        self.manifest["prepared_source"]["model"]["checkpoint"]["binary"] = secret
        self.selection["evaluation"].update(clinical_action=secret, comparison_model=secret)
        serialized = json.dumps(self.build()["recommendation_request"])
        self.assertNotIn(secret, serialized)
        self.assertNotIn("patient_key", serialized)
        facts = self.build()["recommendation_request"]["context"]["facts"]
        self.assertTrue(facts["clinical_action_defined"])
        self.assertTrue(facts["comparison_model_defined"])

    def test_only_structured_objectives_can_enter_public_context(self):
        self.selection["eda"]["objectives"] = ["arbitrary private free text"]
        with self.assertRaisesRegex(ValueError, "objective"):
            self.build()

    def test_optional_results_and_user_selection_do_not_change_context_hash(self):
        before = self.build()["recommendation_request"]["context_hash"]
        self.eda["results"].append({
            "block": "prediction_timing", "status": "failed",
            "details": {"private_note": "Synthetic private free text"}})
        self.selection["eda"].update(add=["prediction_timing"], exclude=["demographics"])
        self.selection["evaluation"].update(add=["recall"], exclude=["brier_score"],
                                             primary_metric="ap")
        after = self.build()["recommendation_request"]["context_hash"]
        self.assertEqual(before, after)
        ai = self.recommendation(eda=[self.recommended("prediction_timing")])
        self.eda["results"][-1]["status"] = "unverifiable"
        self.assertEqual(self.build(ai)["eda"]["ai_status"], "provided")

    def test_required_facts_objectives_catalogs_and_conditions_change_hash(self):
        original = self.build()["recommendation_request"]["context_hash"]
        changes = [
            lambda: self.eda["results"][0]["details"].update(numeric_contract="failed"),
            lambda: self.selection["eda"].update(objectives=["data_quality"]),
            lambda: self.catalogs["eda"]["checks"][0].update(description="Changed public definition."),
            lambda: self.selection["evaluation"].update(clinical_action="Synthetic action."),
            lambda: self.manifest.update(data_id="d" * 64),
        ]
        for change in changes:
            self.setUp()
            change()
            self.assertNotEqual(original, self.build()["recommendation_request"]["context_hash"])

    def test_context_is_stable_for_changed_clinical_free_text(self):
        self.selection["evaluation"]["clinical_action"] = "Synthetic action one."
        before = self.build()["recommendation_request"]["context_hash"]
        self.selection["evaluation"]["clinical_action"] = "Synthetic action two."
        self.assertEqual(before, self.build()["recommendation_request"]["context_hash"])

    def test_catalog_rejects_arbitrary_import_missing_files_and_alias_collisions(self):
        for replacement in ("os.system", "app.blocks.checks.unknown"):
            self.catalogs = fixture_catalogs()
            self.catalogs["eda"]["checks"][0]["module"] = replacement
            with self.assertRaisesRegex(ValueError, "implementation"):
                self.build()
        self.catalogs = fixture_catalogs()
        self.catalogs["eda"]["checks"][0].update(
            id="missing_implementation", module="app.blocks.checks.missing_implementation")
        with self.assertRaisesRegex(ValueError, "implementation"):
            self.build()
        self.catalogs = fixture_catalogs()
        self.catalogs["eda"]["checks"][1]["aliases"] = ["schema"]
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            self.build()

    def test_registered_catalogs_and_policy_are_connected(self):
        eda = planning.load_catalog("eda")
        evaluation = planning.load_catalog("evaluation")
        self.assertEqual(len(eda["checks"]), 7)
        self.assertEqual(len(evaluation["metrics"]), 15)
        result = planning.build_plans(
            self.eda, self.manifest, planning.default_selection(), None)
        self.assertEqual(result["eda"]["selected_ids"],
                         ["schema", "patient_overlap", "demographics", "cohort"])
        self.assertEqual(result["evaluation"]["selected_ids"],
                         ["roc_auc", "average_precision", "brier_score", "calibration_curve"])

    def test_frozen_prepared_source_supports_future_probability_planning(self):
        source = json.loads(
            (planning.ROOT / "config" / "prepared-source.json").read_text(encoding="utf-8"))
        self.manifest.update(
            prepared_source=source, feature_columns=list(source["features"]),
            split_rows={split: value["rows"] for split, value in source["splits"].items()})
        result = self.build()
        self.assertEqual(len(source["features"]), 113)
        self.assertTrue(result["evaluation"]["planned_inputs"]["y_true"])
        self.assertTrue(result["evaluation"]["planned_inputs"]["y_probability"])
        self.assertTrue(all(item["state"] == "eligible_planned_inputs"
                            for item in result["evaluation"]["items"]))

    def test_eda_and_manifest_must_describe_the_same_data(self):
        self.eda["data_id"] = "e" * 64
        with self.assertRaisesRegex(ValueError, "different data_id"):
            self.build()

    def test_load_catalog_unknown_stage_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "stage"):
            planning.load_catalog("training")

    def test_catalog_schema_and_duplicate_ids_are_validated(self):
        invalid = copy.deepcopy(self.catalogs["eda"])
        invalid["schema_version"] = True
        with self.assertRaisesRegex(ValueError, "schema_version"):
            planning._catalog(invalid, "eda")
        invalid = copy.deepcopy(self.catalogs["eda"])
        invalid["checks"].append(copy.deepcopy(invalid["checks"][0]))
        with self.assertRaisesRegex(ValueError, "unique"):
            planning._catalog(invalid, "eda")
        invalid = copy.deepcopy(self.catalogs["eda"])
        invalid["checks"][0].pop("requires")
        with self.assertRaisesRegex(ValueError, "missing"):
            planning._catalog(invalid, "eda")
        invalid = copy.deepcopy(self.catalogs["evaluation"])
        invalid["metrics"][0]["requires"] = ["load_model"]
        with self.assertRaisesRegex(ValueError, "unsupported input"):
            planning._catalog(invalid, "evaluation")

    def test_file_configuration_rejects_duplicate_json_keys(self):
        duplicate = '{"schema_version":1,"schema_version":1}'
        with patch.object(Path, "read_text", return_value=duplicate):
            with self.assertRaisesRegex(ValueError, "duplicate"):
                planning.load_policy()

    def test_catalog_aliases_are_normalized_and_deduplicated(self):
        self.catalogs["evaluation"]["metrics"][0]["aliases"] = ["AUROC", "auroc", "ROC-AUC"]
        catalog = planning._catalog(self.catalogs["evaluation"], "evaluation")
        self.assertEqual(catalog["metrics"][0]["aliases"], ["auroc"])

    def test_snapshots_are_used_without_reading_default_branch_files_again(self):
        with patch.object(planning, "load_catalog", side_effect=AssertionError("unexpected reload")), \
                patch.object(planning, "load_policy", side_effect=AssertionError("unexpected reload")):
            self.build()

    def test_inputs_are_not_mutated(self):
        originals = copy.deepcopy((self.catalogs, self.policy, self.selection,
                                   self.eda, self.manifest))
        self.build()
        self.assertEqual(originals, (self.catalogs, self.policy, self.selection,
                                     self.eda, self.manifest))

    def test_duplicate_required_results_are_ambiguous(self):
        self.eda["results"].append(copy.deepcopy(self.eda["results"][0]))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.build()

    def test_metadata_only_source_has_no_math_library_or_model_execution(self):
        tree = ast.parse(Path(planning.__file__).read_text(encoding="utf-8"))
        forbidden = {"numpy", "sklearn", "pandas", "joblib", "pickle", "importlib"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.assertFalse(any(alias.name.split(".")[0] in forbidden for alias in node.names))
            if isinstance(node, ast.ImportFrom):
                self.assertNotIn((node.module or "").split(".")[0], forbidden)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, {"eval", "exec", "__import__", "compile"})
        completed = subprocess.run(
            [sys.executable, "-B", "-c",
             "import sys; from app.planning_eval import load_catalog, load_policy; "
             "load_catalog('eda'); load_catalog('evaluation'); load_policy(); "
             "assert not any(k.split('.')[0] in {'numpy','sklearn','pandas','joblib'} "
             "for k in sys.modules)"],
            cwd=planning.ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
