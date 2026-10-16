import ast
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch
import uuid
import sys

from app.asset_contract import write_json
from app.dataset import ROOT
from app.executor_eda import report
from app import research
from app import planning_eval as planning
from test_eda_blocks import fixture


REPOSITORY = "example-owner/example-workshop"
DATA_IMAGE = "ghcr.io/example-owner/workshop-db@sha256:" + "a" * 64


def issue_body(values=None):
    values = values or {}
    contract = research.form_contract()
    defaults = {
        "research_goal": "Synthetic research goal",
        "research_question": "Synthetic research question",
        "cohort_requirements": "_No response_",
        "additional_requests": "_No response_",
        "public_data_confirmation": "- [X] " + contract["consent"],
    }
    defaults.update(values)
    return "\n\n".join(f"### {label}\n\n{defaults[name]}" for label, name in contract["fields"].items())


def issue(values=None):
    return {"number": 14, "state": "open", "title": "Synthetic research request",
            "body": issue_body(values), "author_association": "COLLABORATOR",
            "user": {"login": "synthetic-collaborator"}}


def context_and_source(overlap=False):
    context = fixture()
    if overlap:
        context["metadata"][2]["patient_key"] = context["metadata"][0]["patient_key"]
    source = {"features": context["feature_columns"], "splits": {split: {"rows": 2} for split in research.SPLITS},
              "expected_patient_overlap": {"train_validation": int(overlap), "train_test": 0, "validation_test": 0},
              "expected_test_race_missing": 0, "target_mapping": context["target_mapping"]}
    context["data_manifest"] = {"data_id": "f" * 64, "prepared_source": source}
    context["_input_schema_request"] = {
        "schema_version": 1, "profile_id": "synthetic_context", "data_id": "f" * 64,
        "context_hash": "c" * 64, "known_mapping": True, "columns": [],
    }
    return context, source


class ManifestObservationTests(unittest.TestCase):
    def test_legacy_aggregate_can_use_computed_observations_without_golden_values(self):
        context, source = context_and_source(overlap=True)
        source.pop("expected_patient_overlap")
        source.pop("expected_test_race_missing")
        aggregate = report(context)
        research.validate_aggregate(aggregate, source)
        by_name = {result["block"]: result for result in aggregate["results"]}
        self.assertEqual(by_name["patient_overlap"]["details"]["overlaps"]["train_validation"], 1)


def opened_event(candidate=None):
    return {"action": "opened", "issue": candidate or issue(),
            "repository": {"full_name": REPOSITORY, "default_branch": "main"}}


def pull(state="open"):
    return {"number": 19, "state": state, "draft": True,
            "head": {"ref": "research/review-issue-14", "repo": {"full_name": REPOSITORY}},
            "base": {"ref": "main"}}


