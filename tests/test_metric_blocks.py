"""Synthetic-only evaluation contract tests. No clinical data or model execution."""

import ast
import copy
import importlib
import json
from pathlib import Path
import random
import subprocess
import sys
import unittest
from unittest.mock import patch

from app.blocks import metrics


ROOT = Path(__file__).resolve().parents[1]
IDS = (
    "roc_auc", "average_precision", "brier_score", "calibration_curve",
    "confusion_matrix", "accuracy", "recall", "specificity", "precision",
    "negative_predictive_value", "f1", "decision_curve", "categorical_nri",
    "recall_at_k", "precision_at_k",
)
THRESHOLD_IDS = (
    "confusion_matrix", "accuracy", "recall", "specificity", "precision",
    "negative_predictive_value", "f1",
)


def synthetic_context():
    return {
        "y_true": [0, 1, 0, 1],
        "y_probability": [0.1, 0.8, 0.6, 0.4],
        "reference_probabilities": [0.2, 0.3, 0.7, 0.9],
        "sample_ids": ["synthetic-a", "synthetic-b", "synthetic-c", "synthetic-d"],
        "reference_sample_ids": ["synthetic-a", "synthetic-b", "synthetic-c", "synthetic-d"],
        "clinical_action": "Synthetic example: request an additional review",
        "comparison_model": "synthetic-reference-probabilities-only",
    }


def explicit_parameters():
    parameters = {identifier: {"threshold": 0.5} for identifier in THRESHOLD_IDS}
    parameters.update({
        "calibration_curve": {"n_bins": 5},
        "decision_curve": {"thresholds": [0.2, 0.5, 0.8]},
        "categorical_nri": {"risk_thresholds": [0.2, 0.5, 0.8]},
        "recall_at_k": {"top_fraction": 0.5},
        "precision_at_k": {"top_fraction": 0.5},
    })
    return parameters


