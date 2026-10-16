import ast
import copy
import csv
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from app.blocks.catalog import CHECKS, run_checks
from app.executor_eda import feature_rows, load_context, raw_evidence, report
from app.prepared import FEATURE_COLUMNS, METADATA_COLUMNS, SPLITS


def fixture():
    records = []
    vectors = []
    for split_index, split in enumerate(SPLITS):
        for position in (1, 2):
            key = f"synthetic-encounter-{split_index}-{position}"
            records.append({
                "row_key": key, "patient_key": f"synthetic-patient-{split_index}",
                "split": split, "target": str(position % 2), "race": "Caucasian",
                "gender": "synthetic-gender", "age_group": "synthetic-age",
                "race_original": "" if split == "train" and position == 1 else "Caucasian",
                "race_was_missing": "true" if split == "train" and position == 1 else "false",
            })
            vectors.append({"row_key": key, "split": split, "row_position": str(position),
                            "feature_values": [position * -0.25, 1.0]})
    return {
        "metadata": records, "feature_rows": vectors,
        "feature_columns": ["synthetic_scaled", "synthetic_constant"],
        "expected_feature_columns": ["synthetic_scaled", "synthetic_constant"],
        "target_mapping": {"<30": 1, ">30": 0, "NO": 0},
    }


def csv_text(columns, rows):
    handle = io.StringIO()
    writer = csv.DictWriter(handle, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)
    return handle.getvalue()