class IntakeTests(unittest.TestCase):
    def test_actions_run_identifier_accepts_long_run_ids(self):
        for value in ("1", "38722808263", "18446744073709551615"):
            with self.subTest(value=value):
                self.assertEqual(research.run_identifier(value), value)

    def test_actions_run_identifier_rejects_invalid_values(self):
        for value in (None, 1, "0", "-1", "01", "1\n", "1; echo bad", "9" * 21):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "run identifier"):
                    research.run_identifier(value)

    def test_exact_current_five_fields_and_optional_empty_values(self):
        request = research.parse_issue(issue(), REPOSITORY)
        self.assertEqual(request["issue_number"], 14)
        self.assertEqual(request["cohort_requirements"], "")
        self.assertEqual(request["additional_requests"], "")
        self.assertTrue(request["public_data_confirmation"])
        self.assertIsNone(request["prediction_time"])
        self.assertFalse(request["selection_execution"])
        self.assertNotIn("demographic_focus", request)

    def test_registered_form_labels_match_without_editing_form(self):
        content = (ROOT / ".github" / "ISSUE_TEMPLATE" / "research-request.yml").read_text(encoding="utf-8")
        contract = research.form_contract()
        for label, name in contract["fields"].items():
            self.assertIn("label: " + label, content)
            self.assertIn("id: " + name, content)
        self.assertIn(contract["consent"], content)
        self.assertNotIn("demographic_focus", content)

    def test_non_research_issue_is_not_applicable(self):
        candidate = issue()
        candidate["body"] = "# Bug report\n\nSomething unrelated."
        self.assertIsNone(research.parse_issue(candidate, REPOSITORY))

    def test_pull_request_issue_object_is_not_applicable(self):
        candidate = issue()
        candidate["pull_request"] = {"url": "synthetic"}
        self.assertIsNone(research.parse_issue(candidate, REPOSITORY))

    def test_missing_or_reordered_section_is_rejected(self):
        candidate = issue()
        candidate["body"] = candidate["body"].replace("### 분석 대상과 제외 조건", "### Wrong label")
        with self.assertRaisesRegex(ValueError, "signature"):
            research.parse_issue(candidate, REPOSITORY)
        candidate["body"] = issue_body().replace("### 연구 목표", "### 연구 질문과 상세 설명", 1)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            research.parse_issue(candidate, REPOSITORY)

    def test_duplicate_section_is_rejected(self):
        candidate = issue()
        candidate["body"] += "\n\n### 연구 목표\n\nduplicate"
        with self.assertRaisesRegex(ValueError, "duplicate"):
            research.parse_issue(candidate, REPOSITORY)

    def test_required_text_is_nonempty(self):
        for name in ("research_goal", "research_question"):
            for text in ("", "_No response_"):
                with self.subTest(field=name, text=text):
                    with self.assertRaisesRegex(ValueError, "nonempty"):
                        research.parse_issue(issue({name: text}), REPOSITORY)

    def test_unchecked_missing_or_extra_consent_is_rejected(self):
        statement = research.form_contract()["consent"]
        for consent in ("- [ ] " + statement, "", "- [X] changed statement",
                        "- [X] " + statement + "\nextra"):
            with self.subTest(consent=consent):
                with self.assertRaisesRegex(ValueError, "confirmation"):
                    research.parse_issue(issue({"public_data_confirmation": consent}), REPOSITORY)

    def test_body_damage_is_rejected(self):
        candidate = issue()
        candidate["body"] = "unexpected preamble\n" + candidate["body"]
        with self.assertRaisesRegex(ValueError, "signature"):
            research.parse_issue(candidate, REPOSITORY)
        candidate = issue({"research_goal": "\x00"})
        with self.assertRaisesRegex(ValueError, "malformed"):
            research.parse_issue(candidate, REPOSITORY)

    def test_invalid_issue_numbers_and_repositories_are_rejected(self):
        for value in (0, -1, True, 1.0, "01", "1;echo bad", "../2", ""):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    research.issue_number(value)
        for value in ("owner", "owner/../repo", "owner/repo;echo", "owner/repo\nbad"):
            with self.assertRaises(ValueError):
                research.repository_name(value)

    def test_shell_and_unrecognized_headings_are_data_only(self):
        text = "### Not a form section\n> quoted text\n$(touch bad); `echo bad`\n__import__('os').system('bad')"
        with patch("subprocess.run") as execute:
            request = research.parse_issue(issue({"research_question": text}), REPOSITORY)
        execute.assert_not_called()
        self.assertEqual(request["research_question"], text)

    def test_trusted_opened_event_fetches_authoritative_issue(self):
        with patch("app.research.gh_json", return_value=issue()) as api:
            request, decision = research.intake(opened_event(), "issues", REPOSITORY, "synthetic-collaborator")
        api.assert_called_once_with("repos/" + REPOSITORY + "/issues/14")
        self.assertIsNone(decision)
        self.assertEqual(request["issue_number"], 14)

    def test_untrusted_automatic_event_is_deferred_before_api(self):
        candidate = issue()
        candidate["author_association"] = "NONE"
        with patch("app.research.gh_json") as api:
            request, decision = research.intake(opened_event(candidate), "issues", REPOSITORY, "synthetic-collaborator")
        self.assertIsNone(request)
        self.assertEqual(decision["status"], "deferred")
        self.assertIn("dispatch", decision["reason"])
        api.assert_not_called()

    def test_organization_membership_alone_is_not_write_permission(self):
        candidate = issue()
        candidate["author_association"] = "MEMBER"
        with patch("app.research.gh_json") as api:
            _, decision = research.intake(opened_event(candidate), "issues", REPOSITORY, "synthetic-collaborator")
        self.assertEqual(decision["status"], "deferred")
        api.assert_not_called()

    def test_manual_maintainer_can_process_untrusted_issue(self):
        candidate = issue()
        candidate["author_association"] = "NONE"
        with patch("app.research.gh_json", side_effect=[{"permission": "write"}, candidate]) as api:
            request, decision = research.intake(opened_event(), "workflow_dispatch", REPOSITORY, "maintainer", "14")
        self.assertIsNone(decision)
        self.assertEqual(request["issue_number"], 14)
        self.assertEqual(api.call_count, 2)

    def test_manual_insufficient_permission_is_deferred(self):
        with patch("app.research.gh_json", return_value={"permission": "read"}) as api:
            _, decision = research.intake(opened_event(), "workflow_dispatch", REPOSITORY, "reader", "14")
        self.assertEqual(decision["status"], "deferred")
        self.assertEqual(api.call_count, 1)

    def test_invalid_manual_number_is_rejected_before_api(self):
        with patch("app.research.gh_json") as api:
            with self.assertRaises(ValueError):
                research.intake(opened_event(), "workflow_dispatch", REPOSITORY, "maintainer", "$(bad)")
        api.assert_not_called()

    def test_event_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            research.intake(opened_event(), "issues", "other-owner/other-repo", "synthetic-collaborator")
        with self.assertRaises(ValueError):
            research.intake(opened_event(), "issues", REPOSITORY, "not-the-author")

    def test_closed_issue_is_deferred(self):
        candidate = issue()
        candidate["state"] = "closed"
        with patch("app.research.gh_json", return_value=candidate):
            _, decision = research.intake(opened_event(), "issues", REPOSITORY, "synthetic-collaborator")
        self.assertEqual(decision["status"], "deferred")

    def test_unsupported_event_does_not_run(self):
        event = opened_event()
        event["action"] = "edited"
        with patch("app.research.gh_json") as api:
            _, decision = research.intake(event, "issues", REPOSITORY, "synthetic-collaborator")
        self.assertEqual(decision["status"], "not_applicable")
        api.assert_not_called()


