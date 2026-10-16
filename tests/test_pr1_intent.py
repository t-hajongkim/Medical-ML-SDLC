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


def basic_eda():
    from test_planning import fixture_eda

    return {
        "schema_version": 1, "kind": "aggregate-eda", "report_generated": True,
        "assessment_status": "failed", "data_id": "1" * 64,
        "model_execution_performed": False, "training_performed": False,
        "preprocessing_performed": False,
        "results": [*fixture_eda()["results"],
                    {"block": "demographics", "status": "completed", "details": {
                        "distributions": {"test": {"group": {"admissions": 2}}},
                        "count_unit": "rows"}},
                    {"block": "cohort", "status": "unverifiable", "details": {
                        "splits": {"test": {"admissions": 2, "event_rate": 0.5}},
                        "reason": "Upstream population evidence is not supplied."}}],
    }


class IntentTests(unittest.TestCase):
    def setUp(self):
        self.request = public_request()
        self.schema = public_schema()
        self.selection = planning.default_selection()
        self.catalogs = {stage: planning.load_catalog(stage) for stage in ("eda", "evaluation")}
        self.policy = planning.load_policy()

    def context(self, feedback=None, selection=None, *, basic_eda=None):
        return pr1_intent.build_context(
            self.request, self.schema, self.selection if selection is None else selection,
            [] if feedback is None else feedback, catalogs=self.catalogs, policy=self.policy,
            basic_eda=basic_eda)

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

    def test_basic_eda_none_preserves_the_complete_legacy_context_shape(self):
        context = self.context()
        legacy = pr1_intent.build_context(
            self.request, self.schema, self.selection, [],
            catalogs=self.catalogs, policy=self.policy)
        self.assertEqual(context, legacy)
        self.assertEqual(set(context), {
            "schema_version", "stage", "request", "schema_request", "selection",
            "catalogs", "policy", "allowed_objectives", "feedback", "request_hash",
            "selection_hash", "schema_hash", "feedback_hash", "context_hash",
        })
        result = self.apply(context, self.proposal(context, eda_add=["numeric_contract"]))
        self.assertEqual(result["record"]["eda_add"], ["schema"])

    def test_basic_results_and_unused_registered_checks_are_hash_bound_public_copies(self):
        measured = basic_eda()
        context = self.context(basic_eda=measured)
        self.assertEqual(context, self.context(basic_eda=copy.deepcopy(measured)))
        self.assertEqual(context["basic_eda"], measured)
        self.assertEqual(context["executed_eda_ids"],
                         ["schema", "patient_overlap", "demographics", "cohort"])
        self.assertEqual(context["available_eda_ids"],
                         ["prediction_timing", "label_observability", "preprocessing_lineage"])
        self.assertEqual(context["context_hash"], canonical_hash(
            {key: value for key, value in context.items() if key != "context_hash"}))
        measured["results"][0]["details"]["rows"] += 1
        self.assertEqual(context["basic_eda"]["results"][0]["details"]["rows"], 8)
        changed = self.context(basic_eda=measured)
        self.assertNotEqual(changed["context_hash"], context["context_hash"])
        self.assertEqual(changed["schema_hash"], context["schema_hash"])
        with self.assertRaises(ValueError):
            self.apply(changed, self.proposal(context))
        context["basic_eda"]["results"][0]["details"]["rows"] = 99
        self.assertEqual(measured["results"][0]["details"]["rows"], 9)

    def test_unused_candidates_respect_selected_excluded_and_protected_checks(self):
        self.selection["eda"].update(
            add=["label-observability"], exclude=["prediction-timing"])
        context = self.context(basic_eda=basic_eda())
        self.assertEqual(context["available_eda_ids"], ["preprocessing_lineage"])
        for identifier in ("timing", "label_observability"):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                self.apply(context, self.proposal(context, eda_add=[identifier]))
        self.policy["eda"].update(required=[], defaults=[])
        measured = basic_eda()
        measured["results"] = measured["results"][2:]
        context = self.context(basic_eda=measured)
        self.assertNotIn("schema", context["available_eda_ids"])
        self.assertNotIn("patient_overlap", context["available_eda_ids"])
        self.assertEqual(context["available_eda_ids"], ["preprocessing_lineage"])

    def test_ai_may_add_only_unused_checks_but_returned_ids_retain_basic_selections(self):
        context = self.context(basic_eda=basic_eda())
        before = copy.deepcopy(context)
        for identifier in ("schema", "numeric_contract", "patient-overlap",
                           "demographics", "cohort"):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                self.apply(context, self.proposal(context, eda_add=[identifier]))
        result = self.apply(context, self.proposal(context, eda_add=["timing", "lineage"]))
        self.assertEqual(result["record"]["eda_add"],
                         ["prediction_timing", "preprocessing_lineage"])
        self.assertEqual(result["eda_ids"], [
            "schema", "patient_overlap", "demographics", "cohort",
            "prediction_timing", "preprocessing_lineage"])
        self.assertEqual(result["selection"]["eda"]["add"], [])
        self.assertEqual(context, before)

    def test_no_extra_checks_is_valid_including_an_empty_candidate_list(self):
        measured = basic_eda()
        for identifier in ("prediction_timing", "label_observability", "preprocessing_lineage"):
            measured["results"].append(
                {"block": identifier, "status": "unverifiable", "details": {
                    "reason": "Optional evidence is not supplied."}})
        for aggregate in (basic_eda(), measured):
            with self.subTest(blocks=len(aggregate["results"])):
                context = self.context(basic_eda=aggregate)
                result = self.apply(context, self.proposal(context))
                self.assertEqual(result["record"]["eda_add"], [])
                self.assertEqual(result["eda_ids"],
                                 ["schema", "patient_overlap", "demographics", "cohort"])
        self.assertEqual(context["available_eda_ids"], [])

    def test_failed_and_unverifiable_basic_diagnoses_are_not_execution_failures(self):
        measured = basic_eda()
        context = self.context(basic_eda=measured)
        self.assertEqual([item["status"] for item in context["basic_eda"]["results"]],
                         ["unverifiable", "failed", "completed", "unverifiable"])
        result = self.apply(context, self.proposal(context))
        self.assertEqual(result["eda_ids"][:2], ["schema", "patient_overlap"])
        self.assertEqual(context["basic_eda"], measured)

    def test_basic_eda_accepts_public_executor_provenance_and_reused_results(self):
        measured = basic_eda()
        measured.update(
            status_meanings={
                "passed": "The measured conditions are satisfied.",
                "failed": "A measured violation was found.",
                "unverifiable": "Required evidence is missing.",
                "completed": "Descriptive aggregation is complete.",
            },
            interpretation="Report generation is not research suitability certification.",
            prepared_revision=None, raw_source_sha256=None,
            input_profile="synthetic_public", input_mapping_hash="3" * 64,
            dataset_context={"target_definition": "공개 합성 이진 결과"},
            coordination_hash="4" * 64, research_request_hash="5" * 64,
            coordination_evidence={"schema": {"numeric_contract": "passed"}},
            initial_execution_plan_hash="6" * 64, execution_plan_hash="7" * 64,
            execution_mode="registered_execution_plan", execution_result_hash="8" * 64,
            executed_ids=["demographics", "cohort"], reused_ids=["schema", "patient_overlap"],
        )
        context = self.context(basic_eda=measured)
        self.assertEqual(context["basic_eda"], measured)
        self.assertEqual(context["executed_eda_ids"],
                         ["schema", "patient_overlap", "demographics", "cohort"])
        self.assertEqual(context["available_eda_ids"],
                         ["prediction_timing", "label_observability", "preprocessing_lineage"])
        self.apply(context, self.proposal(context))

    def test_basic_context_still_allows_feedback_to_change_existing_human_choices(self):
        self.selection["eda"].update(add=["label_observability"], exclude=["prediction_timing"])
        context = self.context([pending()], basic_eda=basic_eda())
        self.assertEqual(context["available_eda_ids"], ["preprocessing_lineage"])
        result = self.apply(context, self.proposal(context, selection_updates={
            "eda": {"add": ["demographics", "timing"],
                    "exclude": ["cohort", "label_observability"]},
            "evaluation": {"add": ["f1"], "exclude": ["brier"], "primary_metric": "ap"},
        }))
        self.assertEqual(result["selection"]["eda"]["add"], ["demographics", "prediction_timing"])
        self.assertEqual(result["selection"]["eda"]["exclude"], ["cohort", "label_observability"])
        self.assertEqual(result["eda_ids"],
                         ["schema", "patient_overlap", "demographics", "prediction_timing"])
        self.assertEqual(result["selection"]["evaluation"]["add"], ["average_precision", "f1"])
        self.assertEqual(result["selection"]["evaluation"]["exclude"], ["brier_score"])
        for identifier in ("schema", "patient_overlap"):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                self.apply(context, self.proposal(
                    context, selection_updates={"eda": {"exclude": [identifier]}}))

    def test_metric_only_basic_context_changes_are_pure_and_do_not_add_eda(self):
        before = {name for name in sys.modules if name.startswith("app.blocks.")}
        context = self.context([pending()], basic_eda=basic_eda())
        result = self.apply(context, self.proposal(context, selection_updates={
            "evaluation": {"add": ["f1"], "primary_metric": "ap"}}))
        self.assertEqual(result["selection"]["eda"], self.selection["eda"])
        self.assertEqual(result["record"]["eda_add"], [])
        self.assertEqual(result["eda_ids"], ["schema", "patient_overlap", "demographics", "cohort"])
        self.assertNotIn("results", result)
        self.assertEqual(before, {name for name in sys.modules if name.startswith("app.blocks.")})

    def test_basic_eda_requires_a_matching_registered_aggregate_report(self):
        mutations = (
            lambda value: value.update(schema_version=True),
            lambda value: value.update(kind="raw-eda"),
            lambda value: value.update(report_generated=False),
            lambda value: value.update(report_generated=1),
            lambda value: value.update(data_id="2" * 64),
            lambda value: value.update(data_id=None),
            lambda value: value.update(data_id="not-a-digest"),
            lambda value: value.update(results={}),
            lambda value: value.update(results=[]),
            lambda value: value.update(results=[None]),
            lambda value: value["results"].append(copy.deepcopy(value["results"][0])),
            lambda value: value["results"][0].update(block="numeric_contract"),
            lambda value: value["results"][0].update(block="unknown_check"),
            lambda value: value["results"][0].update(block=["schema"]),
            lambda value: value["results"][0].update(status="error"),
            lambda value: value["results"][0].update(status=[]),
            lambda value: value["results"][0].update(details=[]),
            lambda value: value["results"][0].update(extra="unsupported"),
            lambda value: value.update(assessment_status=True),
            lambda value: value.update(model_execution_performed=True),
            lambda value: value.update(training_performed=0),
            lambda value: value.update(preprocessing_performed=True),
            lambda value: value.update(executed_ids=["schema", "schema"]),
            lambda value: value.update(reused_ids=["unknown_check"]),
            lambda value: value.update(reused_ids=[["schema"]]),
            lambda value: value.pop("kind"),
        )
        for mutation in mutations:
            measured = basic_eda()
            mutation(measured)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.context(basic_eda=measured)
        self.schema.pop("data_id")
        with self.assertRaises(ValueError):
            self.context(basic_eda=basic_eda())

    def test_basic_eda_rejects_private_rows_vectors_secrets_and_nonfinite_values(self):
        forbidden = {
            "rows": [{"patient_key": "private-row"}], "raw_rows": [[1, 2]],
            "metadata": [{"patient_key": "private-row"}], "patient_key": "private-row",
            "Patient-IDs": ["private-row"], "row_keys": ["private-row"],
            "feature_rows": [[1, 2]], "feature_values": [1, 2], "vectors": [[1, 2]],
            "predictions": [0.2, 0.3], "y_probability": [0.2, 0.3],
            "sample_ids": ["private-row"], "reference_probabilities": [0.2, 0.3],
            "credentials": {"secret": "private-secret"}, "api_key": "private-secret",
            "client_secret": "private-secret", "environment": {"key": "private-secret"},
            "reason": "환자명: private-row", "contact": "person@example.test",
            "credential_text": "ghp_" + "A" * 36, "value": float("nan"),
            "maximum": float("inf"), "minimum": -float("inf"), "unsupported": object(),
        }
        for key, value in forbidden.items():
            measured = basic_eda()
            measured["results"][0]["details"]["nested"] = {key: value}
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.context(basic_eda=measured)
        measured = basic_eda()
        measured["dataset_context"] = {"raw_metadata": [{"patient_key": "private-row"}]}
        with self.assertRaises(ValueError):
            self.context(basic_eda=measured)

    def test_basic_eda_context_revalidation_rejects_modified_snapshots_and_candidates(self):
        context = self.context(basic_eda=basic_eda())
        mutations = (
            lambda value: value["basic_eda"]["results"][0]["details"].update(rows=99),
            lambda value: value["basic_eda"]["results"][1].update(status="passed"),
            lambda value: value["executed_eda_ids"].append("prediction_timing"),
            lambda value: value["available_eda_ids"].append("schema"),
            lambda value: value["available_eda_ids"].clear(),
            lambda value: value.pop("executed_eda_ids"),
            lambda value: value.pop("basic_eda"),
            lambda value: value.update(basic_eda=None),
        )
        for mutation in mutations:
            changed = copy.deepcopy(context)
            mutation(changed)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.apply(changed, self.proposal(changed))
        for field in ("executed_eda_ids", "available_eda_ids"):
            changed = copy.deepcopy(context)
            changed[field] = []
            changed["context_hash"] = canonical_hash(
                {key: value for key, value in changed.items() if key != "context_hash"})
            with self.subTest(rehashed_field=field), self.assertRaises(ValueError):
                self.apply(changed, self.proposal(changed))
        changed = copy.deepcopy(context)
        changed["basic_eda"]["results"][0]["details"]["rows"] += 1
        changed["context_hash"] = canonical_hash(
            {key: value for key, value in changed.items() if key != "context_hash"})
        with self.assertRaises(ValueError):
            self.apply(changed, self.proposal(context))

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
                    self.apply(changed, self.proposal(context))
            self.schema[field] = original

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
        cleared = self.apply(context, self.proposal(context, selection_updates={
            "evaluation": {"threshold": None}}))
        self.assertIsNone(cleared["selection"]["evaluation"]["threshold"])
        self.assertEqual(cleared["selection"]["evaluation"]["parameters"],
                         {"n_bins": 11, "top_fraction": 0.1})

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




    def test_default_configuration_api_and_legacy_threshold_normalization(self):
        self.selection["evaluation"]["parameters"]["threshold"] = 0.3
        context = pr1_intent.build_context(self.request, self.schema, self.selection, [])
        self.assertEqual(context["selection"]["evaluation"]["threshold"], 0.3)
        self.assertEqual(context["selection"]["evaluation"]["parameters"], {})
        result = pr1_intent.apply_proposal(self.selection, self.proposal(context), context)
        selection = copy.deepcopy(result["selection"])
        selection["evaluation"]["parameters"]["threshold"] = 0.3
        with self.assertRaisesRegex(ValueError, "Duplicate threshold"):
            pr1_intent.build_context(self.request, self.schema, selection, [])


if __name__ == "__main__":
    unittest.main()
