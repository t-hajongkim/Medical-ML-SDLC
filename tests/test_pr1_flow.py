"""Offline PR1 sequence tests with real synthetic prepared inputs and EDA blocks."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from app import executor_eda, research
from app.asset_contract import read_json, write_json
import test_input_contracts as fixtures
from test_research_workflow import issue


REPO = "fixture-owner/workshop"
CODE = "a" * 40
PUBLISHED = "d" * 40
PR = 43


def cleanup_temporary(temporary):
    for attempt in range(10):
        try:
            temporary.cleanup()
            return
        except PermissionError as error:
            if getattr(error, "winerror", None) != 32 or attempt == 9:
                raise
            time.sleep(0.1)


class PR1FlowTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.InputContractTests("test_default_diabetes_profile_preserves_nine_registered_names")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        temporary = tempfile.TemporaryDirectory(prefix="pr1-sequence-", dir=research.ROOT.parent)
        self.addCleanup(cleanup_temporary, temporary)
        self.base = Path(temporary.name)
        self.root = self.base / "checkout"
        self.root.mkdir()
        self.work = self.base / "private"
        self.original = research.ROOT
        self.original_issue = issue()
        self.request = research.parse_issue(self.original_issue, REPO)
        (self.root / ".github" / "ISSUE_TEMPLATE").mkdir(parents=True)
        shutil.copyfile(self.original / ".github" / "ISSUE_TEMPLATE" / "research-request.yml",
                        self.root / ".github" / "ISSUE_TEMPLATE" / "research-request.yml")
        (self.root / "config").mkdir()
        write_json(self.root / "config" / "assets.json", {
            "schema_version": 1,
            "data_image": "ghcr.io/fixture-owner/data@sha256:" + "b" * 64,
            "model_image": "ghcr.io/fixture-owner/model@sha256:" + "c" * 64})
        self.files = {}
        self.comments = {}
        self.pull_head = PUBLISHED
        self.pull_body = ""
        self.calls = {name: Mock(wraps=handler) for name, handler in executor_eda.CHECKS.items()}
        self.assets = None
        self.addCleanup(patch.stopall)
        patch.object(research, "ROOT", self.root).start()
        patch.object(research, "_runtime", return_value=(research, REPO)).start()
        self.remote = patch.object(research, "gh_json", side_effect=self.api).start()
        patch.object(research, "command", side_effect=self.command).start()
        patch.object(research, "existing_pull", return_value=(None, None)).start()
        patch.object(research, "intake", return_value=(self.request, None)).start()
        self.extract = patch.object(research, "image_assets",
                                    side_effect=lambda reference, target: shutil.copytree(self.assets, target)).start()
        patch.object(research, "_read_branch_file", side_effect=self.branch_file).start()
        self.publish = patch.object(research, "_git_tree_commit", return_value=PUBLISHED).start()
        self.cache_auth = patch.object(research, "_authenticate_prior_eda", return_value=True).start()
        patch.dict(executor_eda.CHECKS, self.calls).start()
        patch.dict(os.environ, {"GITHUB_ACTOR": "synthetic-collaborator", "GITHUB_RUN_ID": "123"}).start()

    def command(self, arguments, **kwargs):
        if arguments[:3] == ["git", "status", "--porcelain"]:
            return SimpleNamespace(stdout="")
        if arguments[:3] == ["git", "rev-parse", "HEAD"]:
            return SimpleNamespace(stdout=CODE)
        if arguments[:3] == ["git", "rev-parse", "FETCH_HEAD"]:
            return SimpleNamespace(stdout=self.pull_head)
        if arguments[:2] == ["git", "fetch"]:
            return SimpleNamespace(stdout="")
        raise AssertionError("Unexpected CLI call in offline flow: " + repr(arguments))

    def api(self, endpoint, *arguments):
        if endpoint == f"repos/{REPO}":
            return {"default_branch": "main"}
        if endpoint == f"repos/{REPO}/issues/14":
            return deepcopy(self.original_issue)
        if endpoint == f"repos/{REPO}/pulls/{PR}":
            return {"number": PR, "state": "open", "title": "연구검토 #14: 계획",
                    "head": {"sha": self.pull_head, "ref": "research/review-issue-14",
                             "repo": {"full_name": REPO}},
                    "base": {"ref": "main"}, "body": self.pull_body}
        if "/collaborators/" in endpoint:
            return {"permission": "write"}
        if "/issues/comments/" in endpoint:
            return deepcopy(self.comments[int(endpoint.rsplit("/", 1)[1])])
        raise AssertionError("Unexpected API request: " + endpoint)

    def branch_file(self, flow, commit, path, optional=False):
        name = path.rsplit("/", 1)[1]
        if name in self.files:
            return self.files[name]
        if optional:
            return None
        raise AssertionError("Missing synthetic PR file: " + path)

    def prepare(self, *, known=True):
        self.assets, profile, self.mapping, *_ = self.fixture.package(known=known)
        profile["probability_output_planned"] = True
        self.fixture.save_config()
        return research.prepare_ai(14, self.work, target_kind="issue")

    def plan(self, *, extra=(), updates=None, work=None, target=14):
        work = work or self.work
        context = read_json(work / "RESEARCH_CONTEXT.json")
        write_json(work / "coordination-proposal.json", {
            "schema_version": 1, "context_hash": context["context_hash"],
            "eda_add": list(extra), "objectives": {"eda": [], "evaluation": []},
            "feedback_ids": [item["comment_id"] for item in context["feedback"]],
            "selection_updates": updates or {}, "reason": "기본 점검 결과와 사람 요청을 반영합니다."})
        return research.plan_eda(target, work)

    def recommend(self, *, work=None):
        work = work or self.work
        request = read_json(work / "AI_REQUEST.json")
        session = read_json(work / "session.json")
        folder = self.root / session["report_dir"]
        write_json(folder / "ai-recommendations.json", {
            "schema_version": 1, "provider": "github-copilot-cloud-agent",
            "context_hash": request["context_hash"], "status": "provided",
            "eda": [{"id": name, "reason": "계산한 점검을 보존합니다.", "evidence": ["data_id"]}
                    for name in request["fixed_eda_recommendation_ids"]],
            "evaluation": [{"id": "roc_auc", "reason": "구별 성능을 점검합니다.", "evidence": ["data_id"]}]})

    def finish_initial(self, *, extra=()):
        self.prepare()
        planned = self.plan(extra=extra)
        research.execute_eda(14, self.work, planned["plan_hash"])
        self.recommend()
        return research.apply_ai(14, self.work)

    def begin_feedback(self, initial, body):
        self.pull_body = initial["pull_request_body"]
        folder = self.root / "research" / "reports" / "issue-14"
        self.files = {path.name: path.read_text(encoding="utf-8") for path in folder.iterdir() if path.is_file()}
        self.comments[1] = {"id": 1, "body": body, "user": {"login": "maintainer", "type": "User"},
                            "issue_url": f"https://api.github.com/repos/{REPO}/issues/{PR}"}
        self.files["feedback-requests.json"] = json.dumps({
            "schema_version": 1, "kind": "pr1-natural-feedback", "repository": REPO,
            "pull_request": PR, "issue_number": 14, "comments": [{
                "comment_id": 1, "actor": "maintainer",
                "body_sha256": hashlib.sha256(body.encode()).hexdigest()}]})
        work = self.base / "feedback"
        research.prepare_ai(PR, work)
        return work, folder

    def test_intake_does_not_pull_data_or_create_a_preanalysis_pr(self):
        with patch.object(research, "image_assets") as assets, \
                patch.object(research, "write_payload", side_effect=AssertionError("early PR")):
            outcome = research.run_workflow({}, "issues", REPO, "synthetic-collaborator",
                                            "main", self.work)
        self.assertEqual(outcome["ai_dispatch"]["target_kind"], "issue")
        self.assertFalse(outcome["pull_request_created"])
        assets.assert_not_called()

    def test_basic_eda_is_complete_before_ai_receives_context(self):
        outcome = self.prepare()
        self.assertEqual(outcome["status"], "basic_eda_completed")
        context = read_json(self.work / "RESEARCH_CONTEXT.json")
        self.assertEqual(context["executed_eda_ids"],
                         ["schema", "patient_overlap", "demographics", "cohort"])
        self.assertEqual(set(context["available_eda_ids"]),
                         {"prediction_timing", "label_observability", "preprocessing_lineage"})
        for name in context["executed_eda_ids"]:
            self.assertEqual(self.calls[name].call_count, 1)
        self.publish.assert_not_called()

    def test_extra_execution_only_computes_unused_blocks_and_publishes_last(self):
        self.prepare()
        planned = self.plan(extra=["prediction_timing"])
        self.publish.assert_not_called()
        result = research.execute_eda(14, self.work, planned["plan_hash"])
        self.assertEqual(result["executed_ids"], ["prediction_timing"])
        for name in ("schema", "patient_overlap", "demographics", "cohort", "prediction_timing"):
            self.assertEqual(self.calls[name].call_count, 1)
        self.recommend()
        with patch.object(research, "payload_plans", side_effect=AssertionError("duplicate final plans")):
            outcome = research.apply_ai(14, self.work)
        self.assertTrue(outcome["create_required"])
        self.assertIsNone(outcome["pull_request"])
        self.assertIn("연구검토", outcome["pull_request_body"])
        self.publish.assert_called_once()

    def test_empty_extra_plan_does_not_recompute_basic_blocks(self):
        self.prepare()
        planned = self.plan()
        result = research.execute_eda(14, self.work, planned["plan_hash"])
        self.assertEqual(result["executed_ids"], [])
        self.assertEqual(len(result["reused_ids"]), 4)

    def test_input_mapping_precedes_basic_eda_only_when_needed(self):
        outcome = self.prepare(known=False)
        self.assertEqual(outcome["phase"], "input_mapping")
        self.assertTrue(all(check.call_count == 0 for check in self.calls.values()))
        schema = read_json(self.work / "INPUT_SCHEMA_REQUEST.json")["request"]
        write_json(self.work / "input-mapping-proposal.json", self.fixture.recommendation(schema, self.mapping))
        outcome = research.apply_input_mapping(14, self.work)
        self.assertTrue(outcome["basic_eda_completed"])
        self.assertEqual(self.calls["schema"].call_count, 1)

    def test_changed_assets_are_rejected_before_extra_calculation(self):
        self.prepare()
        planned = self.plan(extra=["prediction_timing"])
        with (self.work / "assets" / "db_features.csv").open("a", encoding="utf-8") as handle:
            handle.write("\n")
        with self.assertRaises(ValueError):
            research.execute_eda(14, self.work, planned["plan_hash"])
        self.calls["prediction_timing"].assert_not_called()

    def test_pure_planning_does_not_reload_or_reparse_prepared_rows(self):
        self.prepare()
        with patch.object(research, "load_context", side_effect=AssertionError("planner row reload")):
            self.plan(extra=["prediction_timing"])

    def test_context_cache_tampering_is_not_treated_as_verified_input(self):
        self.prepare()
        planned = self.plan(extra=["prediction_timing"])
        path = self.work / "VALIDATED_CONTEXT.json"
        value = read_json(path)
        value["metadata"][0]["target"] = 1 - value["metadata"][0]["target"]
        write_json(path, value)
        with self.assertRaisesRegex(ValueError, "문맥"):
            research.execute_eda(14, self.work, planned["plan_hash"])

    def test_public_context_and_publication_do_not_include_private_cached_rows(self):
        self.finish_initial()
        public_context = (self.work / "RESEARCH_CONTEXT.json").read_text(encoding="utf-8")
        self.assertNotIn("private-shared-patient", public_context)
        session = read_json(self.work / "session.json")
        self.assertTrue((self.work / "VALIDATED_CONTEXT.json").is_file())
        self.assertFalse(any("VALIDATED_CONTEXT" in name for name in session["allowed_outputs"]))
        for name in session["allowed_outputs"]:
            self.assertNotIn("private-shared-patient", (self.root / name).read_text(encoding="utf-8"))

    def test_visible_native_body_boundaries_preserve_human_text_and_legacy_prs(self):
        generated = research.merge_review_body("", "합성 분석 결과")
        self.assertNotIn("<!--", generated)
        updated = research.merge_review_body(
            generated + "\n\n사람이 남긴 검토 내용", "새 합성 결과")
        self.assertIn("사람이 남긴 검토 내용", updated)
        self.assertNotIn("\n합성 분석 결과\n", updated)
        old = research.LEGACY_AUTO_START + "\n과거 내용\n" + research.LEGACY_AUTO_END + "\n사람 메모"
        self.assertIn("사람 메모", research.merge_review_body(old, "새 결과"))

    def test_final_metric_recommendation_cannot_trigger_late_eda(self):
        self.prepare()
        planned = self.plan()
        research.execute_eda(14, self.work, planned["plan_hash"])
        self.recommend()
        path = self.root / "research" / "reports" / "issue-14" / "ai-recommendations.json"
        value = read_json(path)
        value["eda"].append({"id": "prediction_timing", "reason": "늦은 추가", "evidence": ["data_id"]})
        write_json(path, value)
        with self.assertRaises(ValueError):
            research.apply_ai(14, self.work)
        self.calls["prediction_timing"].assert_not_called()
        self.publish.assert_not_called()

    def test_metric_only_feedback_reuses_all_eda_and_updates_same_pr(self):
        initial = self.finish_initial(extra=["prediction_timing"])
        previous_counts = {name: handler.call_count for name, handler in self.calls.items()}
        with patch.object(research, "image_assets", side_effect=AssertionError("metric-only asset extraction")), \
                patch.object(research, "load_context", side_effect=AssertionError("metric-only row loading")), \
                patch.object(research, "_context_for_session", side_effect=AssertionError("full private context reload")):
            work, folder = self.begin_feedback(initial, "AP를 주지표로 변경해 주세요.")
            planned = self.plan(work=work, target=PR,
                                updates={"evaluation": {"primary_metric": "average_precision"}})
            result = research.execute_eda(PR, work, planned["plan_hash"])
            self.assertEqual(result["executed_ids"], [])
            self.recommend(work=work)
            outcome = research.apply_ai(PR, work)
        self.assertFalse((work / "assets").exists())
        self.assertFalse((work / "VALIDATED_CONTEXT.json").exists())
        self.assertEqual(outcome["operation"], "update")
        self.assertEqual(outcome["pull_request"], PR)
        self.assertFalse(outcome["create_required"])
        self.assertEqual({name: handler.call_count for name, handler in self.calls.items()}, previous_counts)
        self.assertEqual(read_json(folder / "evaluation-plan.json")["primary_metric"], "average_precision")
        self.cache_auth.assert_called_once()

    def test_eda_feedback_only_calculates_the_requested_new_block(self):
        initial = self.finish_initial()
        before = {name: handler.call_count for name, handler in self.calls.items()}
        work, _ = self.begin_feedback(initial, "예측 시점 점검을 추가해 주세요.")
        self.assertEqual(self.extract.call_count, 1, "Feedback preparation must not extract assets yet")
        self.assertFalse((work / "assets").exists())
        planned = self.plan(work=work, target=PR,
                            updates={"eda": {"add": ["prediction_timing"]}})
        result = research.execute_eda(PR, work, planned["plan_hash"])
        self.assertEqual(self.extract.call_count, 2, "New EDA extracts assets once")
        research.execute_eda(PR, work, planned["plan_hash"])
        self.assertEqual(self.extract.call_count, 2, "Tool retry must not extract assets again")
        self.assertEqual(result["executed_ids"], ["prediction_timing"])
        for name in ("schema", "patient_overlap", "demographics", "cohort"):
            self.assertEqual(self.calls[name].call_count, before[name])
        self.recommend(work=work)
        outcome = research.apply_ai(PR, work)
        self.assertEqual(outcome["pull_request"], PR)
        self.assertFalse(outcome["create_required"])

    def test_missing_publication_proof_prepares_fresh_assets_once(self):
        initial = self.finish_initial()
        self.cache_auth.return_value = False
        before = self.calls["schema"].call_count
        work, _ = self.begin_feedback(initial, "계획을 다시 확인해 주세요.")
        self.assertEqual(self.extract.call_count, 2)
        self.assertEqual(self.calls["schema"].call_count, before + 1)
        self.assertTrue((work / "assets").is_dir())

    def test_publication_never_loads_full_rows_or_verifies_asset_files_again(self):
        self.prepare()
        planned = self.plan(extra=["prediction_timing"])
        research.execute_eda(14, self.work, planned["plan_hash"])
        self.recommend()
        self.remote.reset_mock()
        with patch.object(research, "_context_for_session", side_effect=AssertionError("publication row reload")), \
                patch("app.asset_contract.verify_files", side_effect=AssertionError("publication asset rescan")):
            research.apply_ai(14, self.work)
        requests = [call.args[0] for call in self.remote.call_args_list]
        self.assertEqual(requests.count(f"repos/{REPO}/issues/14"), 1)

    def test_changed_image_does_not_reuse_old_results_or_skip_input_validation(self):
        initial = self.finish_initial()
        path = self.root / "config" / "assets.json"
        config = read_json(path)
        config["data_image"] = "ghcr.io/fixture-owner/data@sha256:" + "d" * 64
        write_json(path, config)
        before = self.calls["schema"].call_count
        work, _ = self.begin_feedback(initial, "입력과 계획을 확인해 주세요.")
        self.assertEqual(self.extract.call_count, 2)
        self.assertEqual(self.calls["schema"].call_count, before + 1)
        self.assertTrue((work / "assets").is_dir())
        self.cache_auth.assert_not_called()

    def test_compact_planning_context_tampering_stops_publication(self):
        self.prepare()
        planned = self.plan()
        research.execute_eda(14, self.work, planned["plan_hash"])
        self.recommend()
        path = self.work / "PLANNING_CONTEXT.json"
        value = read_json(path)
        value["data_manifest"]["data_id"] = "f" * 64
        write_json(path, value)
        with self.assertRaisesRegex(ValueError, "문맥"):
            research.apply_ai(14, self.work)
        self.publish.assert_not_called()

    def test_no_change_feedback_does_not_claim_publication_is_ready(self):
        initial = self.finish_initial()
        work, _ = self.begin_feedback(initial, "현재 선택을 확인해 주세요.")
        planned = self.plan(work=work, target=PR)
        research.execute_eda(PR, work, planned["plan_hash"])
        self.recommend(work=work)
        self.publish.return_value = None
        with patch.object(research, "merge_review_body", return_value=self.pull_body):
            outcome = research.apply_ai(PR, work)
        self.assertEqual(outcome["status"], "unchanged")
        self.assertFalse(outcome["publication_ready"])
        self.assertFalse(outcome["create_required"])
        self.assertFalse(outcome["push_required"])
        self.assertFalse(outcome["body_refresh_required"])

    def test_initial_publication_cannot_create_an_empty_pr(self):
        self.publish.return_value = None
        with self.assertRaisesRegex(ValueError, "빈 최초 PR"):
            self.finish_initial()


if __name__ == "__main__":
    unittest.main()