class AssetAndReportTests(unittest.TestCase):
    def test_only_pinned_data_reference_is_used(self):
        self.assertEqual(research.data_reference({
            "schema_version": 1, "data_image": DATA_IMAGE, "model_image": "do-not-read-this"
        }), DATA_IMAGE)

    def test_bad_data_references_fail(self):
        for reference in ("ghcr.io/example-owner/workshop-db:latest", DATA_IMAGE + ";bad",
                          DATA_IMAGE.upper(), None, "https://bad/image"):
            with self.assertRaises(ValueError):
                research.data_reference({"schema_version": 1, "data_image": reference})

    def test_image_copy_creates_but_never_starts_container(self):
        with patch("app.research.docker", return_value="synthetic") as docker, \
                patch.object(Path, "exists", return_value=False):
            research.image_assets(DATA_IMAGE, Path("synthetic-assets"))
        commands = [call.args for call in docker.call_args_list]
        self.assertEqual([entry[0] for entry in commands], ["pull", "create", "cp", "rm"])
        self.assertEqual(commands[0][1], DATA_IMAGE)
        self.assertIn("--network", commands[1])
        self.assertNotIn("run", [entry[0] for entry in commands])
        self.assertNotIn("start", [entry[0] for entry in commands])
        self.assertIn(":/opt/workshop/assets", commands[2][1])

    def test_image_copy_failure_still_removes_owned_container(self):
        with patch("app.research.docker", side_effect=["", "created", RuntimeError("copy failed"), ""]) as docker, \
                patch.object(Path, "exists", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "copy failed"):
                research.image_assets(DATA_IMAGE, Path("synthetic-assets"))
        self.assertEqual(docker.call_args_list[-1].args[0], "rm")

    def test_network_failure_is_not_continued(self):
        with patch("app.research.docker", side_effect=RuntimeError("network")) as docker, \
                patch.object(Path, "exists", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "network"):
                research.image_assets(DATA_IMAGE, Path("synthetic-assets"))
        self.assertEqual(docker.call_count, 1)

    def test_failed_patient_assessment_is_publishable_not_input_damage(self):
        context, source = context_and_source(overlap=True)
        eda = report(context)
        self.assertEqual(eda["assessment_status"], "failed")
        self.assertEqual(research.validate_aggregate(eda, source)["patient_overlap"]["status"], "failed")

    def test_damaged_feature_input_blocks_publication(self):
        context, source = context_and_source()
        context["feature_rows"][0]["feature_values"].pop()
        with self.assertRaisesRegex(ValueError, "damaged"):
            research.validate_aggregate(report(context), source)

    def test_missing_block_and_row_payload_are_rejected(self):
        context, source = context_and_source()
        eda = report(context)
        bad = copy.deepcopy(eda)
        bad["results"].pop()
        with self.assertRaisesRegex(ValueError, "selected"):
            research.validate_aggregate(bad, source, list(research.CHECKS))
        bad = copy.deepcopy(eda)
        bad["patient_key"] = "synthetic-patient"
        with self.assertRaisesRegex(ValueError, "aggregate"):
            research.validate_aggregate(bad, source)

    def test_unknown_original_race_evidence_is_not_published_as_completed(self):
        context, source = context_and_source()
        context["metadata"][0].pop("race_original")
        with self.assertRaisesRegex(ValueError, "damaged"):
            research.validate_aggregate(report(context), source)

    def test_metric_plan_is_not_calculation_or_approval(self):
        context, _ = context_and_source()
        plan = planning.build_plans(report(context), context["data_manifest"],
                                    planning.default_selection(), None)["evaluation"]
        self.assertIsNone(plan["primary_metric"])
        self.assertIsNone(plan["threshold"])
        self.assertFalse(plan["clinical_use_approved"])
        self.assertFalse(plan["planning_complete"])
        self.assertEqual(plan["ai_status"], "pending")
        self.assertNotIn("categorical_nri", plan["selected_ids"])
        self.assertNotIn("decision_curve", plan["selected_ids"])
        self.assertTrue(all(entry["reason"] and entry["requires"] for entry in plan["items"]))

    def test_generated_report_describes_free_form_feedback_and_merge_approval(self):
        context, _ = context_and_source()
        plans = planning.build_plans(report(context), context["data_manifest"],
                                     planning.default_selection(), None)
        text = "\n".join(research.plan_report_lines(plans))
        self.assertIn("자연어 피드백", text)
        self.assertIn("Python 검증", text)
        self.assertNotIn("/plan", text)
        self.assertIn("Merge된 계획과 commit이 승인 기준", text)
        self.assertNotIn("research/approved/", text)
        self.assertNotIn("일반 댓글·인용·코드블록은 선택을 바꾸지 않습니다", text)

    def test_gh_failure_is_explicit_and_sensitive_output_not_echoed(self):
        result = subprocess.CompletedProcess(["gh"], 1, stdout="sensitive body", stderr="secret-token")
        with patch("subprocess.run", return_value=result):
            with self.assertRaises(RuntimeError) as failure:
                research.gh_json("synthetic")
        self.assertNotIn("secret-token", str(failure.exception))
        self.assertNotIn("sensitive body", str(failure.exception))

    def test_gh_invalid_json_is_explicit(self):
        result = subprocess.CompletedProcess(["gh"], 0, stdout="not JSON", stderr="")
        with patch("subprocess.run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "invalid JSON"):
                research.gh_json("synthetic")

    def test_pr_creation_failure_names_copy_setting_without_sensitive_output(self):
        result = subprocess.CompletedProcess(["gh"], 1, stdout="private-body", stderr="secret-token")
        with patch("subprocess.run", return_value=result):
            with self.assertRaises(RuntimeError) as failure:
                research.command(["gh", "pr", "create", "--title", "private-title"])
        message = str(failure.exception)
        self.assertIn("draft PR 생성 실패", message)
        self.assertIn("Allow GitHub Actions to create and approve pull requests", message)
        self.assertIn("상속되지", message)
        for private in ("private-body", "secret-token", "private-title"):
            self.assertNotIn(private, message)

    def test_recommendation_dispatch_failure_is_distinct_from_pr_creation(self):
        result = subprocess.CompletedProcess(["gh"], 1, stdout="", stderr="secret-token")
        with patch("subprocess.run", return_value=result):
            with self.assertRaises(RuntimeError) as failure:
                research.command(["gh", "workflow", "run", "research-plan.lock.yml"])
        self.assertIn("Copilot 추천 워크플로 요청 실패", str(failure.exception))
        self.assertIn("actions: write", str(failure.exception))
        self.assertNotIn("secret-token", str(failure.exception))


class PayloadAndPublicationTests(unittest.TestCase):
    def setUp(self):
        base = Path(os.environ.get("RESEARCH_TEST_DIRECTORY", str(ROOT.parent / ".research-test-fixtures")))
        self.folder = base / uuid.uuid4().hex
        self.root = self.folder / "checkout"
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.folder)
        form = self.root / ".github" / "ISSUE_TEMPLATE" / "research-request.yml"
        form.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / ".github" / "ISSUE_TEMPLATE" / "research-request.yml", form)
        self.request = research.parse_issue(issue(), REPOSITORY)
        context, self.source = context_and_source(overlap=True)
        self.context = context
        source_path = self.folder / "source.json"
        write_json(source_path, self.source)
        source_patch = patch("app.research.CONFIG", source_path)
        source_patch.start()
        self.addCleanup(source_patch.stop)
        self.eda = report(context)
        self.provenance = {
            "data_image": DATA_IMAGE, "code_commit": "b" * 40,
            "workflow_run_url": f"https://github.com/{REPOSITORY}/actions/runs/12",
            "data_access": "Synthetic validated prepared-file fixture; no DB queries.",
        }
        self.paths = [f"research/reports/issue-14/{name}" for name in research.OUTPUT_NAMES]
        self.calls = []
        self.changed = True
        self.remote = False

    def fake_command(self, arguments, cwd=None, allowed=(0,), input_text=None):
        self.calls.append(arguments)
        output = ""
        code = 0
        if arguments[:3] == ["git", "ls-remote", "--heads"] and self.remote:
            output = "a" * 40 + "\trefs/heads/research/review-issue-14"
        if arguments == ["git", "rev-parse", "HEAD"]:
            output = "b" * 40
        if arguments == ["git", "diff", "--cached", "--name-only"]:
            output = "\n".join(self.paths) if self.changed else ""
        if arguments[:4] == ["git", "diff", "--cached", "--quiet"]:
            code = int(self.changed)
        if arguments[:3] == ["gh", "pr", "create"]:
            output = f"https://github.com/{REPOSITORY}/pull/19"
        return subprocess.CompletedProcess(arguments, code, output, "")



    def test_payload_paths_and_contents_are_aggregate_only(self):
        paths = research.write_payload(self.root, self.request, self.eda, self.provenance)
        self.assertEqual(paths, self.paths)
        all_content = "".join((self.root / path).read_text(encoding="utf-8") for path in paths)
        self.assertNotIn("synthetic-patient-", all_content)
        self.assertNotIn("synthetic-encounter-", all_content)
        markdown = (self.root / self.paths[2]).read_text(encoding="utf-8")
        self.assertIn("`failed`", markdown)
        self.assertIn(self.request["issue_url"], markdown)
        self.assertIn("평가 계획 추천 초안", markdown)
        self.assertNotIn("PR1", markdown)
        self.assertTrue(all(not path.endswith((".csv", ".pkl")) for path in paths))

    def test_payload_rerun_is_semantically_idempotent(self):
        research.write_payload(self.root, self.request, self.eda, self.provenance)
        before = {path: (self.root / path).read_bytes() for path in self.paths}
        newer = {**self.provenance, "workflow_run_url": f"https://github.com/{REPOSITORY}/actions/runs/13"}
        research.write_payload(self.root, self.request, self.eda, newer)
        self.assertEqual(before, {path: (self.root / path).read_bytes() for path in self.paths})
        self.assertTrue(newer["workflow_run_url"].endswith("/13"))

    def test_changed_request_regenerates_new_execution_evidence(self):
        research.write_payload(self.root, self.request, self.eda, self.provenance)
        self.request["additional_requests"] = "A changed review request."
        self.provenance["workflow_run_url"] = f"https://github.com/{REPOSITORY}/actions/runs/13"
        research.write_payload(self.root, self.request, self.eda, self.provenance)
        payload = json.loads((self.root / self.paths[1]).read_text(encoding="utf-8"))
        self.assertTrue(payload["provenance"]["workflow_run_url"].endswith("/13"))

    def test_paths_reject_invalid_issue_number(self):
        self.request["issue_number"] = "../bad"
        with self.assertRaises(ValueError):
            research.write_payload(self.root, self.request, self.eda, self.provenance)

    def test_symlink_output_is_rejected(self):
        original = Path.is_symlink
        def symlink(path):
            return path.name == "report.md" or original(path)
        with patch.object(Path, "is_symlink", symlink):
            with self.assertRaisesRegex(ValueError, "symlink"):
                research.write_payload(self.root, self.request, self.eda, self.provenance)









    def test_intake_hands_off_issue_without_pulling_assets_or_creating_pr(self):
        from test_input_contracts import legacy_profile_fixture
        (self.root / "config").mkdir()
        write_json(self.root / "config" / "assets.json",
                   {"schema_version": 1, "data_image": DATA_IMAGE, "model_image": "never-pull-model"})
        work = self.folder / "runtime"
        def image_assets(ref, path):
            path.mkdir(parents=True)
            write_json(path / "data_manifest.json", {"data_id": self.context["data_manifest"]["data_id"]})
        with patch("app.research.ROOT", self.root), \
                patch("app.research.intake", return_value=(self.request, None)), \
                patch("app.research.existing_pull", return_value=(None, None)), \
                patch("app.research.command", side_effect=self.fake_command), \
                patch("app.research.image_assets", side_effect=image_assets) as assets, \
                patch("app.input_contracts.profile_for_data", return_value=legacy_profile_fixture()), \
                patch("app.research.load_context", return_value=self.context), \
                patch("app.research.write_payload", return_value={"status": "created"}) as publish, \
                patch("app.research.report", side_effect=AssertionError("EDA before AI coordination")), \
                patch.dict(os.environ, {"GITHUB_RUN_ID": "38722808263"}):
            result = research.run_workflow(opened_event(), "issues", REPOSITORY, "synthetic-collaborator", "main", work)
        self.assertEqual(result["status"], "prepared")
        self.assertEqual(result["ai_dispatch"]["target_kind"], "issue")
        self.assertEqual(result["ai_dispatch"]["target_number"], 14)
        assets.assert_not_called()
        publish.assert_not_called()
        self.assertFalse(work.exists())

    def test_intake_does_not_load_unverified_data_before_agent_preparation(self):
        from test_input_contracts import legacy_profile_fixture
        (self.root / "config").mkdir()
        write_json(self.root / "config" / "assets.json", {"schema_version": 1, "data_image": DATA_IMAGE})
        work = self.folder / "runtime"
        def image_assets(ref, path):
            path.mkdir(parents=True)
            write_json(path / "data_manifest.json", {"data_id": self.context["data_manifest"]["data_id"]})
        with patch("app.research.ROOT", self.root), \
                patch("app.research.intake", return_value=(self.request, None)), \
                patch("app.research.existing_pull", return_value=(None, None)), \
                patch("app.research.command", side_effect=self.fake_command), \
                patch("app.research.image_assets", side_effect=image_assets), \
                patch("app.input_contracts.profile_for_data", return_value=legacy_profile_fixture()), \
                patch("app.research.load_context", side_effect=ValueError("damaged input package")), \
                patch("app.research.write_payload") as publish, \
                patch.dict(os.environ, {"GITHUB_RUN_ID": "12"}):
            outcome = research.run_workflow(
                opened_event(), "issues", REPOSITORY, "synthetic-collaborator", "main", work)
        self.assertFalse(outcome["pull_request_created"])
        publish.assert_not_called()
        self.assertFalse(work.exists())



    def write_composed(self, eda=None):
        return research.write_payload(self.root, self.request, eda or self.eda, self.provenance,
                                      context=self.context)

    def report_json(self, name):
        return json.loads((self.root / "research" / "reports" / "issue-14" / name).read_text(encoding="utf-8"))

    def test_missing_ai_remains_pending_and_no_placeholder_is_created(self):
        paths = self.write_composed()
        self.assertEqual(len(paths), 7)
        self.assertFalse((self.root / "research" / "reports" / "issue-14" / "ai-recommendations.json").exists())
        self.assertEqual(self.report_json("evaluation-plan.json")["ai_status"], "pending")
        self.assertFalse(self.report_json("eda-plan.json")["planning_complete"])
        self.assertIn("context_hash", self.report_json("recommendation-request.json"))
        self.assertIn("AI 대기 초안", (self.root / self.paths[2]).read_text(encoding="utf-8"))

    def test_selection_and_cloud_ai_files_are_preserved_and_composed(self):
        self.write_composed()
        directory = self.root / "research" / "reports" / "issue-14"
        selection = self.report_json("selection.json")
        selection["eda"].update(add=["prediction_timing"], exclude=["cohort"])
        selection["evaluation"].update(add=["recall", "categorical_nri"], primary_metric="recall", threshold=0.4)
        write_json(directory / "selection.json", selection)
        self.write_composed()
        request = self.report_json("recommendation-request.json")
        ai = {"schema_version": 1, "provider": "github-copilot-cloud-agent",
              "context_hash": request["context_hash"], "status": "provided",
              "eda": [{"id": "prediction_timing", "reason": "Assess unavailable timing evidence.",
                       "evidence": ["required.schema.status"]}],
              "evaluation": [{"id": "average_precision", "reason": "Inspect discrimination objective.",
                              "evidence": ["objectives.evaluation"]}]}
        write_json(directory / "ai-recommendations.json", ai)
        selection_bytes = (directory / "selection.json").read_bytes()
        ai_bytes = (directory / "ai-recommendations.json").read_bytes()
        paths = self.write_composed()
        self.assertEqual(selection_bytes, (directory / "selection.json").read_bytes())
        self.assertEqual(ai_bytes, (directory / "ai-recommendations.json").read_bytes())
        self.assertIn("research/reports/issue-14/ai-recommendations.json", paths)
        data_plan = self.report_json("eda-plan.json")
        self.assertNotIn("cohort", data_plan["selected_ids"])
        timing = next(item for item in data_plan["items"] if item["id"] == "prediction_timing")
        self.assertEqual(timing["sources"], ["ai", "user"])
        metrics = self.report_json("evaluation-plan.json")
        self.assertEqual(metrics["ai_status"], "provided")
        self.assertTrue(metrics["planning_complete"])
        self.assertEqual(metrics["primary_metric"], "recall")
        self.assertEqual(metrics["threshold"], 0.4)
        nri = next(item for item in metrics["items"] if item["id"] == "categorical_nri")
        self.assertEqual(nri["state"], "blocked")
        self.assertTrue(nri["blocked_reasons"])
        self.assertNotIn("prediction_timing", metrics["selected_ids"])
        self.assertNotIn("recall", data_plan["selected_ids"])

    def test_legacy_manual_primary_and_threshold_migrate_only_once(self):
        directory = research.report_directory(self.root, 14)
        directory.mkdir(parents=True)
        write_json(directory / "evaluation-plan.json",
                   {"primary_metric": "recall", "threshold": 0.37})
        self.write_composed()
        self.assertEqual(self.report_json("selection.json")["evaluation"]["primary_metric"], "recall")
        self.assertEqual(self.report_json("selection.json")["evaluation"]["threshold"], 0.37)
        self.assertIn("recall", self.report_json("evaluation-plan.json")["selected_ids"])
        selection_bytes = (directory / "selection.json").read_bytes()
        self.write_composed()
        self.assertEqual(selection_bytes, (directory / "selection.json").read_bytes())

    def test_empty_ai_success_placeholder_is_rejected(self):
        self.write_composed()
        directory = research.report_directory(self.root, 14)
        request = self.report_json("recommendation-request.json")
        write_json(directory / "ai-recommendations.json", {
            "schema_version": 1, "provider": "github-copilot-cloud-agent",
            "context_hash": request["context_hash"], "status": "provided",
            "eda": [], "evaluation": []})
        with self.assertRaisesRegex(ValueError, "empty"):
            self.write_composed()

    def test_live_execution_calls_only_selected_missing_diagnostics(self):
        directory = research.report_directory(self.root, 14)
        directory.mkdir(parents=True)
        selection = planning.default_selection()
        selection["eda"].update(add=["prediction_timing"], exclude=["cohort", "demographics"])
        write_json(directory / "selection.json", selection)
        required = report(self.context, identifiers=["schema", "patient_overlap"])
        with patch("app.research.report", wraps=report) as execute:
            self.write_composed(required)
        self.assertEqual(execute.call_args.kwargs["identifiers"], ["prediction_timing"])
        self.assertEqual(execute.call_count, 1)
        eda = self.report_json("eda.json")
        self.assertEqual([item["block"] for item in eda["results"]], ["schema", "patient_overlap", "prediction_timing"])
        research.validate_aggregate(eda, self.source, ["schema", "patient_overlap", "prediction_timing"])
        self.assertEqual(eda["execution_mode"], "selected_execution")
        self.assertEqual(eda["assessment_status"], "failed")
        markdown = (self.root / self.paths[2]).read_text(encoding="utf-8")
        self.assertIn("미선택 데이터 점검:", markdown)
        self.assertNotIn("### 인구집단별 기술 집계", markdown)

    def test_optional_cohort_demographic_label_are_not_required(self):
        eda = report(self.context, identifiers=["schema", "patient_overlap"])
        self.assertEqual(set(research.validate_aggregate(eda, self.source)), {"schema", "patient_overlap"})
        eda["results"].append(copy.deepcopy(eda["results"][0]))
        with self.assertRaisesRegex(ValueError, "selected"):
            research.validate_aggregate(eda, self.source)

    def test_projection_is_explicit_and_does_not_run_eda(self):
        with patch("app.research.report") as execute:
            research.write_payload(self.root, self.request, self.eda, self.provenance)
        execute.assert_not_called()
        self.assertEqual(self.report_json("eda.json")["execution_mode"], "projection_reuse")
        self.assertTrue(self.report_json("eda.json")["provenance"]["planning_metadata_mock"])

    def test_pr_automatic_area_is_fresh_and_human_content_is_preserved(self):
        original = "Human preface\n" + research.AUTO_START + "\nold facts\n" + research.AUTO_END + "\nHuman closing"
        updated = research.merge_review_body(original, "new facts")
        self.assertIn("Human preface\n", updated)
        self.assertTrue(updated.endswith("\nHuman closing"))
        self.assertIn("new facts", updated)
        self.assertNotIn("old facts", updated)
        self.assertEqual(updated.count(research.AUTO_START), 1)

    def test_legacy_generated_pr_body_migrates_but_other_human_body_is_kept(self):
        self.assertEqual(research.merge_auto_body("old report", "new report", "old report"),
                         research.AUTO_START + "\nnew report\n" + research.AUTO_END)
        self.assertTrue(research.merge_review_body("human-only", "fresh").endswith("\n\nhuman-only"))

    def test_broken_or_duplicate_markers_are_rejected(self):
        for body in (research.AUTO_START, research.AUTO_END,
                     research.AUTO_END + research.AUTO_START,
                     (research.AUTO_START + research.AUTO_END) * 2):
            with self.assertRaisesRegex(ValueError, "markers"):
                research.merge_review_body(body, "new report")


    def test_pending_and_stale_dispatch_uses_default_branch_and_pr_number(self):
        for status in ("pending", "stale"):
            with patch("app.research.command") as execute:
                result = research.dispatch_ai({"status": "updated", "ai_status": status,
                                              "pull_url": f"https://github.com/{REPOSITORY}/pull/19"},
                                             REPOSITORY, "main")
            self.assertEqual(result["status"], "requested")
            self.assertEqual(result["workflow"], "research-plan.lock.yml")
            self.assertEqual(result["pull_request"], 19)
            self.assertEqual(result["default_branch"], "main")
            self.assertEqual(result["trigger"], "workflow_run")
            execute.assert_not_called()

    def test_ai_handoff_rejects_other_repositories(self):
        with patch("app.research.command") as execute:
            with self.assertRaisesRegex(ValueError, "current repository"):
                research.dispatch_ai({"status": "created", "ai_status": "pending",
                                      "pull_url": "https://github.com/another/repository/pull/19"},
                                     REPOSITORY, "main")
        execute.assert_not_called()

    def test_provided_ai_does_not_dispatch_again(self):
        with patch("app.research.command") as execute:
            result = research.dispatch_ai({"status": "unchanged", "ai_status": "provided"}, REPOSITORY, "main")
        execute.assert_not_called()
        self.assertEqual(result["status"], "not_needed")

    def test_cloud_cli_routes_lazily_without_remote_execution(self):
        for mode, name in (("prepare-ai", "prepare_ai"), ("apply-ai", "apply_ai"),
                           ("apply-input-mapping", "apply_input_mapping"),
                           ("plan-eda", "plan_eda"), ("execute-eda", "execute_eda")):
            arguments = ["research", mode, "--pull-request", "19", "--work", str(self.folder)]
            if mode == "execute-eda":
                arguments += ["--plan-hash", "a" * 64]
            with patch.object(research, name, return_value={"status": "mock"}) as handler, \
                    patch.object(sys, "argv", arguments), \
                    patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), patch("builtins.print"):
                research.main()
            expected = (19, self.folder, "a" * 64) if mode == "execute-eda" else (19, self.folder)
            if mode == "prepare-ai":
                handler.assert_called_once_with(*expected, target_kind="pull_request")
            else:
                handler.assert_called_once_with(*expected)

    def test_cloud_cli_refuses_local_remote_mode(self):
        with patch.object(sys, "argv", ["research", "prepare-ai", "--pull-request", "19",
                                       "--work", str(self.folder)]), \
                patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}), patch("sys.stderr", new=__import__("io").StringIO()):
            with self.assertRaises(SystemExit):
                research.main()