class EDABlockTests(unittest.TestCase):
    def setUp(self):
        self.context = fixture()

    def run_block(self, name):
        return run_checks([name], self.context)[0]

    def test_catalog_registers_exactly_seven_blocks(self):
        self.assertEqual(set(CHECKS), {
            "patient_overlap", "demographics", "schema", "cohort",
            "prediction_timing", "label_observability", "preprocessing_lineage",
        })
        self.assertEqual(len(report(self.context)["results"]), 7)

    def test_stage_two_code_has_no_model_execution_or_dynamic_text_execution(self):
        root = Path(__file__).resolve().parents[1]
        paths = [root / "app" / "executor_eda.py", root / "app" / "planning_eda.py",
                 root / "app" / "blocks" / "catalog.py"]
        paths.extend((root / "app" / "blocks" / "checks").glob("*.py"))
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            with self.subTest(file=path.name):
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call):
                        if isinstance(node.func, ast.Name):
                            self.assertNotIn(node.func.id, ("eval", "exec", "compile", "prepare_model"))
                        if isinstance(node.func, ast.Attribute):
                            self.assertNotIn(node.func.attr, ("fit", "fit_transform", "predict", "predict_proba"))
                    if isinstance(node, ast.Import):
                        self.assertTrue(all(alias.name.split(".")[0] not in (
                            "sklearn", "joblib", "pickle", "numpy", "pandas") for alias in node.names))
                    if isinstance(node, ast.ImportFrom):
                        self.assertNotIn(node.module, ("app.assets", "app.infer", "app.bootstrap",
                                                     "pickle", "joblib", "sklearn"))

    def test_unknown_identifier_fails_before_any_execution(self):
        with self.assertRaisesRegex(ValueError, "Unknown check blocks"):
            run_checks(["patient_overlap", "not-implemented"], self.context)

    def test_canonical_validation_split(self):
        overlap = self.run_block("patient_overlap")
        self.assertEqual(overlap["status"], "passed")
        self.assertEqual(overlap["details"]["patients"], dict.fromkeys(SPLITS, 1))
        self.assertEqual(set(overlap["details"]["overlaps"]), {
            "train_validation", "train_test", "validation_test"})
        demo = self.run_block("demographics")
        self.assertEqual(demo["status"], "completed")
        self.assertEqual(demo["details"]["distributions"]["validation"]["gender"]
                         ["synthetic-gender"]["admissions"], 2)

    def test_valid_alias_is_explicit_and_output_groups_are_canonical(self):
        for row in self.context["metadata"] + self.context["feature_rows"]:
            if row["split"] == "validation":
                row["split"] = "valid"
        results = run_checks(["patient_overlap", "demographics", "schema", "cohort",
                              "label_observability"], self.context)
        for item in results:
            self.assertEqual(item["details"]["split_aliases"], {"valid": "validation"})
        self.assertNotIn("valid", results[0]["details"]["patients"])
        self.assertEqual(results[0]["details"]["overlaps"]["train_valid"], 0)
        self.assertIn("deprecated_overlap_aliases", results[0]["details"])
        self.assertNotIn("valid", results[1]["details"]["distributions"])
        self.assertEqual(results[2]["details"]["numeric_contract"], "passed")

    def test_unknown_split_is_not_silently_accepted(self):
        self.context["metadata"][0]["split"] = "development"
        for name in ("patient_overlap", "demographics", "schema", "cohort", "label_observability"):
            with self.subTest(block=name):
                self.assertEqual(self.run_block(name)["status"], "failed")

    def test_missing_patient_key_is_unverifiable(self):
        self.context["metadata"][0].pop("patient_key")
        for name in ("patient_overlap", "demographics", "cohort", "label_observability"):
            self.assertEqual(self.run_block(name)["status"], "unverifiable")

    def test_malformed_keys_are_failed_without_disclosing_input(self):
        for key in ("row_key", "patient_key"):
            for value in (123, ["synthetic"], " "):
                self.context = fixture()
                self.context["metadata"][0][key] = value
                with self.subTest(key=key, value=value):
                    self.assertEqual(self.run_block("cohort")["status"], "failed")

    def test_missing_split_is_unverifiable(self):
        self.context["metadata"][0].pop("split")
        self.assertEqual(self.run_block("patient_overlap")["status"], "unverifiable")

    def test_zero_rows_cannot_complete_or_pass(self):
        self.context["metadata"] = []
        self.context["feature_rows"] = []
        for name in CHECKS:
            self.assertEqual(self.run_block(name)["status"], "unverifiable")

    def test_absent_split_does_not_pass(self):
        self.context["metadata"] = self.context["metadata"][:2]
        overlap = self.run_block("patient_overlap")
        demo = self.run_block("demographics")
        self.assertEqual(overlap["status"], "unverifiable")
        self.assertEqual(demo["status"], "unverifiable")
        self.assertEqual(demo["details"]["distributions"]["validation"]["race"], {})
        cohort = self.run_block("cohort")
        self.assertIsNone(cohort["details"]["counts"]["test"]["event_rate"])

    def test_known_overlap_remains_failed(self):
        self.context["metadata"][2]["patient_key"] = "synthetic-patient-0"
        item = self.run_block("patient_overlap")
        self.assertEqual(item["status"], "failed")
        self.assertEqual(item["details"]["overlaps"]["train_validation"], 1)

    def test_repeated_patient_admissions_are_not_duplicate_encounters(self):
        item = self.run_block("cohort")
        self.assertEqual(item["details"]["total_admissions"], 6)
        self.assertEqual(item["details"]["unique_patients"], 3)
        self.assertEqual(item["details"]["duplicate_admission_keys"], 0)
        self.assertEqual(item["details"]["counts"]["train"]["patients"], 1)
        self.assertEqual(item["details"]["counts"]["train"]["admissions"], 2)
        self.assertEqual(self.run_block("patient_overlap")["status"], "passed")

    def test_duplicate_admission_keys_fail_cohort_and_demographics(self):
        self.context["metadata"][1]["row_key"] = self.context["metadata"][0]["row_key"]
        self.assertEqual(self.run_block("cohort")["status"], "failed")
        self.assertEqual(self.run_block("demographics")["status"], "failed")

    def test_missing_admission_key_is_unverifiable(self):
        self.context["metadata"][0].pop("row_key")
        for name in ("schema", "demographics", "cohort"):
            self.assertEqual(self.run_block(name)["status"], "unverifiable")

    def test_invalid_boolean_rejected(self):
        for value in ("False", "0", "yes", "", None, 0, 1):
            self.context = fixture()
            self.context["metadata"][0]["race_was_missing"] = value
            with self.subTest(value=value):
                self.assertEqual(self.run_block("demographics")["status"], "failed")

    def test_real_booleans_accepted_and_false_string_not_truthy(self):
        self.context["metadata"][0]["race_was_missing"] = True
        self.context["metadata"][1]["race_was_missing"] = False
        item = self.run_block("demographics")
        self.assertEqual(item["status"], "completed")
        self.assertEqual(item["details"]["original_race_missing"], {"train": 1, "validation": 0, "test": 0})

    def test_invalid_target_rejected(self):
        for value in ("1.0", "01", "2", "", None, True, 0.0):
            self.context = fixture()
            self.context["metadata"][0]["target"] = value
            with self.subTest(value=value):
                self.assertEqual(self.run_block("label_observability")["status"], "failed")
                self.assertEqual(self.run_block("demographics")["status"], "failed")

    def test_integer_target_is_accepted(self):
        self.context["metadata"][0]["target"] = 1
        self.assertEqual(self.run_block("demographics")["status"], "completed")

    def test_original_race_missing_and_imputed_race_are_separate(self):
        item = self.run_block("demographics")["details"]
        public = item["distributions"]["train"]["race"]["Caucasian"]
        original = item["distributions"]["train"]["race_original"]
        self.assertEqual(public["admissions"], 2)
        self.assertEqual(original["MISSING_ORIGINAL"]["admissions"], 1)
        self.assertEqual(original["Caucasian"]["admissions"], 1)
        self.assertEqual(item["caucasian_imputed"]["train"], 1)
        self.assertEqual(public["patients"], 1)
        self.assertEqual(public["patients_with_target_1"], 1)
        self.assertEqual(public["patients_with_target_0"], 1)
        self.assertEqual(public["target_0"], 1)
        self.assertEqual(public["target_1"], 1)
        self.assertEqual(public["event_rate"], 0.5)

    def test_imputation_inconsistency_fails(self):
        self.context["metadata"][0]["race_original"] = "Caucasian"
        self.assertEqual(self.run_block("demographics")["status"], "failed")
        self.context = fixture()
        self.context["metadata"][0]["race"] = "Other"
        self.assertEqual(self.run_block("demographics")["status"], "failed")

    def test_missing_original_race_evidence_is_unverifiable(self):
        self.context["metadata"][0].pop("race_original")
        self.assertEqual(self.run_block("demographics")["status"], "unverifiable")

    def test_missing_demographic_column_is_unverifiable(self):
        self.context["metadata"][0].pop("gender")
        self.assertEqual(self.run_block("demographics")["status"], "unverifiable")

    def test_missing_demographic_values_remain_visible_not_completed(self):
        self.context["metadata"][0]["gender"] = ""
        item = self.run_block("demographics")
        self.assertEqual(item["status"], "unverifiable")
        self.assertEqual(item["details"]["distributions"]["train"]["gender"]["MISSING"]["admissions"], 1)

    def test_absent_groups_are_not_invented(self):
        item = self.run_block("demographics")
        self.assertNotIn("invented-group", item["details"]["distributions"]["test"]["race"])

    def test_feature_order_mismatch_fails(self):
        self.context["feature_columns"].reverse()
        self.assertEqual(self.run_block("schema")["status"], "failed")

    def test_duplicate_feature_names_fail(self):
        for key in ("feature_columns", "expected_feature_columns"):
            self.context[key] = ["duplicate", "duplicate"]
        self.assertEqual(self.run_block("schema")["status"], "failed")

    def test_missing_feature_schema_is_unverifiable(self):
        self.context.pop("expected_feature_columns")
        self.assertEqual(self.run_block("schema")["status"], "unverifiable")

    def test_missing_feature_values_is_unverifiable(self):
        self.context.pop("feature_rows")
        self.assertEqual(self.run_block("schema")["status"], "unverifiable")

    def test_feature_vector_length_mismatch_fails(self):
        self.context["feature_rows"][0]["feature_values"].pop()
        item = self.run_block("schema")
        self.assertEqual(item["status"], "failed")
        self.assertEqual(item["details"]["row_issues"]["length"], 1)

    def test_feature_row_count_mismatch_fails(self):
        self.context["feature_rows"].pop()
        item = self.run_block("schema")
        self.assertEqual(item["status"], "failed")
        self.assertEqual(item["details"]["row_issues"]["row_count"], 1)

    def test_feature_row_order_mismatch_fails(self):
        self.context["feature_rows"][:2] = self.context["feature_rows"][1::-1]
        self.assertEqual(self.run_block("schema")["status"], "failed")

    def test_feature_row_position_mismatch_fails(self):
        self.context["feature_rows"][1]["row_position"] = "1"
        self.assertEqual(self.run_block("schema")["status"], "failed")

    def test_numeric_quality_and_constant_observation(self):
        item = self.run_block("schema")
        self.assertEqual(item["status"], "unverifiable")
        self.assertEqual(item["details"]["numeric_contract"], "passed")
        quality = item["details"]["quality"]
        self.assertEqual(quality["synthetic_scaled"]["min"], -0.5)
        self.assertFalse(quality["synthetic_scaled"]["constant"])
        self.assertTrue(quality["synthetic_constant"]["constant"])
        self.assertEqual(quality["synthetic_constant"]["finite"], 6)
        self.assertEqual(item["details"]["clinical_semantics"]["status"], "unverifiable")

    def test_nonfinite_missing_and_non_numeric_values_fail(self):
        for value, category in ((None, "missing"), ("", "missing"), ("1", "non_numeric"),
                                (True, "non_numeric"), (float("inf"), "non_finite"),
                                (float("nan"), "non_finite")):
            self.context = fixture()
            self.context["feature_rows"][0]["feature_values"][0] = value
            with self.subTest(category=category, value=value):
                item = self.run_block("schema")
                self.assertEqual(item["status"], "failed")
                self.assertEqual(item["details"]["quality"]["synthetic_scaled"][category], 1)

    def test_malformed_json_fails(self):
        self.context["feature_rows"][0]["feature_values"] = "["
        self.assertEqual(self.run_block("schema")["status"], "failed")

    def test_feature_stream_is_consumed_once(self):
        consumed = []
        stream_factory = copy.deepcopy(self.context["feature_rows"])
        def rows():
            for row in stream_factory:
                consumed.append(1)
                yield row
        self.context["feature_rows"] = rows
        self.assertEqual(self.run_block("schema")["details"]["rows"], 6)
        self.assertEqual(len(consumed), 6)

    def test_cohort_selection_is_description_not_row_certification(self):
        item = self.run_block("cohort")
        self.assertEqual(item["status"], "unverifiable")
        self.assertEqual(item["details"]["research_unit"], "not declared")
        self.assertEqual(item["details"]["row_level_selection"]["status"], "unverifiable")
        self.assertIsNone(item["details"]["upstream_population"]["excluded"])
        self.assertEqual(len(item["details"]["source_selection_description"]["criteria"]), 5)

    def test_free_text_selection_is_not_executed(self):
        self.context["additional_selection"] = "__import__('os').system('bad')"
        item = self.run_block("cohort")
        self.assertEqual(item["status"], "unverifiable")
        self.assertEqual(item["details"]["total_admissions"], 6)
        self.assertTrue(item["details"]["additional_selection"]["declared"])
        self.assertNotIn(self.context["additional_selection"], json.dumps(item))

    def test_cohort_propagates_failed_row_selection(self):
        self.context["raw_evidence"] = {"selection": {"status": "failed"}}
        self.assertEqual(self.run_block("cohort")["status"], "failed")

    def test_unknown_research_unit_fails(self):
        self.context["research_unit"] = "invented"
        self.assertEqual(self.run_block("cohort")["status"], "failed")

    def test_no_approved_prediction_time_is_unverifiable(self):
        self.context["feature_columns"] = ["time_in_hospital", "discharge_disposition_id", "anything"]
        item = self.run_block("prediction_timing")
        self.assertEqual(item["status"], "unverifiable")
        self.assertEqual(len(item["details"]["undocumented_features"]), 3)
        self.assertEqual(item["details"]["completion_information_candidates"],
                         ["time_in_hospital", "discharge_disposition_id"])

    def test_lexical_safety_is_never_certified(self):
        self.context["feature_columns"] = ["age"]
        self.context["prediction_time"] = {"point": "admission", "approved": True}
        self.assertEqual(self.run_block("prediction_timing")["status"], "unverifiable")

    def test_admission_timing_conflict_fails_with_evidence(self):
        self.context["feature_columns"] = ["time_in_hospital"]
        self.context["prediction_time"] = {"point": "admission", "approved": True}
        self.context["feature_availability"] = {
            "time_in_hospital": {"available_at": "discharge", "source": "synthetic-dictionary"}}
        self.assertEqual(self.run_block("prediction_timing")["status"], "failed")

    def test_complete_documented_timing_scope_can_pass(self):
        self.context["feature_columns"] = ["synthetic"]
        self.context["prediction_time"] = {"point": "discharge", "approved": True}
        self.context["feature_availability"] = {
            "synthetic": {"available_at": "discharge", "source": "synthetic-dictionary"}}
        self.assertEqual(self.run_block("prediction_timing")["status"], "passed")

    def test_unapproved_time_does_not_pass(self):
        self.context["prediction_time"] = {"point": "admission", "approved": False}
        self.assertEqual(self.run_block("prediction_timing")["status"], "unverifiable")

    def test_encoding_is_separate_from_followup_observation(self):
        self.context["raw_evidence"] = {"label_alignment": {"status": "passed", "matched_admissions": 6}}
        item = self.run_block("label_observability")
        self.assertEqual(item["status"], "unverifiable")
        self.assertEqual(item["details"]["target_definition"]["status"], "passed")
        self.assertEqual(item["details"]["encoding"]["status"], "passed")
        self.assertEqual(item["details"]["followup_completeness"]["status"], "unverifiable")
        self.assertEqual(item["details"]["outside_hospital_capture"]["status"], "unverifiable")

    def test_wrong_target_definition_fails(self):
        self.context["target_mapping"]["NO"] = 1
        self.assertEqual(self.run_block("label_observability")["status"], "failed")

    def test_missing_target_definition_is_unverifiable(self):
        self.context.pop("target_mapping")
        self.assertEqual(self.run_block("label_observability")["status"], "unverifiable")

    def test_raw_label_mismatch_is_not_overridden(self):
        self.context["raw_evidence"] = {"label_alignment": {"status": "failed", "target_mismatches": 1}}
        self.assertEqual(self.run_block("label_observability")["status"], "failed")

    def test_integrity_does_not_prove_train_only_preprocessing(self):
        self.context["asset_integrity_verified"] = True
        self.context["data_manifest"] = {
            "training_performed": False, "preprocessing_performed": False,
            "prepared_source": {"limitations": [
                "Source target encoding before splitting is a concern.",
                "Public validation was used in model development.",
                "Prepared 113-feature inputs are already standardized.",
            ]},
        }
        item = self.run_block("preprocessing_lineage")
        self.assertEqual(item["status"], "unverifiable")
        self.assertEqual(item["details"]["asset_integrity"]["status"], "passed")
        for stage in item["details"]["historical_transformations"].values():
            self.assertIsNone(stage["fit_scope"])
            self.assertEqual(stage["status"], "unverifiable")
        self.assertTrue(item["details"]["historical_transformations"]["target_encoding"]["evidence"])
        self.assertTrue(item["details"]["validation_use"]["evidence"])

    def test_no_lineage_record_does_not_invent_imputation_evidence(self):
        item = self.run_block("preprocessing_lineage")
        self.assertEqual(item["status"], "unverifiable")
        self.assertEqual(item["details"]["historical_transformations"]["imputation"]["evidence"], [])
        self.assertIsNone(item["details"]["historical_transformations"]["imputation"]["documented_rule"])

    def test_aggregate_report_does_not_disclose_keys_or_rows(self):
        payload = report(self.context)
        serialized = json.dumps(payload, allow_nan=False)
        self.assertTrue(payload["report_generated"])
        self.assertEqual(payload["assessment_status"], "unverifiable")
        self.assertFalse(payload["model_execution_performed"])
        self.assertNotIn("synthetic-patient-", serialized)
        self.assertNotIn("synthetic-encounter-", serialized)
        self.assertNotIn("feature_values", serialized)

    def test_failed_assessment_can_generate_successful_report(self):
        self.context["metadata"][2]["patient_key"] = "synthetic-patient-0"
        payload = report(self.context)
        self.assertTrue(payload["report_generated"])
        self.assertEqual(payload["assessment_status"], "failed")

    def test_report_does_not_mutate_inputs(self):
        before = copy.deepcopy(self.context)
        report(self.context)
        self.assertEqual(self.context, before)


