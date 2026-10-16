import copy
import hashlib
import json
import sys
import unittest

from app import planning_eval as planning, planning_eda as pr1_intent
from app.asset_contract import canonical_hash


def public_request():
    return {
        "schema_version": 1, "repository": "example/workshop", "issue_number": 7,
        "issue_url": "https://github.com/example/workshop/issues/7", "title": "합성 공개 연구",
        "research_goal": "공개 합성 자료로 재입원 예측의 코호트와 예측 시점을 점검합니다.",
        "research_question": "공개 이진 결과에서 확률 품질과 임계값별 성능은 어떠한가요?",
        "cohort_requirements": "등록된 고정 분할의 성인 코호트를 사용합니다.",
        "additional_requests": "후속 평가에서 표본 중복과 추적 관찰의 한계를 설명해 주세요.",
        "public_data_confirmation": True,
    }


def public_schema(known_mapping=True):
    roles = ("row_key", "patient_key", "split", "target", "race", "gender",
             "age_group", "race_original", "race_was_missing")
    columns = [
        {"name": role, "description": "합성 공개 컬럼의 역할 설명입니다.",
         "semantic_roles": [role], "data_type": "string" if index < 3 else "integer",
         "missing_count": 0}
        for index, role in enumerate(roles[:4])
    ]
    result = {
        "schema_version": 1, "stage": "input_mapping", "profile_id": "synthetic_public",
        "data_id": "1" * 64, "columns": columns, "required_roles": list(roles[:4]),
        "candidate_bindings": {role: [role] if role in roles[:4] else [] for role in roles},
        "target_definition": "등록된 공개 이진 결과이며 완전한 추적 관찰을 보장하지 않습니다.",
        "split_values": {"train": "train", "validation": "validation", "test": "test"},
        "target_values": {"negative": 0, "positive": 1},
        "allowed_evidence_ids": [f"column:{index}:{role}"
                                 for index, role in enumerate(roles[:4])],
        "known_mapping": known_mapping, "profile_hash": "2" * 64,
    }
    result["context_hash"] = canonical_hash(result)
    return result