class WorkflowSourceTests(unittest.TestCase):
    def test_workflow_has_minimum_permissions_and_trusted_dynamic_checkout(self):
        content = (ROOT / ".github" / "workflows" / "prepare-eda-pr.yml").read_text(encoding="utf-8")
        for expected in ("types: [opened]", "workflow_dispatch:", "contents: read",
                         "pull-requests: read", "issues: read", "cancel-in-progress: false",
                         "ref: ${{ github.event.repository.default_branch }}", "persist-credentials: false"):
            self.assertIn(expected, content)
        for forbidden in ("github.event.issue.body", "github.event.issue.title", "continue-on-error",
                          "packages: write", "secrets.PAT", "types: [edited"):
            self.assertNotIn(forbidden, content)
        self.assertIn("python -B -m app.research workflow", content)

    def test_new_runner_has_no_fit_predict_model_import_or_dynamic_eval(self):
        tree = ast.parse((ROOT / "app" / "research.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, ("eval", "exec", "compile", "prepare_model"))
                if isinstance(node.func, ast.Attribute):
                    self.assertNotIn(node.func.attr, ("fit", "predict", "predict_proba", "load_model"))
            if isinstance(node, ast.ImportFrom):
                self.assertNotIn(node.module, ("app.infer", "app.assets", "app.bootstrap", "pickle", "joblib"))


if __name__ == "__main__":
    unittest.main()
