"""Synthetic-only tests: no model, AI, network, workflow or clinical execution."""

import ast
import copy
import importlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from app import executor_eval
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
        "y_true": [0, 1, 0, 1], "y_probability": [0.1, 0.8, 0.6, 0.4],
        "reference_probabilities": [0.2, 0.3, 0.7, 0.9],
        "sample_ids": ["synthetic-a", "synthetic-b", "synthetic-c", "synthetic-d"],
        "reference_sample_ids": ["synthetic-a", "synthetic-b", "synthetic-c", "synthetic-d"],
        "clinical_action": "Synthetic additional review only",
        "comparison_model": "synthetic-reference-probabilities-only",
    }


def explicit_parameters():
    return {
        **{identifier: {"threshold": 0.5} for identifier in THRESHOLD_IDS},
        "calibration_curve": {"n_bins": 5},
        "decision_curve": {"thresholds": [0.2, 0.5, 0.8]},
        "categorical_nri": {"risk_thresholds": [0.2, 0.5, 0.8]},
        "recall_at_k": {"top_fraction": 0.5},
        "precision_at_k": {"top_fraction": 0.5},
    }


def metadata_plan(**changes):
    plan = {
        "schema_version": 1, "stage": "evaluation", "ai_status": "provided",
        "planning_complete": True, "selected_ids": ["brier_score"], "threshold": None,
        "parameters": {}, "clinical_action": None, "comparison_model": None,
    }
    plan.update(changes)
    return plan


def built_plan(selection=None, policy=None, *, provided=True):
    from app import planning_eval

    if selection is None:
        selection = planning_eval.default_selection()
    pending = planning_eval.build_plans({}, {}, selection, None, policy=policy)
    if not provided:
        return pending["evaluation"]
    # A synthetic recommendation fixture, not an AI invocation or authorship claim.
    recommendation = {
        "schema_version": 1, "provider": "github-copilot-cloud-agent", "status": "provided",
        "context_hash": pending["recommendation_request"]["context_hash"], "eda": [],
        "evaluation": [{
            "id": "brier_score", "reason": "Synthetic metric fixture.",
            "evidence": ["data_id"],
        }],
    }
    return planning_eval.build_plans(
        {}, {}, selection, recommendation, policy=policy,
    )["evaluation"]