def pending(identifier=100, body="공개 합성 연구에서 F1을 추가하고 AP를 주지표로 지정해 주세요."):
    return {"comment_id": identifier, "actor": "public-writer", "body": body,
            "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest()}


def refresh_schema_hash(schema):
    schema["context_hash"] = canonical_hash(
        {key: value for key, value in schema.items() if key != "context_hash"})


class IntentTests(unittest.TestCase):
    def setUp(self):
        self.request = public_request()
        self.schema = public_schema()
        self.selection = planning.default_selection()
        self.catalogs = {stage: planning.load_catalog(stage) for stage in ("eda", "evaluation")}
        self.policy = planning.load_policy()

    def context(self, feedback=None, selection=None):
        return pr1_intent.build_context(
            self.request, self.schema, self.selection if selection is None else selection,
            [] if feedback is None else feedback, catalogs=self.catalogs, policy=self.policy)

    def proposal(self, context, **updates):
        result = {
            "schema_version": 1, "context_hash": context["context_hash"],
            "eda_add": [], "objectives": {"eda": [], "evaluation": []},
            "feedback_ids": [item["comment_id"] for item in context["feedback"]],
            "selection_updates": {}, "reason": "공개 연구 목적과 승인된 선택 범위를 반영합니다.",
        }
        result.update(updates)
        return result

    def apply(self, context, proposal, selection=None):
        return pr1_intent.apply_proposal(
            self.selection if selection is None else selection, proposal, context,
            catalogs=self.catalogs, policy=self.policy)

    def validate(self, result, **overrides):
        arguments = {
            "record": result["record"], "selection": result["selection"],
            "request": self.request, "schema_request": self.schema,
            "catalogs": self.catalogs, "policy": self.policy,
        }
        arguments.update(overrides)
        return pr1_intent.validate_record(**arguments)

    def test_four_raw_public_form_texts_reach_agent_without_issue_metadata(self):
        self.request.update(
            title="제외되는 제목", raw_metadata={"patient_name": "never-copy"},
            patient_rows=[{"row_key": "never-copy-row"}],
            environment={"credential": "never-copy-credential"},
            token="ghp_" + "x" * 36,
        )
        context = self.context()
        self.assertEqual(context["request"], {
            key: self.request[key] for key in (
                "research_goal", "research_question", "cohort_requirements", "additional_requests")})
        serialized = json.dumps(context, ensure_ascii=False)
        for forbidden in ("never-copy", "제외되는 제목", self.request["token"],
                          "issue_url", "repository", "patient_rows", "credential", "raw_metadata"):
            self.assertNotIn(forbidden, serialized)
        self.assertIn(self.request["research_goal"], serialized)
        self.assertIn(self.request["research_question"], serialized)
        self.assertEqual(context["request_hash"], canonical_hash(context["request"]))

    def test_all_hashes_are_deterministic_independent_copies(self):
        feedback = [pending(102), pending(100)]
        context = self.context(feedback)
        same = self.context(list(reversed(feedback)))
        self.assertEqual(context, same)
        self.assertEqual(context["selection_hash"], canonical_hash(context["selection"]))
        self.assertEqual(context["schema_hash"], canonical_hash(self.schema))
        self.assertEqual(context["feedback_hash"], canonical_hash(context["feedback"]))
        self.assertEqual(context["context_hash"], canonical_hash(
            {key: value for key, value in context.items() if key != "context_hash"}))
        self.assertEqual(context["allowed_objectives"],
                         {stage: self.policy[stage]["allowed_objectives"]
                          for stage in ("eda", "evaluation")})
        self.assertEqual(context["catalogs"], self.catalogs)
        self.assertEqual(context["feedback"][0]["body"], feedback[1]["body"])
        context["request"]["research_goal"] = "수정된 복사본"
        context["selection"]["eda"]["add"].append("prediction_timing")
        context["feedback"][0]["body"] = "수정된 복사본"
        self.assertNotEqual(context["request"]["research_goal"], self.request["research_goal"])
        self.assertEqual(self.selection["eda"]["add"], [])
        self.assertNotEqual(context["feedback"][0]["body"], feedback[1]["body"])

    def test_request_hash_uses_only_four_form_fields_and_goal_changes_context(self):
        context = self.context()
        self.request.update(repository="another/repository", issue_number=99, title="다른 제목")
        self.assertEqual(self.context(), context)
        for field in ("research_goal", "research_question", "cohort_requirements",
                      "additional_requests"):
            original = self.request[field]
            self.request[field] += " 공개 연구 설명 추가"
            changed = self.context()
            self.assertNotEqual(changed["request_hash"], context["request_hash"])
            self.assertNotEqual(changed["context_hash"], context["context_hash"])
            self.request[field] = original

    def test_mapping_ready_and_unmapped_schema_both_reach_intent_agent(self):
        for known in (True, False):
            with self.subTest(known_mapping=known):
                self.schema = public_schema(known)
                context = self.context()
                self.assertEqual(context["schema_request"], self.schema)
                self.assertEqual(context["schema_request"]["known_mapping"], known)
                result = self.apply(context, self.proposal(context, eda_add=["timing"]))
                self.assertIn("prediction_timing", result["eda_ids"])

    def test_real_input_contract_builder_accepts_row_free_synthetic_summaries(self):
        from app import input_contracts

        from test_input_contracts import legacy_profile_fixture
        profile = legacy_profile_fixture()
        summaries = [
            {"name": name, "description": entry["description"],
             "semantic_roles": list(entry["semantic_roles"]), "data_type": "string",
             "missing_count": 0}
            for name, entry in profile["columns"].items()
        ]
        for known in (True, False):
            with self.subTest(known_mapping=known):
                if not known:
                    profile["bindings"] = None
                self.schema = input_contracts._schema_request(profile, summaries)
                context = self.context()
                self.assertEqual(context["schema_request"], self.schema)
                self.assertEqual(context["schema_request"]["known_mapping"], known)

    def test_public_confirmation_and_form_types_are_required(self):
        for key, value in (("schema_version", True), ("public_data_confirmation", False),
                           ("public_data_confirmation", 1), ("research_goal", ""),
                           ("research_question", None), ("cohort_requirements", ["raw row"])):
            with self.subTest(key=key, value=value):
                original = self.request[key]
                self.request[key] = value
                with self.assertRaises(ValueError):
                    self.context()
                self.request[key] = original
        del self.request["additional_requests"]
        with self.assertRaises(ValueError):
            self.context()

    def test_sensitive_public_strings_are_explicitly_rejected_not_redacted(self):
        suspicious = (
            "\x00", "-----BEGIN PRIVATE KEY-----", "-----BEGIN CERTIFICATE-----",
            "ghp_" + "A" * 36, "github_pat_" + "B" * 60, "person@example.test",
            "환자명: 합성개인", "환자 이름 = 합성개인", "patient_name: Example Person",
            "성명：합성개인", "api_key=example-secret", "Authorization: Bearer fake-secret",
        )
        for text in suspicious:
            with self.subTest(text=text[:20]):
                self.request["research_goal"] = "공개 합성 설명 " + text
                with self.assertRaises(ValueError) as failure:
                    self.context()
                self.assertNotIn(text, str(failure.exception))
        self.request = public_request()
        with self.assertRaises(ValueError):
            self.context([pending(body="공개 설명 person@example.test")])
        self.schema["columns"][0]["description"] = "patient_name: Example"
        refresh_schema_hash(self.schema)
        with self.assertRaises(ValueError):
            self.context()

    def test_oversized_and_combined_text_and_invalid_unicode_are_rejected(self):
        self.request["research_goal"] = "가" * 65537
        with self.assertRaises(ValueError):
            self.context()
        self.request["research_goal"] = "가" * 33000
        self.request["research_question"] = "나" * 33000
        with self.assertRaises(ValueError):
            self.context()
        self.request = public_request()
        with self.assertRaises(ValueError):
            self.context([pending(1, "가" * 33000), pending(2, "나" * 33000)])
        self.request["research_goal"] += "\ud800"
        with self.assertRaises(ValueError):
            self.context()

    def test_public_execution_contract_rejects_raw_rows_secrets_and_non_json_values(self):
        mutations = (
            lambda value: value.update(rows=[{"patient_key": "not-public"}]),
            lambda value: value["columns"][0].update(examples=["not-public"]),
            lambda value: value.update(patient_rows=[{"patient_key": "not-public"}]),
            lambda value: value.update(environment={"runtime": "not-public"}),
            lambda value: value.update(credentials={"key": "not-public"}),
            lambda value: value.update(execution={"raw_metadata": [{"patient_key": "raw"}]}),
            lambda value: value.update(execution={"patient_name": "not-public"}),
            lambda value: value.update(execution={"api_key": "not-public"}),
            lambda value: value.update(execution={"rows": [[1, 2]]}),
            lambda value: value.update(execution={"feature_rows": [[1, 2]]}),
            lambda value: value.update(execution={"expected_count": float("nan")}),
            lambda value: value.update(execution={"unsupported": object()}),
            lambda value: value.update(execution={1: "not-a-string-key"}),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.schema = public_schema()
                mutation(self.schema)
                with self.assertRaises(ValueError):
                    self.context()

    def test_schema_accepts_extended_or_wrapped_public_execution_contracts_unchanged(self):
        runtime = {
            "feature_columns": ["age_scaled", "admissions_scaled"],
            "expected_feature_columns": ["age_scaled", "admissions_scaled"],
            "model_ref": "ghcr.io/example/fixed-model@sha256:" + "a" * 64,
            "split_rows": {"train": 40, "validation": 10, "test": 10},
            "rows": 60,
        }
        input_schema = public_schema()
        for schema in (
                {**input_schema, **runtime},
                {"input_schema": input_schema, **runtime},
                {"columns": [{"name": "age_scaled", "type": "number"}], **runtime}):
            with self.subTest(schema_keys=list(schema)):
                self.schema = copy.deepcopy(schema)
                context = self.context()
                self.assertEqual(context["schema_request"], schema)
                self.assertEqual(context["schema_hash"], canonical_hash(schema))
                result = self.apply(context, self.proposal(context))
                self.assertEqual(result["record"]["schema_hash"], canonical_hash(schema))
                self.validate(result)
                context["schema_request"]["feature_columns"].append("copy_only")
                self.assertEqual(self.schema, schema)

    def test_schema_binding_hashes_all_runtime_fields_not_only_input_self_hash(self):
        self.schema.update(
            feature_columns=["a", "b"], expected_feature_columns=["a", "b"],
            model_ref="ghcr.io/example/fixed-model@sha256:" + "a" * 64)
        input_self_hash = self.schema["context_hash"]
        context = self.context()
        result = self.apply(context, self.proposal(context))
        for field, value in (
                ("feature_columns", ["b", "a"]),
                ("expected_feature_columns", ["a", "b", "c"]),
                ("model_ref", "ghcr.io/example/fixed-model@sha256:" + "b" * 64)):
            original = self.schema[field]
            self.schema[field] = value
            with self.subTest(field=field):
                changed = self.context()
                self.assertEqual(self.schema["context_hash"], input_self_hash)
                self.assertNotEqual(changed["schema_hash"], context["schema_hash"])
                self.assertNotEqual(changed["context_hash"], context["context_hash"])
                with self.assertRaises(ValueError):
                    self.validate(result)
            self.schema[field] = original
        self.validate(result)

    def test_opaque_input_schema_is_not_revalidated_by_the_intent_coordinator(self):
        self.schema = {
            "stage": "public_execution_contract",
            "input_schema": {"context_hash": "opaque-parent-validated-reference",
                             "columns": [{"name": "feature", "type": "number"}]},
            "model_ref": "ghcr.io/example/fixed-model@sha256:" + "a" * 64,
        }
        context = self.context()
        self.assertEqual(context["schema_request"], self.schema)
        self.assertEqual(context["schema_hash"], canonical_hash(self.schema))
        result = self.apply(context, self.proposal(context))
        self.validate(result)

    def test_pending_feedback_shape_ids_actors_and_body_hash_are_verified(self):
        invalid = (
            [pending(), pending()],
            [{**pending(), "comment_id": True}], [{**pending(), "comment_id": 0}],
            [{**pending(), "actor": "not a github login"}],
            [{**pending(), "body_sha256": "f" * 64}],
            [{**pending(), "body_sha256": "not-a-hash"}],
            [{**pending(), "body": "changed after retrieval"}],
            [{**pending(), "body": ""}],
            [{**pending(), "permission": "admin"}], {},
        )
        for feedback in invalid:
            with self.subTest(feedback=feedback):
                with self.assertRaises(ValueError):
                    self.context(feedback)

    def test_public_goal_can_add_separate_aliased_eda_and_union_objectives(self):
        self.selection["eda"]["add"] = ["label_observability"]
        self.selection["eda"]["exclude"] = ["cohort"]
        self.selection["evaluation"].update(
            add=["f1"], primary_metric="roc_auc", threshold=0.25,
            parameters={"n_bins": 9}, clinical_action="등록된 공개 검토 조건")
        before = copy.deepcopy(self.selection)
        context = self.context()
        proposal = self.proposal(
            context, eda_add=["timing", "prediction-timing", "numeric_contract", "lineage"],
            objectives={"eda": ["cohort_definition", "cohort-definition"],
                        "evaluation": ["threshold_performance"]})
        result = self.apply(context, proposal)
        self.assertEqual(result["record"]["eda_add"],
                         ["prediction_timing", "schema", "preprocessing_lineage"])
        self.assertEqual(result["eda_ids"], [
            "schema", "patient_overlap", "demographics", "prediction_timing",
            "preprocessing_lineage", "label_observability"])
        self.assertEqual(result["selection"]["eda"]["add"], ["label_observability"])
        self.assertEqual(result["selection"]["evaluation"]["add"], ["f1"])
        self.assertEqual(result["selection"]["evaluation"]["threshold"], 0.25)
        self.assertEqual(result["selection"]["evaluation"]["parameters"], {"n_bins": 9})
        for stage in ("eda", "evaluation"):
            self.assertTrue(set(before[stage]["objectives"]).issubset(
                result["selection"][stage]["objectives"]))
            self.assertEqual(result["record"]["objectives"][stage],
                             result["selection"][stage]["objectives"])
        self.assertEqual(self.selection, before)
        self.validate(result)

    def test_defaults_remain_four_and_ai_excluded_by_human_is_not_executed(self):
        self.selection["eda"]["exclude"] = ["prediction_timing"]
        context = self.context()
        result = self.apply(context, self.proposal(context, eda_add=["timing"]))
        self.assertEqual(result["eda_ids"], ["schema", "patient_overlap", "demographics", "cohort"])
        self.assertEqual(result["record"]["eda_add"], ["prediction_timing"])
        self.assertEqual(result["selection"]["eda"]["add"], [])
        self.assertIsNone(result["selection"]["evaluation"]["threshold"])

    def test_no_feedback_cannot_modify_any_protected_human_choice(self):
        context = self.context()
        patches = (
            {"eda": {"add": ["timing"]}}, {"eda": {"exclude": ["demographics"]}},
            {"evaluation": {"add": ["f1"]}},
            {"evaluation": {"exclude": ["brier"]}},
            {"evaluation": {"primary_metric": "ap"}},
            {"evaluation": {"threshold": 0.3}},
            {"evaluation": {"parameters": {"n_bins": 5}}},
            {"evaluation": {"clinical_action": "공개 환자 검토"}},
            {"evaluation": {"comparison_model": "공개 기준 모델"}},
        )
        for patch in patches:
            with self.subTest(patch=patch):
                with self.assertRaises(ValueError):
                    self.apply(context, self.proposal(context, selection_updates=patch))
        result = self.apply(context, self.proposal(
            context, objectives={"eda": [], "evaluation": ["threshold_performance"]}))
        self.assertIn("threshold_performance", result["selection"]["evaluation"]["objectives"])
        self.assertEqual(result["selection"]["evaluation"]["add"], [])

    def test_refreshed_goal_unions_hints_without_changing_accepted_human_choices(self):
        self.selection["eda"]["add"] = ["prediction_timing"]
        self.selection["eda"]["objectives"].append("cohort_definition")
        self.selection["evaluation"].update(
            add=["f1", "average_precision"], exclude=["brier_score"],
            primary_metric="average_precision", threshold=0.3, parameters={"n_bins": 8})
        self.selection["evaluation"]["objectives"].append("threshold_performance")
        self.request["research_goal"] += " 추가로 임상 효용의 계획 조건을 검토합니다."
        before = copy.deepcopy(self.selection)
        context = self.context()
        result = self.apply(context, self.proposal(
            context, objectives={"eda": ["outcome_observability"],
                                 "evaluation": ["clinical_utility"]}))
        expected = copy.deepcopy(before)
        expected["eda"]["objectives"].append("outcome_observability")
        expected["evaluation"]["objectives"].append("clinical_utility")
        self.assertEqual(result["selection"], expected)
        self.assertEqual(result["record"]["base_selection_hash"], canonical_hash(before))
        self.assertEqual(result["record"]["feedback_ids"], [])
        self.assertEqual(self.selection, before)
        self.validate(result)

    def test_exact_all_pending_feedback_ids_are_required(self):
        context = self.context([pending(100), pending(102)])
        for identifiers in ([], [100], [102, 102], [100, 102, 104], [True], ["100"]):
            with self.subTest(identifiers=identifiers):
                with self.assertRaises(ValueError):
                    self.apply(context, self.proposal(
                        context, feedback_ids=identifiers,
                        selection_updates={"evaluation": {"add": ["f1"]}}))
        result = self.apply(context, self.proposal(context, feedback_ids=[102, 100]))
        self.assertEqual(result["record"]["feedback_ids"], [100, 102])
        empty_context = self.context()
        with self.assertRaises(ValueError):
            self.apply(empty_context, self.proposal(empty_context, feedback_ids=[100]))

    def test_writer_feedback_adds_f1_ap_primary_and_explicit_threshold(self):
        self.selection["evaluation"]["exclude"] = ["average_precision", "recall"]
        self.selection["evaluation"]["parameters"] = {"n_bins": 8}
        context = self.context([pending()])
        proposal = self.proposal(context, selection_updates={
            "evaluation": {"add": ["F1", "sensitivity"], "primary_metric": "AP",
                           "threshold": 0.3},
            "eda": {"add": ["timing"], "exclude": ["demographics"]},
        })
        result = self.apply(context, proposal)
        metric = result["selection"]["evaluation"]
        self.assertEqual(metric["primary_metric"], "average_precision")
        self.assertEqual(metric["threshold"], 0.3)
        self.assertEqual(metric["parameters"], {"n_bins": 8})
        self.assertEqual(metric["add"], ["average_precision", "f1", "recall"])
        self.assertEqual(metric["exclude"], [])
        self.assertEqual(result["selection"]["eda"]["add"], ["prediction_timing"])
        self.assertNotIn("demographics", result["eda_ids"])
        self.assertNotIn("threshold", metric["parameters"])
        self.assertEqual(result["record"]["eda_add"], [])
        plans = planning.build_plans(
            {}, {}, result["selection"], None, catalogs=self.catalogs, policy=self.policy)
        self.assertIn("f1", plans["evaluation"]["selected_ids"])
        self.assertEqual(plans["evaluation"]["primary_metric"], "average_precision")
        self.assertEqual(plans["evaluation"]["parameters"]["threshold"], 0.3)
        f1_item = next(item for item in plans["evaluation"]["items"] if item["id"] == "f1")
        self.assertEqual(f1_item["sources"], ["user"])
        self.validate(result)

    def test_sequential_feedback_preserves_previous_f1_and_other_parameters(self):
        first = self.context([pending()])
        result = self.apply(first, self.proposal(first, selection_updates={
            "evaluation": {"add": ["f1"], "parameters": {"n_bins": 8, "threshold": 0.3}}}))
        next_selection = result["selection"]
        second = self.context([pending(101, "합성 공개 연구에서 AP를 주지표로 변경해 주세요.")],
                              selection=next_selection)
        result = self.apply(second, self.proposal(second, selection_updates={
            "evaluation": {"primary_metric": "ap", "parameters": {"top_fraction": 0.2}}}),
                            selection=next_selection)
        metric = result["selection"]["evaluation"]
        self.assertEqual(metric["add"], ["f1", "average_precision"])
        self.assertEqual(metric["threshold"], 0.3)
        self.assertEqual(metric["parameters"], {"n_bins": 8, "top_fraction": 0.2})
        self.assertEqual(result["record"]["feedback_ids"], [101])
        self.assertNotIn("threshold", metric["parameters"])
        self.validate(result)

    def test_add_exclude_patches_merge_and_remove_from_opposite_list(self):
        self.selection["evaluation"].update(add=["f1", "recall"], exclude=["precision"])
        context = self.context([pending()])
        result = self.apply(context, self.proposal(context, selection_updates={
            "evaluation": {"add": ["ppv"], "exclude": ["sensitivity"]}}))
        self.assertEqual(result["selection"]["evaluation"]["add"], ["f1", "precision"])
        self.assertEqual(result["selection"]["evaluation"]["exclude"], ["recall"])
        with self.assertRaises(ValueError):
            self.apply(context, self.proposal(context, selection_updates={
                "evaluation": {"add": ["sensitivity"], "exclude": ["recall"]}}))

    def test_required_eda_and_policy_required_metrics_cannot_be_removed(self):
        for identifier in ("schema", "numeric-contract", "patient-overlap"):
            with self.subTest(identifier=identifier):
                context = self.context([pending()])
                with self.assertRaises(ValueError):
                    self.apply(context, self.proposal(
                        context, selection_updates={"eda": {"exclude": [identifier]}}))
        self.policy["eda"]["required"] = []
        self.selection["eda"]["exclude"] = ["schema"]
        with self.assertRaises(ValueError):
            self.context()
        self.selection["eda"]["exclude"] = []
        self.policy["evaluation"]["required"] = ["brier_score"]
        context = self.context([pending()])
        with self.assertRaises(ValueError):
            self.apply(context, self.proposal(context, selection_updates={
                "evaluation": {"exclude": ["brier"]}}))

    def test_current_primary_removal_requires_a_new_registered_primary(self):
        self.selection["evaluation"].update(primary_metric="roc_auc", add=["roc_auc"])
        context = self.context([pending()])
        with self.assertRaises(ValueError):
            self.apply(context, self.proposal(context, selection_updates={
                "evaluation": {"exclude": ["AUROC"]}}))
        result = self.apply(context, self.proposal(context, selection_updates={
            "evaluation": {"primary_metric": "AP", "exclude": ["AUROC"]}}))
        self.assertEqual(result["selection"]["evaluation"]["primary_metric"], "average_precision")
        self.assertEqual(result["selection"]["evaluation"]["exclude"], ["roc_auc"])
        self.assertEqual(result["selection"]["evaluation"]["add"], ["average_precision"])
        for primary in (None, "unknown_metric", "https://example.test/metric", ["ap"]):
            with self.subTest(primary=primary):
                with self.assertRaises(ValueError):
                    self.apply(context, self.proposal(
                        context, selection_updates={"evaluation": {"primary_metric": primary}}))

    def test_threshold_and_parameters_follow_existing_planner_types_and_ranges(self):
        context = self.context([pending()])
        invalid_patches = (
            {"threshold": float("nan")}, {"threshold": float("inf")},
            {"threshold": -0.01}, {"threshold": 1.01}, {"threshold": True},
            {"threshold": "0.3"}, {"threshold": 0.3, "parameters": {"threshold": 0.3}},
            {"parameters": {"threshold": float("nan")}},
            {"parameters": {"n_bins": 0}}, {"parameters": {"n_bins": True}},
            {"parameters": {"n_bins": 1.5}}, {"parameters": {"n_bins": "10"}},
            {"parameters": {"risk_thresholds": []}},
            {"parameters": {"risk_thresholds": [0, 0.3]}},
            {"parameters": {"risk_thresholds": [0.3, 1]}},
            {"parameters": {"thresholds": [0.3, 0.3]}},
            {"parameters": {"thresholds": [0.7, 0.3]}},
            {"parameters": {"top_fraction": 0}},
            {"parameters": {"top_fraction": 1.01}},
            {"parameters": {"model_features": ["injected"]}},
            {"parameters": {"n-bins": 5, "n_bins": 6}},
            {"clinical_action": ""}, {"clinical_action": 123},
            {"comparison_model": []},
        )
        for patch in invalid_patches:
            with self.subTest(patch=patch):
                with self.assertRaises(ValueError):
                    self.apply(context, self.proposal(
                        context, selection_updates={"evaluation": patch}))
        for threshold in (0, 0.3, 1):
            with self.subTest(threshold=threshold):
                result = self.apply(context, self.proposal(context, selection_updates={
                    "evaluation": {"parameters": {"threshold": threshold}}}))
                self.assertEqual(result["selection"]["evaluation"]["threshold"], threshold)
                self.assertNotIn("threshold", result["selection"]["evaluation"]["parameters"])

    def test_clinical_context_and_comparison_parameters_are_metadata_only(self):
        before = {name for name in sys.modules if name.startswith("app.blocks.")}
        context = self.context([pending()])
        result = self.apply(context, self.proposal(context, selection_updates={
            "evaluation": {
                "add": ["dca", "nri"], "parameters": {
                    "thresholds": [0.2, 0.3], "risk_thresholds": [0.1, 0.4]},
                "clinical_action": "공개 연구에서 별도 임상 검토 필요",
                "comparison_model": "아직 실행하지 않은 등록 비교 모델",
            }}))
        self.assertEqual(result["selection"]["evaluation"]["add"],
                         ["decision_curve", "categorical_nri"])
        self.assertNotIn("results", result)
        self.assertEqual(before, {name for name in sys.modules if name.startswith("app.blocks.")})
        self.validate(result)

    def test_unknown_fields_stages_roles_urls_and_model_input_edits_are_rejected(self):
        context = self.context([pending()])
        patches = (
            {"inference": {"add": ["f1"]}}, {"schema_version": 1},
            {"eda": {"primary_metric": "roc_auc"}},
            {"eda": {"objectives": ["cohort_definition"]}},
            {"evaluation": {"objectives": []}},
            {"evaluation": {"primary": "AP"}},
            {"evaluation": {"threshold_value": 0.3}},
            {"evaluation": {"features": ["new_input"]}},
            {"evaluation": {"feature_columns": ["new_input"]}},
            {"evaluation": {"bindings": {"patient_key": "row_key"}}},
            {"evaluation": {"role": "target"}},
            {"evaluation": {"module": "app.blocks.metrics.arbitrary"}},
            {"evaluation": {"url": "https://example.test"}},
            {"evaluation": {"code": "raise RuntimeError"}},
            {"evaluation": {"add": ["https://example.test"]}},
            {"evaluation": {"add": ["__import__('os')"]}},
            {"evaluation": {"add": ["unknown_metric"]}},
        )
        for patch in patches:
            with self.subTest(patch=patch):
                with self.assertRaises(ValueError):
                    self.apply(context, self.proposal(context, selection_updates=patch))
        for extras in ({"provider": pr1_intent.PROVIDER}, {"bindings": {}},
                       {"schema_version": True}, {"eda_add": ["not_registered"]},
                       {"reason": "English only"}, {"reason": "환자명: 개인정보"},
                       {"objectives": {"eda": ["no_such_goal"], "evaluation": []}},
                       {"objectives": {"eda": [], "evaluation": [], "inference": []}}):
            with self.subTest(extras=extras):
                with self.assertRaises(ValueError):
                    self.apply(context, self.proposal(context, **extras))
        for key in self.proposal(context):
            proposal = self.proposal(context)
            del proposal[key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                self.apply(context, proposal)

    def test_context_base_and_proposal_staleness_and_tampering_are_rejected(self):
        context = self.context([pending()])
        proposal = self.proposal(context)
        self.selection["evaluation"]["add"] = ["f1"]
        with self.assertRaises(ValueError):
            self.apply(context, proposal)
        self.selection = planning.default_selection()
        with self.assertRaises(ValueError):
            self.apply(context, {**proposal, "context_hash": "e" * 64})
        for key, value in (
                ("request_hash", "f" * 64),
                ("feedback", [pending(body="다른 공개 연구 댓글")]),
                ("allowed_objectives", {"eda": [], "evaluation": []}),
                ("unexpected", True)):
            modified = copy.deepcopy(context)
            modified[key] = value
            modified["context_hash"] = canonical_hash(
                {name: item for name, item in modified.items() if name != "context_hash"})
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.apply(modified, self.proposal(modified))

    def test_proposal_and_context_are_not_mutated_and_parameter_patch_preserves_state(self):
        self.selection["evaluation"].update(
            threshold=0.2, parameters={"n_bins": 11, "top_fraction": 0.1})
        context = self.context([pending()])
        proposal = self.proposal(context, selection_updates={
            "evaluation": {"parameters": {"threshold": 0.3, "n_bins": 6}}})
        before = copy.deepcopy((self.selection, context, proposal))
        result = self.apply(context, proposal)
        self.assertEqual((self.selection, context, proposal), before)
        self.assertEqual(result["selection"]["evaluation"]["threshold"], 0.3)
        self.assertEqual(result["selection"]["evaluation"]["parameters"],
                         {"n_bins": 6, "top_fraction": 0.1})
        self.validate(result)
        cleared = self.apply(context, self.proposal(context, selection_updates={
            "evaluation": {"threshold": None}}))
        self.assertIsNone(cleared["selection"]["evaluation"]["threshold"])
        self.assertEqual(cleared["selection"]["evaluation"]["parameters"],
                         {"n_bins": 11, "top_fraction": 0.1})
        self.validate(cleared)

    def test_sensitive_choice_context_and_agent_output_are_rejected(self):
        self.selection["evaluation"]["clinical_action"] = "환자명: 예시"
        with self.assertRaises(ValueError):
            self.context()
        self.selection = planning.default_selection()
        context = self.context([pending()])
        with self.assertRaises(ValueError):
            self.apply(context, self.proposal(context, selection_updates={
                "evaluation": {"comparison_model": "github_pat_" + "A" * 60}}))
        with self.assertRaises(ValueError):
            self.apply(context, self.proposal(context, reason="연구 근거 person@example.test"))

    def test_record_contains_hashes_typed_choices_and_agent_reason_not_source_bodies(self):
        context = self.context([pending(body="공개 합성 댓글의 고유한 원문 표식입니다.")])
        result = self.apply(context, self.proposal(context, eda_add=["timing"]))
        record = result["record"]
        self.assertEqual(set(record), {
            "schema_version", "provider", "context_hash", "request_hash", "base_selection_hash",
            "selection_hash", "schema_hash", "feedback_hash", "feedback_ids", "eda_add",
            "objectives", "reason", "coordination_hash",
        })
        self.assertEqual(record["schema_version"], 1)
        self.assertEqual(record["coordination_hash"], canonical_hash({
            "record": {key: value for key, value in record.items() if key != "coordination_hash"},
            "catalogs_hash": canonical_hash(self.catalogs),
            "policy_hash": canonical_hash(self.policy),
        }))
        serialized = json.dumps(record, ensure_ascii=False)
        for text in (*context["request"].values(), context["feedback"][0]["body"],
                     context["feedback"][0]["actor"]):
            self.assertNotIn(text, serialized)
        for forbidden in ("body_sha256", "schema_request", "columns", "selection_updates",
                          "patient_rows", "created_at"):
            self.assertNotIn(forbidden, record)
        self.assertEqual(record["provider"], "github-copilot-cloud-agent")
        self.assertEqual(record["request_hash"], canonical_hash(context["request"]))
        self.assertEqual(record["base_selection_hash"], context["selection_hash"])
        self.assertEqual(record["selection_hash"], canonical_hash(result["selection"]))
        self.assertEqual(record["schema_hash"], canonical_hash(self.schema))
        self.assertEqual(record["feedback_hash"], context["feedback_hash"])
        self.assertEqual(record["feedback_ids"], [100])
        self.assertEqual(record["reason"], self.proposal(context)["reason"])
        self.assertEqual(self.apply(context, self.proposal(context, eda_add=["timing"])), result)
        validated = self.validate(result)
        self.assertEqual(validated, record)
        validated["eda_add"].append("schema")
        self.assertEqual(record["eda_add"], ["prediction_timing"])

    def test_record_freeze_validation_rejects_each_tampered_field(self):
        context = self.context([pending()])
        result = self.apply(context, self.proposal(context, eda_add=["timing"]))
        for key in result["record"]:
            record = copy.deepcopy(result["record"])
            if key == "schema_version":
                record[key] = True
            elif key == "provider":
                record[key] = "local-fallback"
            elif key == "feedback_ids":
                record[key] = [101]
            elif key == "eda_add":
                record[key] = ["schema"]
            elif key == "objectives":
                record[key]["eda"].append("outcome_observability")
            elif key == "reason":
                record[key] = "변경된 근거"
            else:
                record[key] = "f" * 64
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.validate(result, record=record)
        record = {**result["record"], "body": "원문"}
        with self.assertRaises(ValueError):
            self.validate(result, record=record)
        for key in result["record"]:
            record = copy.deepcopy(result["record"])
            del record[key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                self.validate(result, record=record)

    def test_freeze_cross_checks_goal_schema_selection_and_registered_configuration(self):
        context = self.context()
        result = self.apply(context, self.proposal(context, eda_add=["timing"]))
        request = copy.deepcopy(self.request)
        request["research_goal"] += " 변경된 연구 목적"
        with self.assertRaises(ValueError):
            self.validate(result, request=request)
        schema = copy.deepcopy(self.schema)
        schema["known_mapping"] = False
        refresh_schema_hash(schema)
        with self.assertRaises(ValueError):
            self.validate(result, schema_request=schema)
        for change in (
                lambda value: value["evaluation"].update(threshold=0.3),
                lambda value: value["evaluation"]["add"].append("f1"),
                lambda value: value["eda"]["exclude"].append("schema"),
                lambda value: value["eda"]["objectives"].append("cohort_definition")):
            selection = copy.deepcopy(result["selection"])
            change(selection)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.validate(result, selection=selection)
        catalogs = copy.deepcopy(self.catalogs)
        catalogs["eda"]["checks"][0]["description"] += " 추가 설명"
        with self.assertRaises(ValueError):
            self.validate(result, catalogs=catalogs)
        policy = copy.deepcopy(self.policy)
        policy["eda"]["defaults"] = ["schema", "patient_overlap"]
        with self.assertRaises(ValueError):
            self.validate(result, policy=policy)

    def test_freeze_structural_checks_hold_even_after_recomputed_coordination_hash(self):
        context = self.context()
        result = self.apply(context, self.proposal(context, eda_add=["timing"]))
        mutations = (
            lambda record: record.update(eda_add=["timing"]),
            lambda record: record.update(eda_add=["prediction_timing", "prediction_timing"]),
            lambda record: record.update(feedback_ids=[1, 1]),
            lambda record: record.update(feedback_ids=[],
                                          feedback_hash=canonical_hash([{"not": "empty"}])),
            lambda record: record["objectives"]["evaluation"].append("threshold_performance"),
            lambda record: record.update(reason="English only"),
        )
        for mutation in mutations:
            record = copy.deepcopy(result["record"])
            mutation(record)
            record["coordination_hash"] = pr1_intent._coordination_hash(
                record, self.catalogs, self.policy)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.validate(result, record=record)

    def test_default_configuration_api_and_legacy_threshold_normalization(self):
        self.selection["evaluation"]["parameters"]["threshold"] = 0.3
        context = pr1_intent.build_context(self.request, self.schema, self.selection, [])
        self.assertEqual(context["selection"]["evaluation"]["threshold"], 0.3)
        self.assertEqual(context["selection"]["evaluation"]["parameters"], {})
        result = pr1_intent.apply_proposal(self.selection, self.proposal(context), context)
        pr1_intent.validate_record(result["record"], result["selection"],
                                   self.request, self.schema)
        selection = copy.deepcopy(result["selection"])
        selection["evaluation"]["parameters"]["threshold"] = 0.3
        with self.assertRaises(ValueError):
            self.validate(result, selection=selection)


if __name__ == "__main__":
    unittest.main()