class EDARunnerTests(unittest.TestCase):
    def setUp(self):
        self.context = fixture()
        self.manifest = {
            "raw_source_sha256": "a" * 64, "split_rows": dict.fromkeys(SPLITS, 2),
            "feature_columns": self.context["feature_columns"],
            "prepared_source": {
                "features": self.context["feature_columns"],
                "target_mapping": self.context["target_mapping"],
            },
        }

    def test_loader_reuses_manifest_validation_and_lazy_features(self):
        content = csv_text(METADATA_COLUMNS, self.context["metadata"])
        with patch("app.executor_eda.validate_data_manifest", return_value=self.manifest) as validate, \
                patch.object(Path, "open", return_value=io.StringIO(content)), \
                patch("app.executor_eda.feature_rows") as stream:
            context = load_context(Path("synthetic-assets"))
            validate.assert_called_once_with(Path("synthetic-assets"))
            stream.assert_not_called()
            self.assertTrue(context["asset_integrity_verified"])
            context["feature_rows"]()
            stream.assert_called_once_with(Path("synthetic-assets"))

    def test_loader_rejects_manifest_failure(self):
        with patch("app.executor_eda.validate_data_manifest", side_effect=ValueError("integrity")):
            with self.assertRaisesRegex(ValueError, "integrity"):
                load_context(Path("synthetic-assets"))

    def test_loader_rejects_metadata_count_mismatch(self):
        content = csv_text(METADATA_COLUMNS, self.context["metadata"][:2])
        with patch("app.executor_eda.validate_data_manifest", return_value=self.manifest), \
                patch.object(Path, "open", return_value=io.StringIO(content)):
            with self.assertRaisesRegex(ValueError, "split counts"):
                load_context(Path("synthetic-assets"))

    def test_feature_reader_uses_existing_order_contract(self):
        content = csv_text(FEATURE_COLUMNS, self.context["feature_rows"])
        with patch.object(Path, "open", return_value=io.StringIO(content)):
            self.assertEqual(len(list(feature_rows(Path("synthetic-assets")))), 6)
        content = "split,row_key,row_position,feature_values\n"
        with patch.object(Path, "open", return_value=io.StringIO(content)):
            with self.assertRaisesRegex(ValueError, "schema"):
                list(feature_rows(Path("synthetic-assets")))

    def raw_rows(self):
        return [{
            "encounter_id": row["row_key"], "patient_nbr": row["patient_key"],
            "readmitted": "<30" if row["target"] == "1" else (">30" if index == 1 else "NO"),
            "time_in_hospital": "3", "num_lab_procedures": "4", "num_medications": "2",
            "diag_1": "250.01", "diag_2": "?", "diag_3": "?",
        } for index, row in enumerate(self.context["metadata"])]

    def evidence(self, rows, checksum=None):
        content = csv_text(list(rows[0]), rows)
        with patch("app.executor_eda.validate_dataset", return_value={"sha256": checksum or "a" * 64}) as validate, \
                patch.object(Path, "open", return_value=io.StringIO(content)):
            evidence = raw_evidence(Path("synthetic.csv"), self.context["metadata"], self.manifest)
            validate.assert_called_once()
            return evidence

    def test_raw_label_mapping_and_selection_counts(self):
        evidence = self.evidence(self.raw_rows())
        alignment = evidence["label_alignment"]
        self.assertEqual(alignment["status"], "passed")
        self.assertEqual(alignment["raw_readmitted_counts"], {"<30": 3, ">30": 1, "NO": 2})
        self.assertEqual(alignment["matched_admissions"], 6)
        self.assertEqual(evidence["selection"]["status"], "unverifiable")
        for predicate in evidence["selection"]["raw_count_checks"].values():
            self.assertEqual(predicate["satisfied"], 6)
            self.assertEqual(predicate["status"], "passed")

    def test_raw_target_and_patient_mismatch_are_aggregated(self):
        rows = self.raw_rows()
        rows[0]["readmitted"] = "NO"
        rows[1]["patient_nbr"] = "synthetic-wrong"
        evidence = self.evidence(rows)
        self.assertEqual(evidence["label_alignment"]["status"], "failed")
        self.assertEqual(evidence["label_alignment"]["target_mismatches"], 1)
        self.assertEqual(evidence["label_alignment"]["patient_link_mismatches"], 1)
        self.assertNotIn("synthetic-wrong", json.dumps(evidence))

    def test_raw_source_hash_mismatch_rejected(self):
        with self.assertRaisesRegex(ValueError, "raw source"):
            self.evidence(self.raw_rows(), "b" * 64)

    def test_duplicate_raw_encounter_rejected(self):
        rows = self.raw_rows()
        rows.append(rows[0])
        with self.assertRaisesRegex(ValueError, "uniqueness"):
            self.evidence(rows)

    def test_missing_raw_coverage_rejected(self):
        with self.assertRaisesRegex(ValueError, "missing"):
            self.evidence(self.raw_rows()[:-1])

    def test_row_selection_violation_is_not_hidden(self):
        rows = self.raw_rows()
        rows[0]["time_in_hospital"] = "15"
        evidence = self.evidence(rows)
        self.assertEqual(evidence["selection"]["status"], "failed")
        self.assertEqual(evidence["selection"]["raw_count_checks"]
                         ["length_of_stay_1_to_14"]["violated"], 1)

    def test_unknown_raw_selection_field_is_unverifiable(self):
        rows = self.raw_rows()
        rows[0]["num_lab_procedures"] = "?"
        evidence = self.evidence(rows)
        self.assertEqual(evidence["selection"]["raw_count_checks"]
                         ["laboratory_count_positive"]["unverifiable"], 1)


if __name__ == "__main__":
    unittest.main()