class ExecuteMetricsTests(unittest.TestCase):
    def setUp(self):
        self.context = synthetic_context()
        self.parameters = explicit_parameters()

    def test_all_fifteen_metrics_reuse_registry_and_keep_aggregate_results(self):
        expected = metrics.run_metrics(list(IDS), self.context, self.parameters)
        for item in expected:
            item["details"].pop("selected_indices", None)
        with patch.object(metrics, "run_metrics", wraps=metrics.run_metrics) as run:
            actual = executor_eval.execute_metrics(list(IDS), self.context, self.parameters)
        self.assertEqual(actual, expected)
        self.assertEqual(run.call_count, 1)
        self.assertEqual([item["block"] for item in actual], list(IDS))
        self.assertTrue(all(item["status"] == "completed" for item in actual))
        json.dumps(actual, allow_nan=False)

    def test_aliases_normalize_deduplicate_and_preserve_first_requested_order(self):
        module = importlib.import_module("app.blocks.metrics.precision")
        with patch.object(module, "run", wraps=module.run) as run:
            actual = executor_eval.execute_metrics(
                [" PPV ", "precision", "SENSITIVITY", "recall", "AP", "ap", "npv"],
                self.context, {
                    "PPV": {"threshold": 0.5}, "precision": {"threshold": 0.5},
                    " sensitivity ": {"threshold": 0.5}, "NPV": {"threshold": 0.5},
                },
            )
        self.assertEqual(run.call_count, 1)
        self.assertEqual([item["block"] for item in actual], [
            "precision", "recall", "average_precision", "negative_predictive_value",
        ])

    def test_conflicting_alias_parameters_fail_before_execution(self):
        with patch.object(metrics, "run_metrics") as run:
            with self.assertRaisesRegex(ValueError, "Conflicting"):
                executor_eval.execute_metrics(["ppv"], self.context, {
                    "ppv": {"threshold": 0.4}, "precision": {"threshold": 0.5},
                })
        run.assert_not_called()

    def test_unknown_ids_and_parameter_ids_are_not_echoed_or_executed(self):
        private = "private-sample-marker-[0.123456789]"
        for identifiers, parameters in (
            (["roc_auc", private], None), (["roc_auc"], {private: {}}),
            (["../accuracy"], None), (["app.blocks.metrics.accuracy"], None),
            ([None], None), ([""], None),
        ):
            with self.subTest(identifiers=identifiers), patch.object(metrics, "run_metrics") as run:
                with self.assertRaisesRegex(ValueError, "Unknown metric id") as raised:
                    executor_eval.execute_metrics(identifiers, self.context, parameters)
                self.assertNotIn(private, str(raised.exception))
                run.assert_not_called()

    def test_bad_api_shapes_fail_with_value_error(self):
        for identifiers in (None, "roc_auc", {"roc_auc"}, 1):
            with self.subTest(identifiers=identifiers), self.assertRaises(ValueError):
                executor_eval.execute_metrics(identifiers, self.context)
        for context in (None, [], "invalid"):
            with self.subTest(context=context), self.assertRaisesRegex(ValueError, "context"):
                executor_eval.execute_metrics([], context)
        for parameters in ([], "invalid", 1):
            with self.subTest(parameters=parameters), self.assertRaisesRegex(ValueError, "parameters_by_id"):
                executor_eval.execute_metrics([], self.context, parameters)
        with self.assertRaisesRegex(ValueError, "parameters"):
            executor_eval.execute_metrics(["accuracy"], self.context, {"accuracy": []})

    def test_unselected_registered_parameters_do_not_add_a_metric(self):
        output = executor_eval.execute_metrics(["brier"], self.context, {
            "accuracy": {"threshold": 0.5}, "brier_score": None,
        })
        self.assertEqual([item["block"] for item in output], ["brier_score"])
        with patch.object(metrics, "import_module") as importer:
            self.assertEqual(executor_eval.execute_metrics([], self.context, self.parameters), [])
        importer.assert_not_called()

    def test_explicit_parameters_are_validated_even_when_input_is_missing(self):
        invalid = (
            ("roc_auc", {"threshold": 0.5}),
            ("accuracy", {"thresholds": [0.5]}),
            ("accuracy", {"threshold": True}),
            ("accuracy", {"threshold": float("nan")}),
            ("accuracy", {"threshold": 1.1}),
            ("calibration_curve", {"n_bins": None}),
            ("calibration_curve", {"n_bins": 0}),
            ("calibration_curve", {"n_bins": True}),
            ("decision_curve", {"thresholds": [0.8, 0.2]}),
            ("decision_curve", {"thresholds": [0.5, 0.5]}),
            ("categorical_nri", {"risk_thresholds": [0, 1]}),
            ("recall_at_k", {"top_fraction": 0}),
            ("precision_at_k", {"top_fraction": float("inf")}),
        )
        for identifier, parameters in invalid:
            with self.subTest(identifier=identifier, parameters=parameters):
                with patch.object(metrics, "run_metrics") as run:
                    with self.assertRaises(ValueError):
                        executor_eval.execute_metrics([identifier], {}, {identifier: parameters})
                    run.assert_not_called()

    def test_unspecified_or_null_threshold_is_not_applicable_not_half(self):
        for parameters in (None, {}, {identifier: {"threshold": None} for identifier in THRESHOLD_IDS}):
            actual = executor_eval.execute_metrics(list(THRESHOLD_IDS), self.context, parameters)
            self.assertTrue(all(item["status"] == "not_applicable" for item in actual))
            for item in actual:
                self.assertIsNone(item["value"])
                self.assertEqual(item["details"]["reason"], "missing_parameters")
                self.assertEqual(item["details"]["missing_parameters"], ["threshold"])
        for threshold in (0, 1):
            actual = executor_eval.execute_metrics(
                ["accuracy"], self.context, {"accuracy": {"threshold": threshold}},
            )[0]
            self.assertEqual(actual["status"], "completed")
            self.assertEqual(actual["details"]["threshold"], threshold)

    def test_missing_prerequisites_and_undefined_results_respect_registered_blocks(self):
        self.assertEqual(
            executor_eval.execute_metrics(list(IDS), {}),
            metrics.run_metrics(list(IDS), {}),
        )
        contexts = [
            {**self.context, "y_true": [0, 0, 0, 0]},
            {**self.context, "y_true": [1, 1, 1, 1]},
            {**self.context, "y_true": [], "y_probability": [], "reference_probabilities": [],
             "sample_ids": [], "reference_sample_ids": []},
            {**self.context, "clinical_action": None, "comparison_model": None},
            {**self.context, "sample_ids": None},
        ]
        for context in contexts:
            with self.subTest(context=context):
                expected = metrics.run_metrics(list(IDS), context, self.parameters)
                for item in expected:
                    item["details"].pop("selected_indices", None)
                self.assertEqual(
                    executor_eval.execute_metrics(list(IDS), context, self.parameters), expected,
                )

    def test_input_validation_errors_never_embed_raw_values(self):
        marker = "synthetic-private-row-marker"
        invalid_contexts = (
            {**self.context, "y_probability": [marker] * 4},
            {**self.context, "reference_probabilities": [marker] * 4},
            {**self.context, "sample_ids": [marker] * 4},
            {**self.context, "reference_sample_ids": [marker] * 4},
        )
        for context in invalid_contexts:
            with self.subTest(context=context), self.assertRaises(ValueError) as raised:
                executor_eval.execute_metrics(list(IDS), context, self.parameters)
            self.assertNotIn(marker, str(raised.exception))

    def test_top_fraction_row_positions_are_removed_without_mutating_block_result(self):
        raw = metrics.run_metrics(
            ["recall_at_k"], self.context, {"recall_at_k": {"top_fraction": 0.5}},
        )
        original = copy.deepcopy(raw)
        self.assertIn("selected_indices", raw[0]["details"])
        with patch.object(metrics, "run_metrics", return_value=raw):
            output = executor_eval.execute_metrics(
                ["recall_at_k"], self.context, {"recall_at_k": {"top_fraction": 0.5}},
            )[0]
        self.assertNotIn("selected_indices", output["details"])
        self.assertEqual(output["details"]["alert_count"], 2)
        self.assertEqual(raw, original)

    def test_context_parameters_and_identifier_order_are_not_mutated(self):
        identifiers = list(reversed(IDS))
        originals = copy.deepcopy((identifiers, self.context, self.parameters))
        executor_eval.execute_metrics(identifiers, self.context, self.parameters)
        self.assertEqual((identifiers, self.context, self.parameters), originals)

    def test_model_objects_are_not_accessed_or_copied(self):
        class ForbiddenModel:
            def __getattribute__(self, name):
                raise AssertionError("No model object access is permitted")

        context = {
            **self.context, "model": ForbiddenModel(), "reference_model": ForbiddenModel(),
            "model_path": "synthetic-never-opened.joblib",
        }
        output = executor_eval.execute_metrics(list(IDS), context, self.parameters)
        self.assertTrue(all(item["status"] == "completed" for item in output))


