"""Synthetic startup-plan, registered-tool and reuse contract regressions."""

from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from app import executor_eda, input_contracts, planning_eda, planning_eval
from app.asset_contract import canonical_hash
from test_research_workflow import context_and_source


class EdaExecutionPlanTests(unittest.TestCase):
    def setUp(self):
        self.context, _ = context_and_source()
        self.required = ["schema", "patient_overlap"]

    def plan(self, ids=None):
        return planning_eda.build_execution_plan(self.context, ids or self.required)

    def reseal(self, plan):
        plan["plan_hash"] = canonical_hash({key: value for key, value in plan.items() if key != "plan_hash"})
        return plan

    def test_planning_is_metadata_only_and_does_not_execute(self):
        with patch.dict(executor_eda.CHECKS, {"schema": Mock(side_effect=AssertionError("planning executed"))}):
            plan = self.plan()
        self.assertEqual([item["tool_id"] for item in plan["calls"]], self.required)
        self.assertEqual(plan["calls"][0]["inputs"]["feature_rows"], "dataset.feature_rows")
        self.assertNotIn("metadata", plan["input_signature"])
        self.assertNotIn("feature_values", json.dumps(plan))
        self.assertEqual(planning_eda.validate_execution_plan(plan), plan)

    def test_required_unknown_duplicate_and_wrong_stage_are_rejected(self):
        with self.assertRaises(ValueError):
            self.plan(["schema"])
        with self.assertRaises(ValueError):
            self.plan(["schema", "patient_overlap", "not_registered"])
        for change in ("duplicate", "wrong_stage", "wrong_input", "parameters"):
            plan = self.plan()
            if change == "duplicate":
                plan["calls"].append(deepcopy(plan["calls"][0]))
            elif change == "wrong_stage":
                plan["stage"] = "evaluation"
            elif change == "wrong_input":
                plan["calls"][0]["inputs"]["metadata"] = "arbitrary.file"
            else:
                plan["calls"][0]["parameters"] = {"threshold": 0.5}
            self.reseal(plan)
            with self.subTest(change=change), self.assertRaises(ValueError):
                planning_eda.validate_execution_plan(plan)

    def test_changed_input_or_missing_required_role_stops_execution(self):
        plan = self.plan()
        changed = deepcopy(self.context)
        changed["expected_feature_columns"] = list(reversed(changed["feature_columns"]))
        with self.assertRaisesRegex(ValueError, "feature"):
            executor_eda.execute_plan(plan, changed)
        changed = deepcopy(self.context)
        changed["metadata"][0].pop("patient_key")
        with self.assertRaisesRegex(ValueError, "role"):
            executor_eda.execute_plan(plan, changed)

    def test_execute_once_then_reuse_without_recalculating(self):
        plan = self.plan()
        measured = Mock(wraps=executor_eda.CHECKS["schema"])
        with patch.dict(executor_eda.CHECKS, {"schema": measured}):
            first = executor_eda.execute_plan(plan, self.context)
            before = deepcopy(first)
            second = executor_eda.execute_plan(plan, self.context, reuse=first, reuse_plan=plan)
        self.assertEqual(measured.call_count, 1)
        self.assertEqual(first, before)
        self.assertEqual(second["results"], first["results"])
        self.assertEqual(second["reused_ids"], self.required)
        self.assertEqual(second["executed_ids"], [])
        self.assertFalse(second["model_execution_performed"])

    def test_only_newly_selected_block_is_computed(self):
        old = self.plan()
        first = executor_eda.execute_plan(old, self.context)
        new = self.plan(self.required + ["demographics"])
        demographic = Mock(wraps=executor_eda.CHECKS["demographics"])
        with patch.dict(executor_eda.CHECKS, {
                "schema": Mock(side_effect=AssertionError("schema recomputed")),
                "patient_overlap": Mock(side_effect=AssertionError("overlap recomputed")),
                "demographics": demographic}):
            second = executor_eda.execute_plan(new, self.context, reuse=first, reuse_plan=old)
        self.assertEqual(demographic.call_count, 1)
        self.assertEqual(second["executed_ids"], ["demographics"])
        self.assertEqual(second["initial_execution_plan_hash"], old["plan_hash"])

    def test_changed_cache_and_policy_are_not_reused(self):
        plan = self.plan()
        result = executor_eda.execute_plan(plan, self.context)
        result["results"][0]["details"]["rows"] += 1
        with self.assertRaisesRegex(ValueError, "Cached"):
            executor_eda.execute_plan(plan, self.context, reuse=result, reuse_plan=plan)
        policy = planning_eval.load_policy()
        policy["eda"]["defaults"] = list(reversed(policy["eda"]["defaults"]))
        with self.assertRaisesRegex(ValueError, "policy"):
            planning_eda.validate_execution_plan(plan, policy=policy)

    def test_output_contract_rejects_individual_records(self):
        unsafe = {"block": "schema", "status": "completed",
                  "details": {"metadata": [{"patient_key": "synthetic-private"}]}}
        with patch.dict(executor_eda.CHECKS, {"schema": Mock(return_value=unsafe)}):
            with self.assertRaisesRegex(ValueError, "aggregates"):
                executor_eda.execute_plan(self.plan(), self.context)

    def test_missing_optional_evidence_is_unverifiable_not_a_fake_pass(self):
        result = executor_eda.execute_plan(self.plan(self.required + ["prediction_timing"]), self.context)
        self.assertEqual(result["results"][-1]["status"], "unverifiable")


class StartupProfileTests(unittest.TestCase):
    def test_startup_contract_comes_from_manifest_not_a_repository_active_profile(self):
        from test_input_contracts import legacy_manifest_fixture
        manifest = legacy_manifest_fixture()
        chosen = input_contracts.profile_for_data(manifest)
        self.assertEqual(chosen["profile_id"], "diabetes_prepared")
        self.assertEqual(chosen["data_id"], manifest["data_id"])
        chosen["bindings"].clear()
        self.assertTrue(input_contracts.profile_for_data(manifest)["bindings"])

    def test_bare_data_id_unknown_format_and_changed_manifest_are_rejected(self):
        from test_input_contracts import legacy_manifest_fixture
        manifest = legacy_manifest_fixture()
        for value in ("a" * 64, {}, {**manifest, "data_id": "f" * 64},
                      {**manifest, "kind": "unknown"}):
            with self.subTest(manifest=value), self.assertRaises(ValueError):
                input_contracts.profile_for_data(value)


if __name__ == "__main__":
    unittest.main()