class MetricBlockTests(unittest.TestCase):
    def setUp(self):
        self.context = synthetic_context()
        self.parameters = explicit_parameters()

    def run_block(self, identifier, context=None, parameters=None):
        return metrics.run_metrics(
            [identifier], self.context if context is None else context,
            {identifier: parameters} if parameters is not None else None,
        )[0]

    def assert_unavailable(self, output, reason=None):
        self.assertEqual(output["status"], "not_applicable")
        self.assertIsNone(output["value"])
        self.assertTrue(output["details"]["reason"])
        if reason:
            self.assertEqual(output["details"]["reason"], reason)

    def test_catalog_has_exact_evaluation_schema_and_fifteen_blocks(self):
        catalog = metrics.load_catalog()
        self.assertEqual(set(catalog), {"schema_version", "stage", "metrics"})
        self.assertEqual(catalog["schema_version"], 1)
        self.assertEqual(catalog["stage"], "evaluation")
        self.assertEqual({entry["id"] for entry in catalog["metrics"]}, set(IDS))
        self.assertEqual(len(catalog["metrics"]), 15)
        for entry in catalog["metrics"]:
            with self.subTest(identifier=entry["id"]):
                self.assertEqual(set(entry), {
                    "id", "name", "description", "module", "aliases", "requires",
                    "requires_parameters", "requires_context",
                })
                self.assertEqual(entry["module"], f"app.blocks.metrics.{entry['id']}")
                self.assertTrue(entry["name"])
                self.assertTrue(entry["description"])
                self.assertNotIn("default", entry)
                module = importlib.import_module(entry["module"])
                self.assertTrue(callable(module.run))

    def test_catalog_reads_are_independent(self):
        changed = metrics.load_catalog()
        changed["metrics"][0]["id"] = "synthetic-mutated"
        self.assertEqual(metrics.load_catalog()["metrics"][0]["id"], "roc_auc")

    def test_catalog_declares_threshold_and_conditional_requirements(self):
        entries = {entry["id"]: entry for entry in metrics.load_catalog()["metrics"]}
        for identifier in THRESHOLD_IDS:
            self.assertEqual(entries[identifier]["requires_parameters"], ["threshold"])
        self.assertEqual(entries["decision_curve"]["requires_context"], ["clinical_action"])
        self.assertEqual(entries["decision_curve"]["requires_parameters"], ["thresholds"])
        self.assertEqual(entries["categorical_nri"]["requires_context"], ["comparison_model"])
        self.assertEqual(entries["categorical_nri"]["requires_parameters"], ["risk_thresholds"])
        self.assertEqual(set(entries["categorical_nri"]["requires"]), {
            "y_true", "y_probability", "reference_probabilities", "sample_ids", "reference_sample_ids",
        })

    def test_aliases_are_canonical_and_execute_once_in_requested_order(self):
        output = metrics.run_metrics(
            ["PPV", "precision", "sensitivity", "recall", "npv", "negative_predictive_value", "AP", "ap"],
            self.context,
            {"ppv": {"threshold": 0.5}, "sensitivity": {"threshold": 0.5}, "npv": {"threshold": 0.5}},
        )
        self.assertEqual([item["block"] for item in output], [
            "precision", "recall", "negative_predictive_value", "average_precision",
        ])
        self.assertTrue(all(item["status"] == "completed" for item in output))

    def test_alias_deduplication_is_execution_not_only_output_deduplication(self):
        module = importlib.import_module("app.blocks.metrics.precision")
        with patch.object(module, "run", wraps=module.run) as run:
            metrics.run_metrics(["ppv", "precision", "PPV"], self.context, {"ppv": {"threshold": 0.5}})
        self.assertEqual(run.call_count, 1)

    def test_repeated_alias_parameters_may_agree_but_must_not_conflict(self):
        output = metrics.run_metrics(["ppv", "precision"], self.context, {
            "ppv": {"threshold": 0.5}, "precision": {"threshold": 0.5},
        })
        self.assertEqual(len(output), 1)
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            metrics.run_metrics(["precision"], self.context, {
                "ppv": {"threshold": 0.4}, "precision": {"threshold": 0.5},
            })

    def test_unknown_id_fails_before_import_or_execution(self):
        for identifier in ("not_a_metric", "app.blocks.metrics.accuracy", "../accuracy", "", "__import__"):
            with self.subTest(identifier=identifier), patch.object(metrics, "import_module") as importer:
                with self.assertRaisesRegex(ValueError, "Unknown metric id"):
                    metrics.run_metrics(["roc_auc", identifier], self.context)
                importer.assert_not_called()

    def test_unknown_parameter_id_fails_before_execution(self):
        with patch.object(metrics, "import_module") as importer:
            with self.assertRaisesRegex(ValueError, "Unknown metric id"):
                metrics.run_metrics(["roc_auc"], self.context, {"unknown": {}})
            importer.assert_not_called()

    def test_registry_rejects_bad_arguments(self):
        for ids in ("roc_auc", None, {"roc_auc"}, [1]):
            with self.subTest(ids=ids), self.assertRaisesRegex(ValueError, "ids"):
                metrics.run_metrics(ids, self.context)
        for context in (None, [], "invalid"):
            with self.subTest(context=context), self.assertRaisesRegex(ValueError, "context"):
                metrics.run_metrics([], context)
        for parameters in ([], "threshold", 1):
            with self.subTest(parameters=parameters), self.assertRaisesRegex(ValueError, "parameters_by_id"):
                metrics.run_metrics(["roc_auc"], self.context, parameters)
        with self.assertRaisesRegex(ValueError, "Each parameters_by_id"):
            metrics.run_metrics(["roc_auc"], self.context, {"roc_auc": []})

    def test_empty_selection_performs_no_metric_imports(self):
        with patch.object(metrics, "import_module") as importer:
            self.assertEqual(metrics.run_metrics([], self.context), [])
            importer.assert_not_called()

    def test_catalog_rejects_untrusted_module_paths(self):
        for module in ("os", "app.blocks.metrics._validation", "app.blocks.metrics.roc_auc.evil",
                       "app.blocks.metrics...roc_auc", "app.blocks.checks.schema"):
            catalog = metrics.load_catalog()
            catalog["metrics"][0]["module"] = module
            with self.subTest(module=module), patch.object(metrics, "_CATALOG_PATH") as path:
                path.read_text.return_value = json.dumps(catalog)
                with patch.object(metrics, "import_module") as importer:
                    with self.assertRaisesRegex(ValueError, "allowlist"):
                        metrics.run_metrics(["roc_auc"], self.context)
                    importer.assert_not_called()

    def test_catalog_rejects_missing_duplicate_or_unknown_ids(self):
        original = metrics.load_catalog()
        malformed = []
        removed = copy.deepcopy(original)
        removed["metrics"].pop()
        malformed.append(removed)
        duplicated = copy.deepcopy(original)
        duplicated["metrics"].append(copy.deepcopy(duplicated["metrics"][0]))
        malformed.append(duplicated)
        unknown = copy.deepcopy(original)
        unknown["metrics"][0]["id"] = "arbitrary_import"
        malformed.append(unknown)
        for catalog in malformed:
            with self.subTest(catalog=catalog), patch.object(metrics, "_CATALOG_PATH") as path:
                path.read_text.return_value = json.dumps(catalog)
                with self.assertRaises(ValueError):
                    metrics.load_catalog()

    def test_catalog_rejects_ambiguous_aliases_and_wrong_fields(self):
        for field, value in (
            ("aliases", ["ppv"]), ("aliases", [" "]), ("aliases", "auc"),
            ("requires", [1]), ("requires", ["y_true", "y_true"]),
            ("name", ""), ("description", None),
        ):
            catalog = metrics.load_catalog()
            catalog["metrics"][0][field] = value
            with self.subTest(field=field, value=value), patch.object(metrics, "_CATALOG_PATH") as path:
                path.read_text.return_value = json.dumps(catalog)
                with self.assertRaises(ValueError):
                    metrics.load_catalog()
        catalog = metrics.load_catalog()
        catalog["metrics"][0]["default"] = True
        with patch.object(metrics, "_CATALOG_PATH") as path:
            path.read_text.return_value = json.dumps(catalog)
            with self.assertRaises(ValueError):
                metrics.load_catalog()

    def test_catalog_rejects_bad_root_and_bad_json(self):
        for value in ([], {}, {"schema_version": True, "stage": "evaluation", "metrics": []},
                      {"schema_version": 1, "stage": "eda", "metrics": []}):
            with self.subTest(value=value), patch.object(metrics, "_CATALOG_PATH") as path:
                path.read_text.return_value = json.dumps(value)
                with self.assertRaisesRegex(ValueError, "schema"):
                    metrics.load_catalog()
        with patch.object(metrics, "_CATALOG_PATH") as path:
            path.read_text.return_value = "{not-json"
            with self.assertRaisesRegex(ValueError, "Cannot read"):
                metrics.load_catalog()

    def test_each_independent_module_implements_run_and_validates_arguments(self):
        for identifier in IDS:
            module = importlib.import_module(f"app.blocks.metrics.{identifier}")
            with self.subTest(identifier=identifier):
                with self.assertRaisesRegex(ValueError, "context"):
                    module.run(None)
                with self.assertRaisesRegex(ValueError, "parameters"):
                    module.run(self.context, [])
                output = module.run(self.context, self.parameters.get(identifier))
                self.assertEqual(set(output), {"block", "status", "value", "details"})
                self.assertEqual(output["block"], identifier)
                self.assertEqual(output["status"], "completed")
                self.assertIn("definition", output["details"])
                self.assertIn("denominator", output["details"])

    def test_all_blocks_missing_inputs_are_not_applicable(self):
        for identifier in IDS:
            with self.subTest(identifier=identifier):
                output = self.run_block(identifier, {}, self.parameters.get(identifier))
                self.assert_unavailable(output, "missing_inputs")
                self.assertIn("y_true", output["details"]["missing_inputs"])

    def test_none_input_is_missing_not_a_numerical_zero(self):
        for field in ("y_true", "y_probability"):
            context = {**self.context, field: None}
            with self.subTest(field=field):
                self.assert_unavailable(self.run_block("brier_score", context), "missing_inputs")

    def test_all_blocks_zero_rows_are_not_applicable(self):
        context = {**self.context, "y_true": [], "y_probability": [], "reference_probabilities": [],
                   "sample_ids": [], "reference_sample_ids": []}
        for identifier in IDS:
            with self.subTest(identifier=identifier):
                self.assert_unavailable(
                    self.run_block(identifier, context, self.parameters.get(identifier)), "empty_input",
                )

    def test_all_blocks_reject_nonfinite_probabilities(self):
        for identifier in IDS:
            for bad in (float("nan"), float("inf"), -float("inf")):
                context = {**self.context, "y_probability": [0.1, bad, 0.2, 0.9]}
                with self.subTest(identifier=identifier, bad=bad), self.assertRaisesRegex(ValueError, "finite"):
                    self.run_block(identifier, context, self.parameters.get(identifier))

    def test_all_blocks_reject_out_of_range_probabilities(self):
        for identifier in IDS:
            for bad in (-0.001, 1.001):
                context = {**self.context, "y_probability": [0.1, bad, 0.2, 0.9]}
                with self.subTest(identifier=identifier, bad=bad), self.assertRaisesRegex(ValueError, "between"):
                    self.run_block(identifier, context, self.parameters.get(identifier))

    def test_all_blocks_reject_length_mismatch(self):
        for identifier in IDS:
            context = {**self.context, "y_probability": [0.1]}
            with self.subTest(identifier=identifier), self.assertRaisesRegex(ValueError, "equal row counts"):
                self.run_block(identifier, context, self.parameters.get(identifier))

    def test_all_blocks_reject_invalid_binary_labels(self):
        for identifier in IDS:
            for bad in (2, -1, 0.5, float("nan"), float("inf"), "1", None, [1]):
                context = {**self.context, "y_true": [0, bad, 0, 1]}
                with self.subTest(identifier=identifier, bad=bad), self.assertRaises(ValueError):
                    self.run_block(identifier, context, self.parameters.get(identifier))

    def test_vector_shapes_types_and_boolean_probabilities_are_rejected(self):
        for key in ("y_true", "y_probability"):
            for bad in ("0101", {0, 1}, {"0": 1}, 1, [[0], [1], [0], [1]]):
                with self.subTest(key=key, bad=bad), self.assertRaisesRegex(ValueError, key):
                    self.run_block("brier_score", {**self.context, key: bad})
        for bad in (True, "0.5", None, complex(0.5, 0)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.run_block("brier_score", {**self.context, "y_probability": [bad] * 4})

    def test_numpy_numeric_vectors_are_accepted_and_outputs_are_json_safe(self):
        import numpy as np

        context = {**self.context, "y_true": np.array([0, 1, 0, 1], dtype=np.int64),
                   "y_probability": np.array([0.1, 0.8, 0.6, 0.4], dtype=np.float32)}
        outputs = metrics.run_metrics(list(IDS), context, self.parameters)
        json.dumps(outputs, allow_nan=False)
        self.assertTrue(all(item["status"] == "completed" for item in outputs))

    def test_boolean_labels_are_binary_labels(self):
        output = self.run_block("brier_score", {"y_true": [False, True], "y_probability": [0.0, 1.0]})
        self.assertEqual(output["value"], 0.0)

    def test_probability_metrics_match_sklearn_on_exact_reverse_constant_and_sparse_inputs(self):
        from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

        datasets = [
            ([0, 0, 1, 1], [0, 0, 1, 1]),
            ([0, 0, 1, 1], [1, 1, 0, 0]),
            ([0, 0, 0, 1], [0.3, 0.3, 0.3, 0.3]),
            ([0] * 99 + [1], [index / 100 for index in range(100)]),
            ([0, 1, 0, 1], [0.1, 0.8, 0.6, 0.4]),
        ]
        references = {
            "roc_auc": roc_auc_score, "average_precision": average_precision_score,
            "brier_score": brier_score_loss,
        }
        for labels, probabilities in datasets:
            for identifier, reference in references.items():
                with self.subTest(identifier=identifier, labels=labels, scores=probabilities):
                    output = self.run_block(identifier, {"y_true": labels, "y_probability": probabilities})
                    self.assertAlmostEqual(output["value"], reference(labels, probabilities), places=14)

    def test_probability_metrics_match_sklearn_randomized_synthetic_ties(self):
        from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

        rng = random.Random(20261019)
        for count in (2, 3, 11, 100, 501):
            for _ in range(5):
                labels = [0, 1] + [rng.randrange(2) for _ in range(count - 2)]
                scores = [rng.randrange(11) / 10 for _ in range(count)]
                for identifier, reference in (
                    ("roc_auc", roc_auc_score), ("average_precision", average_precision_score),
                    ("brier_score", brier_score_loss),
                ):
                    with self.subTest(count=count, identifier=identifier):
                        actual = self.run_block(identifier, {"y_true": labels, "y_probability": scores})["value"]
                        self.assertAlmostEqual(actual, reference(labels, scores), places=14)

    def test_roc_auc_requires_both_classes(self):
        for labels in ([0] * 4, [1] * 4):
            with self.subTest(labels=labels):
                self.assert_unavailable(self.run_block("roc_auc", {**self.context, "y_true": labels}),
                                        "both_classes_required")

    def test_average_precision_requires_positives_but_all_positive_ap_is_one(self):
        self.assert_unavailable(self.run_block("average_precision", {**self.context, "y_true": [0] * 4}),
                                "positive_class_required")
        self.assertEqual(self.run_block("average_precision", {**self.context, "y_true": [1] * 4})["value"], 1.0)

    def test_average_precision_is_ap_and_not_trapezoidal_area(self):
        from sklearn.metrics import auc, precision_recall_curve

        context = {"y_true": [0, 1, 0, 1], "y_probability": [0.9, 0.8, 0.7, 0.6]}
        output = self.run_block("average_precision", context)
        precision, recall, _ = precision_recall_curve(context["y_true"], context["y_probability"])
        self.assertAlmostEqual(output["value"], 0.5)
        self.assertNotAlmostEqual(output["value"], auc(recall, precision))
        self.assertTrue(output["details"]["not_trapezoidal_pr_area"])

    def test_brier_is_defined_for_single_class_and_extreme_probabilities(self):
        self.assertEqual(self.run_block("brier_score", {"y_true": [0, 0], "y_probability": [1, 1]})["value"], 1.0)
        self.assertEqual(self.run_block("brier_score", {"y_true": [1, 1], "y_probability": [1, 1]})["value"], 0.0)

    def test_calibration_bin_count_sum_and_exact_boundary_convention(self):
        context = {"y_true": [0, 1, 0, 1, 1], "y_probability": [0, 0.25, 0.5, 0.75, 1]}
        output = self.run_block("calibration_curve", context, {"n_bins": 4})
        bins = output["value"]
        self.assertEqual([row["count"] for row in bins], [1, 1, 1, 2])
        self.assertEqual(sum(row["count"] for row in bins), 5)
        self.assertEqual(bins[3]["mean_predicted_probability"], 0.875)
        self.assertEqual(bins[3]["observed_event_rate"], 1.0)
        self.assertTrue(bins[3]["upper_inclusive"])

    def test_calibration_empty_groups_are_null_not_zero(self):
        output = self.run_block("calibration_curve", {"y_true": [0, 1], "y_probability": [0, 1]}, {"n_bins": 4})
        for row in output["value"][1:3]:
            self.assertEqual(row["count"], 0)
            self.assertIsNone(row["mean_predicted_probability"])
            self.assertIsNone(row["observed_event_rate"])
        json.dumps(output, allow_nan=False)

    def test_calibration_default_and_single_bin(self):
        self.assertEqual(len(self.run_block("calibration_curve")["value"]), 10)
        output = self.run_block("calibration_curve", parameters={"n_bins": 1})
        self.assertEqual(output["value"][0]["count"], 4)
        self.assertAlmostEqual(output["value"][0]["mean_predicted_probability"], 0.475)
        self.assertEqual(output["value"][0]["observed_event_rate"], 0.5)

    def test_calibration_nonboundary_nonempty_bins_match_sklearn(self):
        from sklearn.calibration import calibration_curve

        observed, predicted = calibration_curve(self.context["y_true"], self.context["y_probability"], n_bins=3)
        actual = self.run_block("calibration_curve", parameters={"n_bins": 3})["value"]
        nonempty = [row for row in actual if row["count"]]
        for row, expected_observed, expected_predicted in zip(nonempty, observed, predicted):
            self.assertAlmostEqual(row["observed_event_rate"], expected_observed)
            self.assertAlmostEqual(row["mean_predicted_probability"], expected_predicted)
        self.assertEqual(len(nonempty), len(observed))

    def test_calibration_rejects_invalid_bin_counts(self):
        for value in (0, -1, 1.5, True, None, "3", float("inf")):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "n_bins"):
                self.run_block("calibration_curve", parameters={"n_bins": value})

    def test_all_threshold_metrics_require_explicit_threshold(self):
        for identifier in THRESHOLD_IDS:
            for parameters in (None, {}, {"threshold": None}):
                with self.subTest(identifier=identifier, parameters=parameters):
                    output = self.run_block(identifier, parameters=parameters)
                    self.assert_unavailable(output, "missing_parameters")
                    self.assertEqual(output["details"]["missing_parameters"], ["threshold"])

    def test_all_threshold_metrics_reject_invalid_threshold(self):
        for identifier in THRESHOLD_IDS:
            for value in (-0.01, 1.01, float("nan"), float("inf"), True, "0.5", []):
                with self.subTest(identifier=identifier, value=value), self.assertRaisesRegex(ValueError, "threshold"):
                    self.run_block(identifier, parameters={"threshold": value})

    def test_threshold_boundary_is_inclusive_and_endpoints_are_valid(self):
        context = {"y_true": [0, 1, 1], "y_probability": [0, 0.5, 1]}
        expected = {0: [[0, 1], [0, 2]], 0.5: [[1, 0], [0, 2]], 1: [[1, 0], [1, 1]]}
        for threshold, matrix in expected.items():
            with self.subTest(threshold=threshold):
                output = self.run_block("confusion_matrix", context, {"threshold": threshold})
                self.assertEqual(output["value"]["matrix"], matrix)
                self.assertEqual(output["value"]["labels"], [0, 1])

    def test_confusion_counts_and_threshold_metrics_match_sklearn(self):
        from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, precision_score, recall_score

        labels = self.context["y_true"]
        predicted = [int(score >= 0.5) for score in self.context["y_probability"]]
        output = self.run_block("confusion_matrix", parameters={"threshold": 0.5})
        self.assertEqual(output["value"]["matrix"], confusion_matrix(labels, predicted, labels=[0, 1]).tolist())
        self.assertEqual({key: output["value"][key] for key in ("tp", "tn", "fp", "fn")},
                         {"tp": 1, "tn": 1, "fp": 1, "fn": 1})
        for identifier, reference in (
            ("accuracy", accuracy_score), ("recall", recall_score), ("precision", precision_score), ("f1", f1_score),
        ):
            with self.subTest(identifier=identifier):
                self.assertEqual(self.run_block(identifier, parameters={"threshold": 0.5})["value"],
                                 reference(labels, predicted))
        self.assertEqual(self.run_block("specificity", parameters={"threshold": 0.5})["value"], 0.5)
        self.assertEqual(self.run_block("npv", parameters={"threshold": 0.5})["value"], 0.5)

    def test_perfect_and_reverse_threshold_predictions(self):
        for scores, value in (([0, 1, 0, 1], 1), ([1, 0, 1, 0], 0)):
            for identifier in THRESHOLD_IDS[1:]:
                with self.subTest(identifier=identifier, scores=scores):
                    output = self.run_block(identifier, {**self.context, "y_probability": scores}, {"threshold": 0.5})
                    self.assertEqual(output["value"], value)

    def test_zero_denominators_remain_not_applicable_and_never_zero(self):
        cases = [
            ("recall", [0, 0], [0.1, 0.8]),
            ("specificity", [1, 1], [0.1, 0.8]),
            ("precision", [0, 1], [0.1, 0.2]),
            ("negative_predictive_value", [0, 1], [0.8, 0.9]),
            ("f1", [0, 0], [0.1, 0.2]),
        ]
        for identifier, labels, scores in cases:
            with self.subTest(identifier=identifier):
                output = self.run_block(identifier, {"y_true": labels, "y_probability": scores}, {"threshold": 0.5})
                self.assert_unavailable(output, "zero_denominator")
                self.assertEqual(output["details"]["denominator"], 0)

    def test_single_class_accuracy_and_confusion_counts_remain_defined(self):
        context = {"y_true": [0, 0], "y_probability": [0.1, 0.2]}
        self.assertEqual(self.run_block("accuracy", context, {"threshold": 0.5})["value"], 1.0)
        self.assertEqual(self.run_block("confusion_matrix", context, {"threshold": 0.5})["value"]["tn"], 2)

    def test_decision_curve_requires_explicit_thresholds_and_clinical_action(self):
        self.assert_unavailable(self.run_block("decision_curve"), "missing_parameters")
        for action in (None, "", "   "):
            with self.subTest(action=action):
                self.assert_unavailable(self.run_block(
                    "decision_curve", {**self.context, "clinical_action": action}, {"thresholds": [0.5]},
                ), "missing_context")
        with self.assertRaisesRegex(ValueError, "clinical_action"):
            self.run_block("decision_curve", {**self.context, "clinical_action": True}, {"thresholds": [0.5]})

    def test_decision_curve_matches_known_hand_calculation(self):
        # Synthetic N=4, events=2; at t=.25 TP=2 FP=1, at t=.5 TP=1 FP=1.
        rows = self.run_block("decision_curve", parameters={"thresholds": [0.25, 0.5]})["value"]
        self.assertAlmostEqual(rows[0]["model_net_benefit"], 0.5 - (0.25 / 3))
        self.assertAlmostEqual(rows[0]["treat_all_net_benefit"], 0.5 - (0.5 / 3))
        self.assertEqual(rows[0]["treat_none_net_benefit"], 0)
        self.assertEqual(rows[1]["model_net_benefit"], 0)
        self.assertEqual(rows[1]["treat_all_net_benefit"], 0)

    def test_decision_curve_preserves_negative_net_benefit(self):
        context = {**self.context, "y_probability": [1, 0, 1, 0]}
        output = self.run_block("decision_curve", context, {"thresholds": [0.75]})
        self.assertEqual(output["value"][0]["model_net_benefit"], -1.5)
        self.assertEqual(output["value"][0]["treat_all_net_benefit"], -1.0)

    def test_dca_threshold_equality_and_single_class_are_well_defined(self):
        context = {**self.context, "y_true": [0, 0], "y_probability": [0.5, 0.4]}
        output = self.run_block("decision_curve", context, {"thresholds": [0.5]})
        self.assertEqual(output["value"][0]["model_net_benefit"], -0.5)

    def test_dca_and_nri_reject_invalid_cutoff_vectors(self):
        for identifier, parameter in (("decision_curve", "thresholds"), ("categorical_nri", "risk_thresholds")):
            for value in ([], [0], [1], [-0.1], [1.1], [float("nan")], [float("inf")],
                          [0.5, 0.5], [0.8, 0.2], [True], "0.5", 0.5, {"cutoff": 0.5}):
                with self.subTest(identifier=identifier, value=value), self.assertRaisesRegex(ValueError, parameter):
                    self.run_block(identifier, parameters={parameter: value})

    def test_nri_requires_reference_probabilities_and_explicit_cutoffs(self):
        context = dict(self.context)
        del context["reference_probabilities"]
        self.assert_unavailable(self.run_block("categorical_nri", context, {"risk_thresholds": [0.5]}),
                                "missing_inputs")
        self.assert_unavailable(self.run_block("categorical_nri"), "missing_parameters")

    def test_nri_requires_paired_sample_ids(self):
        for key in ("sample_ids", "reference_sample_ids"):
            context = dict(self.context)
            del context[key]
            with self.subTest(key=key):
                output = self.run_block("categorical_nri", context, {"risk_thresholds": [0.5]})
                self.assert_unavailable(output, "missing_inputs")
                self.assertIn(key, output["details"]["missing_inputs"])

    def test_nri_rejects_misordered_unmatched_duplicate_and_wrong_length_ids(self):
        values = [
            list(reversed(self.context["sample_ids"])),
            ["synthetic-a", "synthetic-b", "synthetic-c", "synthetic-other"],
            ["synthetic-a"] * 4, ["synthetic-a"], [0, 1, 2, None], [0, 1, 2, True],
            ["", "synthetic-b", "synthetic-c", "synthetic-d"],
        ]
        for key in ("sample_ids", "reference_sample_ids"):
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, "sample_ids"):
                    self.run_block("categorical_nri", {**self.context, key: value}, {"risk_thresholds": [0.5]})

    def test_nri_accepts_unique_integer_identifiers(self):
        context = {**self.context, "sample_ids": [1, 2, 3, 4], "reference_sample_ids": [1, 2, 3, 4]}
        output = self.run_block("categorical_nri", context, {"risk_thresholds": [0.5]})
        self.assertTrue(output["details"]["paired_sample_ids_verified"])

    def test_nri_requires_comparison_model_description(self):
        for value in (None, "", " "):
            with self.subTest(value=value):
                self.assert_unavailable(self.run_block(
                    "categorical_nri", {**self.context, "comparison_model": value}, {"risk_thresholds": [0.5]},
                ), "missing_context")
        with self.assertRaisesRegex(ValueError, "comparison_model"):
            self.run_block("categorical_nri", {**self.context, "comparison_model": {}}, {"risk_thresholds": [0.5]})

    def test_nri_requires_both_classes(self):
        for labels in ([0] * 4, [1] * 4):
            with self.subTest(labels=labels):
                self.assert_unavailable(self.run_block(
                    "categorical_nri", {**self.context, "y_true": labels}, {"risk_thresholds": [0.5]},
                ), "both_classes_required")

    def test_nri_reference_probabilities_validate_shape_range_and_finiteness(self):
        for values in ([0.1], [0.1, 0.2, 0.3, -0.1], [0.1, 0.2, 0.3, float("nan")],
                       [0.1, 0.2, 0.3, float("inf")], [[0.1]] * 4):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.run_block("categorical_nri", {**self.context, "reference_probabilities": values},
                               {"risk_thresholds": [0.5]})

    def test_nri_perfect_reclassification_components_and_boundaries(self):
        context = {
            **self.context, "y_true": [1, 1, 0, 0],
            "reference_probabilities": [0.19, 0.49, 0.2, 0.5],
            "y_probability": [0.2, 0.5, 0.19, 0.49],
        }
        output = self.run_block("categorical_nri", context, {"risk_thresholds": [0.2, 0.5]})
        self.assertEqual(output["value"], {"event_component": 1, "nonevent_component": 1, "total": 2})
        self.assertEqual(output["details"]["movements"],
                         {"event_up": 2, "event_down": 0, "nonevent_up": 0, "nonevent_down": 2})
        self.assertIn("not proof", output["details"]["interpretation"])

    def test_nri_counts_each_reclassified_row_once_not_number_of_categories(self):
        context = {
            **self.context, "y_true": [1, 1, 0, 0],
            "reference_probabilities": [0.1, 0.9, 0.1, 0.9], "y_probability": [0.9, 0.1, 0.9, 0.1],
        }
        output = self.run_block("categorical_nri", context, {"risk_thresholds": [0.2, 0.5, 0.8]})
        self.assertEqual(output["value"], {"event_component": 0, "nonevent_component": 0, "total": 0})
        self.assertEqual(sum(output["details"]["movements"].values()), 4)

    def test_nri_identical_reference_has_zero_components_not_missing_results(self):
        context = {**self.context, "reference_probabilities": list(self.context["y_probability"])}
        output = self.run_block("categorical_nri", context, {"risk_thresholds": [0.2, 0.5]})
        self.assertEqual(output["status"], "completed")
        self.assertEqual(output["value"], {"event_component": 0, "nonevent_component": 0, "total": 0})

    def test_nri_negative_reclassification_is_preserved(self):
        context = {
            **self.context, "y_true": [1, 1, 0, 0],
            "reference_probabilities": [1, 1, 0, 0], "y_probability": [0, 0, 1, 1],
        }
        self.assertEqual(self.run_block("categorical_nri", context, {"risk_thresholds": [0.5]})["value"]["total"], -2)

    def test_top_fraction_is_required_and_must_be_explicitly_valid(self):
        for identifier in ("recall_at_k", "precision_at_k"):
            self.assert_unavailable(self.run_block(identifier), "missing_parameters")
            for value in (0, -0.1, 1.1, True, "0.5", float("nan"), float("inf")):
                with self.subTest(identifier=identifier, value=value), self.assertRaisesRegex(ValueError, "top_fraction"):
                    self.run_block(identifier, parameters={"top_fraction": value})

    def test_top_k_reports_stable_tie_alert_budget_and_captured_count(self):
        context = {"y_true": [0, 1, 1, 0, 1], "y_probability": [0.9, 0.5, 0.5, 0.5, 0.1]}
        recall = self.run_block("recall_at_k", context, {"top_fraction": 0.4})
        precision = self.run_block("precision_at_k", context, {"top_fraction": 0.4})
        for output in (recall, precision):
            self.assertEqual(output["details"]["selected_indices"], [0, 1])
            self.assertEqual(output["details"]["alert_count"], 2)
            self.assertEqual(output["details"]["captured_positives"], 1)
            self.assertTrue(output["details"]["boundary_tie"])
            self.assertIn("stable", output["details"]["tie_policy"])
        self.assertAlmostEqual(recall["value"], 1 / 3)
        self.assertEqual(precision["value"], 0.5)

    def test_top_k_constant_probabilities_preserve_row_order(self):
        context = {"y_true": [0, 1, 1, 0], "y_probability": [0.5] * 4}
        output = self.run_block("precision_at_k", context, {"top_fraction": 0.5})
        self.assertEqual(output["details"]["selected_indices"], [0, 1])
        self.assertEqual(output["value"], 0.5)
        self.assertTrue(output["details"]["boundary_tie"])

    def test_top_k_rounds_up_to_at_least_one_alert(self):
        context = {"y_true": [0, 1, 0], "y_probability": [0.1, 0.9, 0.2]}
        for fraction, expected in ((0.01, 1), (0.5, 2), (1, 3)):
            with self.subTest(fraction=fraction):
                output = self.run_block("precision_at_k", context, {"top_fraction": fraction})
                self.assertEqual(output["details"]["alert_count"], expected)
                self.assertEqual(output["details"]["captured_positives"], 1)
                self.assertEqual(output["value"], 1 / expected)

    def test_top_recall_no_positives_is_undefined_but_precision_can_be_zero(self):
        context = {"y_true": [0, 0], "y_probability": [0.3, 0.5]}
        recall = self.run_block("recall_at_k", context, {"top_fraction": 0.5})
        self.assert_unavailable(recall, "zero_denominator")
        self.assertEqual(recall["details"]["alert_count"], 1)
        self.assertEqual(recall["details"]["captured_positives"], 0)
        precision = self.run_block("precision_at_k", context, {"top_fraction": 0.5})
        self.assertEqual(precision["status"], "completed")
        self.assertEqual(precision["value"], 0)

    def test_all_results_are_strictly_json_serializable(self):
        completed = metrics.run_metrics(list(IDS), self.context, self.parameters)
        missing = metrics.run_metrics(list(IDS), {})
        json.dumps({"completed": completed, "missing": missing}, allow_nan=False)
        self.assertEqual(len(completed), 15)
        self.assertTrue(all(item["status"] == "completed" for item in completed))
        self.assertTrue(all(item["status"] == "not_applicable" for item in missing))

    def test_evaluation_does_not_mutate_context_parameters_or_input_order(self):
        original_context = copy.deepcopy(self.context)
        original_parameters = copy.deepcopy(self.parameters)
        metrics.run_metrics(list(IDS), self.context, self.parameters)
        self.assertEqual(self.context, original_context)
        self.assertEqual(self.parameters, original_parameters)

    def test_supplied_model_objects_are_ignored_without_load_fit_or_predict(self):
        class ForbiddenModel:
            def __getattribute__(self, name):
                raise AssertionError("Evaluation must not access a model object")

        context = {
            **self.context, "model": ForbiddenModel(),
            "reference_model": ForbiddenModel(),
            "model_path": "synthetic-model-must-not-be-opened.joblib",
        }
        outputs = metrics.run_metrics(list(IDS), context, self.parameters)
        self.assertTrue(all(item["status"] == "completed" for item in outputs))

    def test_metric_library_never_imports_model_loaders_or_calls_models(self):
        forbidden_imports = {
            "pickle", "joblib", "sklearn", "numpy", "scipy", "pandas", "torch",
            "xgboost", "lightgbm", "app.assets", "app.infer", "app.bootstrap",
        }
        forbidden_calls = {"fit", "fit_transform", "predict", "predict_proba", "load_model", "eval", "exec", "compile"}
        for path in (ROOT / "app" / "blocks" / "metrics").glob("*.py"):
            with self.subTest(file=path.name):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        self.assertFalse(any(item.name.split(".")[0] in forbidden_imports for item in node.names))
                    elif isinstance(node, ast.ImportFrom):
                        self.assertNotIn(node.module, forbidden_imports)
                    elif isinstance(node, ast.Call):
                        if isinstance(node.func, ast.Attribute):
                            self.assertNotIn(node.func.attr, forbidden_calls)
                        elif isinstance(node.func, ast.Name):
                            self.assertNotIn(node.func.id, forbidden_calls)

    def test_import_catalog_and_execution_work_without_site_packages_in_subprocess(self):
        script = r"""
import builtins
import json
import sys
original_import = builtins.__import__
forbidden = {"numpy", "scipy", "sklearn", "pandas", "joblib", "pickle", "torch", "xgboost", "lightgbm"}
def guarded_import(name, *args, **kwargs):
    if name.split(".")[0] in forbidden:
        raise AssertionError("Unexpected ML/model import: " + name)
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
import app.blocks.metrics as metrics
assert not any(name.startswith("app.blocks.metrics.") for name in sys.modules)
catalog = metrics.load_catalog()
assert not any(name.startswith("app.blocks.metrics.") for name in sys.modules)
assert not forbidden.intersection(sys.modules)
context = {
    "y_true": [0, 1], "y_probability": [0.1, 0.9], "reference_probabilities": [0.6, 0.4],
    "sample_ids": ["synthetic-a", "synthetic-b"], "reference_sample_ids": ["synthetic-a", "synthetic-b"],
    "clinical_action": "Synthetic review only", "comparison_model": "synthetic-reference",
}
parameters = {}
for entry in catalog["metrics"]:
    supplied = {"threshold": 0.5, "thresholds": [0.25, 0.5], "risk_thresholds": [0.5], "top_fraction": 0.5}
    parameters[entry["id"]] = {key: supplied[key] for key in entry["requires_parameters"]}
results = metrics.run_metrics([entry["id"] for entry in catalog["metrics"]], context, parameters)
assert all(item["status"] == "completed" for item in results)
assert not forbidden.intersection(sys.modules)
print(json.dumps({"synthetic_only": True, "without_site_packages": True, "completed": len(results)}, allow_nan=False))
"""
        process = subprocess.run(
            [sys.executable, "-B", "-S", "-c", script], cwd=ROOT,
            capture_output=True, text=True, encoding="utf-8", timeout=30, check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout), {
            "synthetic_only": True, "without_site_packages": True, "completed": 15,
        })


if __name__ == "__main__":
    unittest.main()