class OutputContractTests(unittest.TestCase):
    def setUp(self):
        self.context = synthetic_context()
        self.result = metrics.run_metrics(["brier_score"], self.context)[0]

    def assert_invalid(self, result, identifiers=None, parameters=None):
        with patch.object(metrics, "run_metrics", return_value=result):
            with self.assertRaisesRegex(ValueError, "Invalid registered metric output") as raised:
                executor_eval.execute_metrics(
                    identifiers or ["brier_score"], self.context, parameters,
                )
        self.assertNotIn("private-row-marker", str(raised.exception))

    def test_invalid_envelope_count_order_block_status_and_details_fail_explicitly(self):
        for result in (None, (), {}, [], [self.result, self.result], [None], [[]]):
            with self.subTest(result=result):
                self.assert_invalid(result)
        for field, value in (
            ("block", "brier"), ("status", "passed"), ("value", [0, 1]),
            ("value", float("nan")), ("value", True), ("details", []),
        ):
            with self.subTest(field=field):
                self.assert_invalid([{**self.result, field: value}])
        for field in self.result:
            changed = copy.deepcopy(self.result)
            del changed[field]
            with self.subTest(missing=field):
                self.assert_invalid([changed])
        self.assert_invalid([{**self.result, "rows": ["private-row-marker"]}])

    def test_missing_or_wrong_aggregate_detail_fields_are_rejected(self):
        for field, value in (
            ("sample_count", [1, 2, 3]), ("definition", {"sample_ids": ["private-row-marker"]}),
            ("denominator", float("inf")), ("reason", "missing_inputs"),
        ):
            with self.subTest(field=field):
                changed = copy.deepcopy(self.result)
                changed["details"][field] = value
                self.assert_invalid([changed])
        changed = copy.deepcopy(self.result)
        del changed["details"]["definition"]
        self.assert_invalid([changed])

    def test_forbidden_raw_arrays_and_row_data_are_rejected_not_published(self):
        for name in (
            "y_true", "y_probability", "sample_ids", "reference_sample_ids",
            "reference_probabilities", "predictions", "rows", "selected_indices",
        ):
            changed = copy.deepcopy(self.result)
            changed["details"][name] = ["private-row-marker"]
            with self.subTest(name=name):
                self.assert_invalid([changed])

    def test_nested_curve_confusion_and_nri_row_payloads_are_rejected(self):
        parameters = explicit_parameters()
        for identifier in ("calibration_curve", "confusion_matrix", "decision_curve", "categorical_nri"):
            changed = metrics.run_metrics([identifier], self.context, parameters)
            value = changed[0]["value"]
            target = value[0] if isinstance(value, list) else value
            target["sample_ids"] = ["private-row-marker"]
            with self.subTest(identifier=identifier):
                self.assert_invalid(changed, [identifier], parameters)

    def test_wrong_nested_aggregate_shapes_fail_with_value_error(self):
        for identifier, transform in (
            ("calibration_curve", lambda value: value.pop()),
            ("confusion_matrix", lambda value: value.update(matrix=[[1, 2, 3], [4, 5, 6]])),
            ("confusion_matrix", lambda value: value.update(labels=[False, True])),
            ("categorical_nri", lambda value: value.update(total=[0.1, 0.2])),
            ("decision_curve", lambda value: value[0].update(model_net_benefit=float("inf"))),
        ):
            changed = metrics.run_metrics([identifier], self.context, explicit_parameters())
            transform(changed[0]["value"])
            with self.subTest(identifier=identifier):
                self.assert_invalid(changed, [identifier], explicit_parameters())

    def test_invalid_not_applicable_results_are_not_silently_coerced(self):
        base = {
            "block": "brier_score", "status": "not_applicable", "value": None,
            "details": {"reason": "missing_inputs", "missing_inputs": ["y_true"]},
        }
        for field, value in (
            ("value", 0), ("details", {}), ("details", {"reason": "unknown"}),
            ("details", {"reason": ["private-row-marker"]}),
            ("details", {"reason": "missing_inputs", "missing_inputs": ["private-row-marker"]}),
        ):
            with self.subTest(field=field, value=value):
                self.assert_invalid([{**base, field: value}])

    def test_unexpected_registered_runtime_errors_are_not_broadly_caught(self):
        with patch.object(metrics, "run_metrics", side_effect=RuntimeError("synthetic bug")):
            with self.assertRaisesRegex(RuntimeError, "synthetic bug"):
                executor_eval.execute_metrics(["brier_score"], self.context)


class ExecutePlanTests(unittest.TestCase):
    def setUp(self):
        self.context = synthetic_context()

    def test_real_build_plans_metadata_executes_fifteen_metrics_without_model_io(self):
        from app import planning_eval

        selection = planning_eval.default_selection()
        selection["evaluation"].update({
            "add": list(IDS), "threshold": 0.5,
            "parameters": {
                "n_bins": 5, "thresholds": [0.2, 0.5, 0.8],
                "risk_thresholds": [0.2, 0.5, 0.8], "top_fraction": 0.5,
            },
            "clinical_action": self.context["clinical_action"],
            "comparison_model": self.context["comparison_model"],
        })
        plan = built_plan(selection)
        self.assertEqual(plan["parameters"]["threshold"], plan["threshold"])
        original = copy.deepcopy((plan, self.context))
        actual = executor_eval.execute_plan(plan, self.context)
        expected = executor_eval.execute_metrics(list(IDS), self.context, explicit_parameters())
        self.assertEqual(actual, expected)
        self.assertEqual((plan, self.context), original)
        self.assertTrue(plan["approval_required"])
        self.assertFalse(plan["clinical_use_approved"])

    def test_real_pending_plan_is_rejected_without_calculation(self):
        with patch.object(metrics, "run_metrics") as run:
            with self.assertRaisesRegex(ValueError, "planning_complete"):
                executor_eval.execute_plan(built_plan(provided=False), self.context)
        run.assert_not_called()

    def test_plan_uses_selected_ids_not_policy_defaults_or_items(self):
        from app import planning_eval

        policy = planning_eval.load_policy()
        policy["evaluation"]["defaults"] = ["brier_score"]
        plan = built_plan(policy=policy)
        actual = executor_eval.execute_plan(plan, self.context)
        self.assertEqual([item["block"] for item in actual], ["brier_score"])
        self.assertEqual(executor_eval.execute_plan(
            metadata_plan(selected_ids=[], items=[{"id": "roc_auc"}]), self.context,
        ), [])

    def test_plan_routes_flat_parameters_only_to_applicable_blocks(self):
        plan = metadata_plan(
            selected_ids=["AP", "sensitivity", "PPV", "calibration_curve", "recall_at_k"],
            threshold=0, parameters={"n_bins": 3, "top_fraction": 0.5},
        )
        with patch.object(executor_eval, "execute_metrics", wraps=executor_eval.execute_metrics) as run:
            actual = executor_eval.execute_plan(plan, self.context)
        self.assertEqual(run.call_args.args[2], {
            "average_precision": {}, "recall": {"threshold": 0.0},
            "precision": {"threshold": 0.0}, "calibration_curve": {"n_bins": 3},
            "recall_at_k": {"top_fraction": 0.5},
        })
        self.assertTrue(all(item["status"] == "completed" for item in actual))

    def test_unspecified_plan_threshold_stays_not_applicable_even_with_policy_metadata(self):
        plan = metadata_plan(
            selected_ids=["accuracy"], defaults=["accuracy"], items=[{"state": "blocked"}],
        )
        result = executor_eval.execute_plan(plan, {**self.context, "threshold": 0.5})[0]
        self.assertEqual(result["status"], "not_applicable")
        self.assertEqual(result["details"]["reason"], "missing_parameters")
        self.assertIsNone(plan["threshold"])

    def test_explicit_flat_parameter_threshold_and_redundant_normalized_threshold(self):
        for threshold, parameters in (
            (None, {"threshold": 0.4}), (0.4, {}), (0.4, {"threshold": 0.4}),
        ):
            with self.subTest(threshold=threshold):
                plan = metadata_plan(
                    selected_ids=["accuracy"], threshold=threshold, parameters=parameters,
                )
                result = executor_eval.execute_plan(plan, self.context)[0]
                self.assertEqual(result["details"]["threshold"], 0.4)
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            executor_eval.execute_plan(metadata_plan(
                threshold=0.5, parameters={"threshold": 0.4},
            ), self.context)

    def test_plan_context_descriptions_merge_only_explicit_values_and_reject_conflicts(self):
        plan = metadata_plan(
            selected_ids=["dca", "nri"],
            parameters={"thresholds": [0.5], "risk_thresholds": [0.5]},
            clinical_action=self.context["clinical_action"],
            comparison_model=self.context["comparison_model"],
        )
        without_descriptions = {
            key: value for key, value in self.context.items()
            if key not in ("clinical_action", "comparison_model")
        }
        expected = executor_eval.execute_plan(plan, self.context)
        self.assertEqual(executor_eval.execute_plan(plan, without_descriptions), expected)
        self.assertNotIn("clinical_action", without_descriptions)
        self.assertNotIn("comparison_model", without_descriptions)
        for name in ("clinical_action", "comparison_model"):
            with self.subTest(name=name), patch.object(metrics, "run_metrics") as run:
                with self.assertRaisesRegex(ValueError, f"Conflicting.*{name}"):
                    executor_eval.execute_plan(plan, {**self.context, name: "different explicit choice"})
                run.assert_not_called()

    def test_null_plan_descriptions_keep_existing_context_and_never_fabricate_missing_context(self):
        plan = metadata_plan(
            selected_ids=["dca", "nri"],
            parameters={"thresholds": [0.5], "risk_thresholds": [0.5]},
        )
        self.assertTrue(all(item["status"] == "completed" for item in
                            executor_eval.execute_plan(plan, self.context)))
        context = {**self.context, "clinical_action": None, "comparison_model": None}
        actual = executor_eval.execute_plan(plan, context)
        for item in actual:
            self.assertEqual(item["status"], "not_applicable")
            self.assertEqual(item["details"]["reason"], "missing_context")

    def test_comparison_description_does_not_fabricate_reference_arrays(self):
        context = {key: value for key, value in self.context.items()
                   if key not in ("reference_probabilities", "reference_sample_ids")}
        plan = metadata_plan(
            selected_ids=["nri"], parameters={"risk_thresholds": [0.5]},
            comparison_model=context["comparison_model"],
        )
        result = executor_eval.execute_plan(plan, context)[0]
        self.assertEqual(result["status"], "not_applicable")
        self.assertEqual(result["details"]["reason"], "missing_inputs")

    def test_malformed_plan_metadata_is_rejected_before_execution(self):
        variants = [None, [], {}, {"evaluation": metadata_plan()}]
        for field, value in (
            ("schema_version", True), ("schema_version", 2), ("stage", "eda"),
            ("ai_status", "pending"), ("ai_status", "stale"), ("planning_complete", False),
            ("planning_complete", 1), ("selected_ids", "accuracy"),
            ("selected_ids", ["unknown"]), ("parameters", None), ("parameters", []),
            ("parameters", {"accuracy": {"threshold": 0.5}}),
            ("parameters", {"threshold": None}), ("parameters", {"n_bins": True}),
            ("threshold", True), ("threshold", float("inf")), ("threshold", -0.1),
            ("clinical_action", ""), ("comparison_model", True),
        ):
            variants.append(metadata_plan(**{field: value}))
        for field in metadata_plan():
            variant = metadata_plan()
            del variant[field]
            variants.append(variant)
        for plan in variants:
            with self.subTest(plan=plan), patch.object(metrics, "run_metrics") as run:
                with self.assertRaises(ValueError):
                    executor_eval.execute_plan(plan, self.context)
                run.assert_not_called()
        for context in (None, [], "invalid"):
            with self.subTest(context=context), self.assertRaisesRegex(ValueError, "context"):
                executor_eval.execute_plan(metadata_plan(), context)

    def test_malformed_context_descriptions_are_not_overwritten(self):
        for name in ("clinical_action", "comparison_model"):
            for value in ("", " ", False, []):
                with self.subTest(name=name, value=value):
                    with self.assertRaisesRegex(ValueError, name):
                        executor_eval.execute_plan(metadata_plan(), {**self.context, name: value})


class ExecutorIsolationTests(unittest.TestCase):
    def test_import_and_all_fifteen_calculations_work_without_site_packages(self):
        script = r"""
import json
import sys
from app import executor_eval
from app.blocks import metrics
forbidden = {"numpy", "pandas", "scipy", "sklearn", "joblib", "pickle", "torch",
             "xgboost", "lightgbm", "requests", "httpx", "socket", "subprocess"}
context = {
    "y_true": [0, 1], "y_probability": [0.1, 0.9],
    "reference_probabilities": [0.6, 0.4],
    "sample_ids": ["synthetic-a", "synthetic-b"],
    "reference_sample_ids": ["synthetic-a", "synthetic-b"],
    "clinical_action": "Synthetic review", "comparison_model": "synthetic-reference",
}
parameters = {}
for entry in metrics.load_catalog()["metrics"]:
    values = {"threshold": 0.5, "thresholds": [0.25, 0.5],
              "risk_thresholds": [0.5], "top_fraction": 0.5}
    parameters[entry["id"]] = {name: values[name] for name in entry["requires_parameters"]}
results = executor_eval.execute_metrics(list(parameters), context, parameters)
assert len(results) == 15 and all(item["status"] == "completed" for item in results)
assert not forbidden.intersection(sys.modules)
assert "app.planning_eval" not in sys.modules
assert "selected_indices" not in json.dumps(results)
print(json.dumps({"synthetic_only": True, "completed": len(results)}, allow_nan=False))
"""
        process = subprocess.run(
            [sys.executable, "-B", "-S", "-c", script], cwd=ROOT,
            capture_output=True, text=True, encoding="utf-8", timeout=30, check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout), {"synthetic_only": True, "completed": 15})

    def test_executor_only_imports_standard_library_and_existing_metrics(self):
        tree = ast.parse((ROOT / "app" / "executor_eval.py").read_text(encoding="utf-8"))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module)
            elif isinstance(node, ast.ExceptHandler):
                self.assertIsNotNone(node.type)
                self.assertNotIn(getattr(node.type, "id", ""), ("Exception", "BaseException"))
        self.assertEqual(set(imports), {
            "math", "numbers", "app.blocks", "app.blocks.metrics",
        })
        self.assertEqual(executor_eval.__all__, ["execute_metrics", "execute_plan"])

    def test_documented_scope_does_not_equate_completion_with_approval(self):
        self.assertIn("neither human approval nor clinical", executor_eval.__doc__)
        self.assertIn("future upper-level caller", executor_eval.__doc__)
        self.assertIn("not connected to the current Actions path", executor_eval.__doc__)
        self.assertIn("not to establish", executor_eval.execute_plan.__doc__)


if __name__ == "__main__":
    unittest.main()
