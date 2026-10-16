import ast
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app import planning, research, review


ROOT = Path(__file__).resolve().parents[1]
REPO = "example/workshop"
PR = 42
ISSUE = 7
HEAD = "a" * 40
MERGE = "b" * 40
MAIN = "c" * 40
CODE = "d" * 40
PREFIX = f"research/reports/issue-{ISSUE}/"
BRANCH = f"research/review-issue-{ISSUE}"


def blob_sha(content):
    return hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()


def encoded(value):
    return review._json(value).encode("utf-8")


def comment(identifier, body, login="maintainer", kind="User"):
    return {
        "id": identifier, "body": body, "user": {"login": login, "type": kind},
        "issue_url": f"https://api.github.com/repos/{REPO}/issues/{PR}",
    }


def pull(merged=False):
    return {
        "number": PR, "state": "closed" if merged else "open",
        "title": f"연구검토 #{ISSUE}: 교육용 계획",
        "head": {"ref": BRANCH, "sha": HEAD, "repo": {"full_name": REPO}},
        "base": {"ref": "main", "repo": {"full_name": REPO}},
        "merged": merged, "merge_commit_sha": MERGE,
        "merged_by": {"login": "maintainer", "type": "User"},
        "merged_at": "2026-10-16T01:00:00Z",
    }


def event(item):
    return {
        "action": "created", "repository": {"full_name": REPO, "default_branch": "main"},
        "issue": {"number": PR, "pull_request": {}}, "comment": copy.deepcopy(item),
    }


def merged_event():
    return {"action": "closed", "repository": {"full_name": REPO, "default_branch": "main"},
            "pull_request": pull(True)}


def request_marker():
    return {"schema_version": 1, "repository": REPO, "issue_number": ISSUE,
            "issue_url": f"https://github.com/{REPO}/issues/{ISSUE}"}


def approval_files():
    source = json.loads((ROOT / "config" / "prepared-source.json").read_text(encoding="utf-8"))
    catalogs = {stage: planning.load_catalog(stage) for stage in review.STAGES}
    policy = planning.load_policy()
    selected = planning.default_selection()
    selected["eda"]["exclude"] = ["demographics", "cohort"]
    selected["evaluation"].update(primary_metric="roc_auc", add=["roc_auc"])
    eda = {
        "schema_version": 1, "kind": "aggregate-eda", "report_generated": True,
        "model_execution_performed": False, "training_performed": False,
        "preprocessing_performed": False, "data_id": "1" * 64, "assessment_status": "failed",
        "provenance": {"data_image": "ghcr.io/example/data@sha256:" + "2" * 64,
                       "code_commit": CODE},
        "results": [
            {"block": "schema", "status": "unverifiable", "details": {
                "numeric_contract": "passed", "actual_count": len(source["features"]),
                "expected_count": len(source["features"]), "schema_match": True,
                "duplicate_columns": 0,
                "rows": sum(split["rows"] for split in source["splits"].values()),
                "split_rows": {name: split["rows"] for name, split in source["splits"].items()}}},
            {"block": "patient_overlap", "status": "failed",
             "details": {"overlaps": source["expected_patient_overlap"]}},
        ],
    }
    manifest = {
        "data_id": eda["data_id"], "prepared_source": source, "files": {"metadata.csv": None},
        "feature_columns": source["features"],
        "split_rows": {name: source["splits"][name]["rows"]
                       for name in ("train", "validation", "test")}}
    initial = planning.build_plans(eda, manifest, selected, None)
    ai = {
        "schema_version": 1, "provider": "github-copilot-cloud-agent",
        "context_hash": initial["recommendation_request"]["context_hash"], "status": "provided",
        "eda": [{"id": "schema", "reason": "공개 구조 점검", "evidence": ["data_id"]}],
        "evaluation": [],
    }
    plans = planning.build_plans(eda, manifest, selected, ai)
    values = {
        "request.json": request_marker(), "eda.json": eda, "eda-plan.json": plans["eda"],
        "evaluation-plan.json": plans["evaluation"], "selection.json": selected,
        "ai-recommendations.json": ai, "recommendation-request.json": plans["recommendation_request"],
        "config/assets.json": {
            "schema_version": 1, "data_image": eda["provenance"]["data_image"],
            "model_image": "ghcr.io/example/model@sha256:" + "3" * 64},
        "config/prepared-source.json": source, "config/eda-catalog.json": catalogs["eda"],
        "config/metric-catalog.json": catalogs["evaluation"], "config/planning-policy.json": policy,
    }
    return {name: encoded(value) for name, value in values.items()}


class FakeGitHub:
    """In-memory Git data and paginated comments; no network/model process exists."""

    def __init__(self):
        self.pull = pull()
        self.permissions = {"maintainer": "write", "other": "maintain", "reader": "read"}
        self.comments = []
        self.refs = {BRANCH: HEAD, "main": MAIN}
        self.trees = {}
        self.blobs = {}
        self.commits = {}
        self.writes = []
        self.reads = []
        self.count = 100
        self.block_ref = False
        self.race_ref = False
        self.set_files(HEAD, {
            PREFIX + "request.json": encoded(request_marker()),
            PREFIX + "selection.json": encoded(planning.default_selection())})
        self.set_files(MAIN, {"preserved.txt": b"concurrent main change\n"})

    def set_files(self, commit, files):
        tree = {}
        for path, content in files.items():
            sha = blob_sha(content)
            self.blobs[sha] = content
            tree[path] = {"path": path, "mode": "100644", "type": "blob", "sha": sha,
                          "size": len(content)}
        tree_sha = self.new_sha()
        self.trees[tree_sha] = tree
        self.commits[commit] = tree_sha

    def new_sha(self):
        self.count += 1
        return f"{self.count:040x}"

    def read(self, *arguments):
        self.reads.append(arguments)
        path = next(argument for argument in arguments if argument.startswith("repos/"))
        path = path[len(f"repos/{REPO}/"):]
        if path == f"pulls/{PR}":
            result = copy.deepcopy(self.pull)
            result["head"]["sha"] = self.refs[BRANCH]
            return result
        if path.startswith("collaborators/"):
            login = path.split("/")[1]
            return {"permission": self.permissions.get(login, "none")}
        if path.startswith("issues/comments/"):
            identifier = int(path.rsplit("/", 1)[1])
            return copy.deepcopy(next(item for item in self.comments if item["id"] == identifier))
        if path == f"issues/{PR}/comments":
            page = int(next(item.split("=")[1] for item in arguments if item.startswith("page=")))
            return copy.deepcopy(self.comments[(page - 1) * 100:page * 100])
        if path.startswith("git/trees/"):
            sha = path.split("/")[2].split("?")[0]
            return {"truncated": False, "tree": list(self.trees[self.commits.get(sha, sha)].values())}
        if path.startswith("git/blobs/"):
            sha = path.rsplit("/", 1)[1]
            return {"encoding": "base64", "content": base64.b64encode(self.blobs[sha]).decode()}
        if path.startswith("git/ref/heads/"):
            branch = path[len("git/ref/heads/"):]
            return {"object": {"sha": self.refs[branch]}}
        if path.startswith("git/commits/"):
            sha = path.rsplit("/", 1)[1]
            return {"tree": {"sha": self.commits[sha]}}
        if path == "":
            return {"default_branch": "main"}
        raise AssertionError(f"Unexpected API read: {arguments}")

    def command(self, arguments, **kwargs):
        if arguments[:2] != ["gh", "api"]:
            raise AssertionError("Real CLI, Docker, AI or git execution is forbidden in this fixture.")
        method = arguments[arguments.index("--method") + 1]
        path = next(arg for arg in arguments if arg.startswith("repos/"))[len(f"repos/{REPO}/"):]
        payload = json.loads(kwargs["input_text"])
        self.writes.append((method, path, copy.deepcopy(payload)))
        sha = self.new_sha()
        if path == "git/blobs":
            content = base64.b64decode(payload["content"])
            sha = blob_sha(content)
            self.blobs[sha] = content
        elif path == "git/trees":
            tree = copy.deepcopy(self.trees[payload["base_tree"]])
            for entry in payload["tree"]:
                tree[entry["path"]] = {**entry, "size": len(self.blobs[entry["sha"]])}
            self.trees[sha] = tree
        elif path == "git/commits":
            self.commits[sha] = payload["tree"]
        elif path.startswith("git/refs/heads/"):
            if self.block_ref or self.race_ref:
                raise RuntimeError("protected branch or simultaneous update")
            self.refs[path[len("git/refs/heads/"):]] = payload["sha"]
        else:
            raise AssertionError(f"Unexpected API write: {path}")
        return SimpleNamespace(stdout=json.dumps({"sha": sha}), returncode=0)

    def content(self, branch, path):
        tree = self.trees[self.commits[self.refs[branch]]]
        return self.blobs[tree[path]["sha"]]

    def json(self, branch, path):
        return json.loads(self.content(branch, path))


class CommandTests(unittest.TestCase):
    def apply(self, body, selected=None):
        return review.apply_commands(selected or planning.default_selection(), body)

    def test_explicit_add_remove_alias_primary_threshold(self):
        selected, result = self.apply(
            "/plan add eda prediction-timing\n"
            "/plan remove eda demographics\n/plan add evaluation sensitivity\n"
            "/plan remove evaluation brier\n/plan primary AUROC\n/plan threshold 0.27")
        self.assertEqual(result["rejected"], [])
        self.assertEqual(len(result["applied"]), 6)
        self.assertIn("prediction_timing", selected["eda"]["add"])
        self.assertIn("demographics", selected["eda"]["exclude"])
        self.assertIn("recall", selected["evaluation"]["add"])
        self.assertIn("brier_score", selected["evaluation"]["exclude"])
        self.assertEqual(selected["evaluation"]["primary_metric"], "roc_auc")
        self.assertEqual(selected["evaluation"]["threshold"], 0.27)
        self.assertNotIn("threshold", selected["evaluation"]["parameters"])

    def test_required_unknown_and_primary_removal_rejected(self):
        selected, result = self.apply(
            "/plan primary AUROC\n/plan remove evaluation roc_auc\n"
            "/plan remove eda schema\n/plan remove eda patient-overlap\n"
            "/plan add evaluation imaginary\n/plan add eda nonexistent")
        self.assertEqual(len(result["applied"]), 1)
        self.assertEqual(len(result["rejected"]), 5)
        self.assertEqual(selected["evaluation"]["primary_metric"], "roc_auc")
        self.assertEqual(selected["eda"]["exclude"], [])
        self.assertIn("먼저", result["rejected"][0]["reason"])

    def test_primary_explicitly_readds_excluded_metric_and_can_then_switch(self):
        selected, result = self.apply(
            "/plan remove evaluation recall\n/plan primary sensitivity\n"
            "/plan primary auroc\n/plan remove evaluation recall")
        self.assertEqual(result["rejected"], [])
        self.assertEqual(selected["evaluation"]["primary_metric"], "roc_auc")
        self.assertIn("recall", selected["evaluation"]["exclude"])

    def test_threshold_never_defaults_and_rejects_nonfinite_outside_range(self):
        selected, result = self.apply(
            "/plan threshold NaN\n/plan threshold inf\n/plan threshold -0.1\n"
            "/plan threshold 1.01\n/plan threshold nope")
        self.assertIsNone(selected["evaluation"]["threshold"])
        self.assertEqual(len(result["rejected"]), 5)
        for value in ("0", "1"):
            selected, result = self.apply("/plan threshold " + value)
            self.assertEqual(selected["evaluation"]["threshold"], float(value))
            self.assertEqual(result["rejected"], [])

    def test_only_exact_command_lines_are_interpreted_and_no_code_runs(self):
        body = "please /plan primary roc_auc\nimport os; os.system('bad')\nSELECT * FROM rows;\n/planet add eda schema"
        with patch("subprocess.run", side_effect=AssertionError("must not execute")):
            selected, result = self.apply(body)
        self.assertEqual(selected, planning.default_selection())
        self.assertEqual(result["applied"], [])
        self.assertEqual(result["rejected"], [])
        self.assertFalse(result["recommend"])

    def test_quotes_examples_and_mixed_prose_do_not_change_selections(self):
        for body in (
            "예시입니다.\n/plan primary roc_auc",
            "```text\n/plan threshold 0.9\n```",
            "> /plan primary roc_auc",
            "/plan primary roc_auc\n일반 설명",
        ):
            with self.subTest(body=body):
                selected, result = self.apply(body)
                self.assertEqual(selected, planning.default_selection())
                self.assertEqual(result["applied"], [])

    def test_parameter_values_and_clinical_action_remain_explicit(self):
        selected, result = self.apply(
            "/plan parameter thresholds [0.05, 0.1, 0.2]\n"
            "/plan parameter top_fraction 0.1\n/plan parameter n_bins 8\n"
            "/plan clinical-action 교육용 상담 대상 검토")
        self.assertEqual(result["rejected"], [])
        self.assertEqual(selected["evaluation"]["parameters"], {
            "thresholds": [0.05, 0.1, 0.2], "top_fraction": 0.1, "n_bins": 8})
        self.assertEqual(selected["evaluation"]["clinical_action"], "교육용 상담 대상 검토")
        selected, result = self.apply(
            "/plan parameter n_bins 0\n/plan parameter top_fraction 0\n"
            "/plan parameter thresholds [0.2, 0.1]\n/plan parameter arbitrary 4")
        self.assertEqual(selected, planning.default_selection())
        self.assertEqual(len(result["rejected"]), 4)

    def test_invalid_does_not_rollback_valid_and_recommend_changes_no_selection(self):
        selected, result = self.apply("/plan threshold bad\n/plan recommend\n/plan add eda cohort")
        self.assertEqual(len(result["rejected"]), 1)
        self.assertEqual(len(result["applied"]), 2)
        self.assertTrue(result["recommend"])
        self.assertIsNone(selected["evaluation"]["threshold"])


class _ApiCase(unittest.TestCase):
    def setUp(self):
        self.api = FakeGitHub()
        self.read = patch.object(research, "gh_json", side_effect=self.api.read).start()
        self.command = patch.object(research, "command", side_effect=self.api.command).start()
        self.refresh = patch.object(review, "_refresh", return_value={
            "status": "updated", "ai_status": "provided"}).start()
        self.dispatch = patch.object(research, "dispatch_ai", return_value={"status": "requested"}).start()
        self.addCleanup(patch.stopall)


class ReviewApiTests(_ApiCase):
    def review(self, identifier=None):
        target = self.api.comments[-1] if identifier is None else next(
            item for item in self.api.comments if item["id"] == identifier)
        return review.run_event(event(target), "issue_comment", REPO, target["user"]["login"])

    def test_atomic_two_file_write_records_hash_actor_and_rejections_not_body(self):
        self.api.comments = [comment(1, "/plan primary auroc\n/plan remove eda schema\n"
                                       "/plan add eda PRIVATE_SYNTHETIC_NOTE")]
        outcome = self.review()
        self.assertEqual(outcome["status"], "partially_applied")
        self.assertIn("필수", outcome["comments"][0]["rejected"][0]["reason"])
        state_bytes = self.api.content(BRANCH, PREFIX + "review-state.json")
        self.assertNotIn(b"PRIVATE_SYNTHETIC_NOTE", state_bytes)
        state = json.loads(state_bytes)
        self.assertEqual(state["comments"]["1"]["actor"], "maintainer")
        self.assertEqual(state["comments"]["1"]["body_sha256"],
                         hashlib.sha256(self.api.comments[0]["body"].encode()).hexdigest())
        tree_writes = [data for _, path, data in self.api.writes if path == "git/trees"]
        self.assertEqual(len(tree_writes), 1)
        self.assertEqual({entry["path"] for entry in tree_writes[0]["tree"]},
                         {PREFIX + "selection.json", PREFIX + "review-state.json"})
        ref_writes = [data for method, path, data in self.api.writes if path.startswith("git/refs/")]
        self.assertEqual(ref_writes[0]["force"], False)
        parent = next(data for _, path, data in self.api.writes if path == "git/commits")
        self.assertEqual(parent["parents"], [HEAD])
        self.refresh.assert_called_once_with(REPO, "main", "maintainer", ISSUE)

    def test_paginated_backlog_processed_in_id_order_up_to_event(self):
        self.api.comments = [comment(index, "ordinary") for index in range(1, 99)]
        self.api.comments.extend([
            comment(101, "/plan threshold 0.8", "other"),
            comment(99, "/plan threshold 0.1"),
            comment(100, "/plan add evaluation recall"),
            comment(102, "/plan threshold 0.2")])
        result = self.review(101)
        self.assertEqual([item["comment_id"] for item in result["comments"]], [99, 100, 101])
        self.assertEqual(self.api.json(BRANCH, PREFIX + "selection.json")["evaluation"]["threshold"], 0.8)
        self.refresh.assert_called_once_with(REPO, "main", "other", ISSUE)
        self.assertTrue(any("page=2" in arguments for arguments in self.api.reads))

    def test_duplicate_and_edited_applied_comment_never_reinterpreted(self):
        self.api.comments = [comment(1, "/plan threshold 0.2")]
        self.review()
        writes = len(self.api.writes)
        self.api.comments[0]["body"] = "/plan threshold 0.9"
        result = self.review()
        self.assertEqual(result["status"], "unchanged")
        self.assertIn("새 /plan", result["edit_policy"])
        self.assertEqual(len(self.api.writes), writes)
        self.assertEqual(self.api.json(BRANCH, PREFIX + "selection.json")["evaluation"]["threshold"], 0.2)

    def test_event_body_is_ignored_current_api_body_is_used(self):
        item = comment(1, "/plan threshold 0.13")
        stale_event = event(item)
        stale_event["comment"]["body"] = "/plan threshold 0.95"
        self.api.comments = [item]
        review.run_event(stale_event, "issue_comment", REPO, "maintainer")
        self.assertEqual(self.api.json(BRANCH, PREFIX + "selection.json")["evaluation"]["threshold"], 0.13)

    def test_bot_and_no_write_permission_defer_without_changes(self):
        for login, kind in (("reader", "User"), ("agent", "Bot"), ("external", "User")):
            with self.subTest(login=login):
                self.api.comments = [comment(1, "/plan primary auroc", login, kind)]
                self.assertEqual(self.review()["status"], "deferred")
                self.assertEqual(self.api.writes, [])

    def test_each_backlog_author_permission_checked_and_rejected_visible(self):
        self.api.comments = [
            comment(1, "/plan threshold 0.9", "reader"),
            comment(2, "/plan threshold 0.3", "maintainer")]
        result = self.review()
        self.assertEqual(result["status"], "partially_applied")
        self.assertTrue(result["comments"][0]["rejected"])
        self.assertEqual(self.api.json(BRANCH, PREFIX + "selection.json")["evaluation"]["threshold"], 0.3)

    def test_actor_mismatch_wrong_repo_and_wrong_pr_are_rejected(self):
        self.api.comments = [comment(1, "/plan primary auroc")]
        with self.assertRaisesRegex(ValueError, "actor"):
            review.run_event(event(self.api.comments[0]), "issue_comment", REPO, "other")
        self.api.pull["head"]["repo"]["full_name"] = "outsider/fork"
        with self.assertRaisesRegex(ValueError, "저장소"):
            self.review()
        self.assertEqual(self.api.writes, [])

    def test_required_rejection_is_not_reported_as_success(self):
        self.api.comments = [comment(1, "/plan remove eda patient_overlap")]
        result = self.review()
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["comments"][0]["applied"], [])
        self.refresh.assert_not_called()

    def test_recommend_dispatches_even_no_selection_change_with_current_pr(self):
        self.api.comments = [comment(1, "/plan recommend")]
        result = self.review()
        self.assertFalse(result["selection_changed"])
        self.dispatch.assert_called_once_with(
            {"status": "updated", "ai_status": "stale",
             "pull_url": f"https://github.com/{REPO}/pull/{PR}"}, REPO, "main")

    def test_normal_refresh_already_dispatching_does_not_double_request(self):
        self.refresh.return_value["ai_dispatch"] = {"status": "requested"}
        self.api.comments = [comment(1, "/plan recommend")]
        self.review()
        self.dispatch.assert_not_called()

    def test_refresh_failure_keeps_selection_and_reports_failure(self):
        self.api.comments = [comment(1, "/plan primary auroc")]
        self.refresh.side_effect = RuntimeError("synthetic refresh failure")
        with patch.object(review, "_summary") as summary:
            with self.assertRaises(RuntimeError):
                self.review()
        self.assertEqual(self.api.json(BRANCH, PREFIX + "selection.json")["evaluation"]["primary_metric"], "roc_auc")
        self.assertEqual(summary.call_args.args[0]["status"], "failed")
        self.assertTrue(summary.call_args.args[0]["review_pr_preserved"])
        writes = len(self.api.writes)
        self.refresh.side_effect = None
        recovered = self.review()
        self.assertEqual(recovered["status"], "unchanged")
        self.assertEqual(recovered["refresh"]["status"], "updated")
        self.assertEqual(len(self.api.writes), writes)
        self.assertEqual(self.refresh.call_count, 2)

    def test_ref_race_never_force_updates_selection(self):
        self.api.comments = [comment(1, "/plan threshold 0.2")]
        self.api.race_ref = True
        with self.assertRaisesRegex(RuntimeError, "force=false"):
            self.review()
        self.assertEqual(self.api.refs[BRANCH], HEAD)
        self.refresh.assert_not_called()

    def test_ordinary_text_no_audit_or_execution(self):
        self.api.comments = [comment(1, "실행해 주세요: Python 또는 SQL 명령")]
        self.assertEqual(self.review()["status"], "not_applicable")
        self.assertEqual(self.api.writes, [])
        self.refresh.assert_not_called()

    def test_ordinary_comments_on_unrelated_pr_are_not_failed_research_jobs(self):
        self.api.pull["head"]["ref"] = "feature/ordinary-code"
        self.api.comments = [comment(1, "확인했습니다.")]
        self.assertEqual(self.review()["status"], "not_applicable")
        self.assertEqual(self.api.writes, [])
        self.refresh.assert_not_called()


class ApprovalReadinessTests(unittest.TestCase):
    def plans(self):
        files = approval_files()
        return {"eda": json.loads(files["eda-plan.json"]),
                "evaluation": json.loads(files["evaluation-plan.json"])}

    def test_ready_is_educational_only_and_keeps_known_eda_failure(self):
        readiness = planning.approval_readiness(self.plans())
        self.assertTrue(readiness["ready"])
        self.assertEqual(readiness["approval_scope"], "educational_demo_only")
        self.assertFalse(readiness["clinical_use_approved"])

    def test_ai_primary_and_blocked_requirements_are_distinct(self):
        plans = self.plans()
        plans["eda"]["ai_status"] = "stale"
        plans["evaluation"]["primary_metric"] = None
        plans["evaluation"]["items"][0]["state"] = "blocked"
        readiness = planning.approval_readiness(plans)
        self.assertFalse(readiness["ready"])
        self.assertEqual(len(readiness["reasons"]), 3)


class FreezeTests(_ApiCase):
    def setUp(self):
        super().setUp()
        self.api.pull = pull(True)
        self.files = approval_files()
        self.api.set_files(MERGE, self.remote_files())

    def remote_files(self):
        return {name if name.startswith("config/") else PREFIX + name: value
                for name, value in self.files.items()}

    def mutate(self, name, update):
        value = json.loads(self.files[name])
        update(value)
        self.files[name] = encoded(value)
        self.api.set_files(MERGE, self.remote_files())

    def freeze(self):
        return review.run_event(merged_event(), "pull_request", REPO, "maintainer")

    def test_freeze_all_hashes_snapshot_scope_limits_and_latest_main_preserved(self):
        result = self.freeze()
        self.assertEqual(result["status"], "frozen")
        path = f"research/approved/issue-{ISSUE}/{MERGE}.json"
        snapshot = self.api.json("main", path)
        self.assertEqual(snapshot["file_hashes"], {
            name: hashlib.sha256(content).hexdigest() for name, content in self.files.items()})
        self.assertEqual(snapshot["source_head_commit"], HEAD)
        self.assertEqual(snapshot["merge_commit"], MERGE)
        self.assertEqual(snapshot["code_commit"], CODE)
        self.assertEqual(snapshot["primary_metric"], "roc_auc")
        self.assertEqual(snapshot["approval_scope"], "educational_demo_only")
        self.assertFalse(snapshot["clinical_use_approved"])
        self.assertFalse(snapshot["metric_execution_performed"])
        self.assertFalse(snapshot["model_execution_performed"])
        self.assertTrue(snapshot["limitations"]["validation_development_reuse"])
        self.assertFalse(snapshot["limitations"]["independent_clinical_validation"])
        self.assertTrue(snapshot["limitations"]["patient_overlap"]["overlaps"]["train_validation"])
        self.assertIn("schema", snapshot["limitations"]["uncertain_blocks"])
        self.assertEqual(self.api.content("main", "preserved.txt"), b"concurrent main change\n")
        commits = [value for _, path, value in self.api.writes if path == "git/commits"]
        self.assertEqual(commits[0]["parents"], [MAIN])
        tree_writes = [value for _, path, value in self.api.writes if path == "git/trees"]
        self.assertEqual([entry["path"] for entry in tree_writes[0]["tree"]], [path])
        self.assertFalse(next(value for _, path, value in self.api.writes
                              if path.startswith("git/refs/"))["force"])
        self.refresh.assert_not_called()
        self.dispatch.assert_not_called()

    def test_same_record_noop_and_changed_record_is_immutable(self):
        result = self.freeze()
        writes = len(self.api.writes)
        self.assertEqual(self.freeze()["status"], "unchanged")
        self.assertEqual(len(self.api.writes), writes)
        current = self.api.refs["main"]
        self.api.set_files(current, {result["approved_plan"]: b'{"tampered":true}\n'})
        with self.assertRaisesRegex(ValueError, "불변성"):
            self.freeze()
        self.assertEqual(len(self.api.writes), writes)

    def test_nonmerged_and_untrusted_merger_never_freeze(self):
        item = merged_event()
        item["pull_request"]["merged"] = False
        self.assertEqual(review.run_event(item, "pull_request", REPO, "maintainer")["status"], "not_applicable")
        self.api.pull["merged_by"] = {"login": "reader", "type": "User"}
        with self.assertRaisesRegex(ValueError, "권한"):
            self.freeze()
        self.assertEqual(self.api.writes, [])

    def test_wrong_repo_base_branch_marker_and_merge_sha_rejected(self):
        for field, value in (("head_repo", "outsider/fork"), ("base", "development"),
                             ("branch", "feature/custom"), ("sha", "e" * 40)):
            with self.subTest(field=field):
                self.api.pull = pull(True)
                if field == "head_repo":
                    self.api.pull["head"]["repo"]["full_name"] = value
                elif field == "base":
                    self.api.pull["base"]["ref"] = value
                elif field == "branch":
                    self.api.pull["head"]["ref"] = value
                else:
                    self.api.pull["merge_commit_sha"] = value
                with self.assertRaises(ValueError):
                    self.freeze()
        self.api.pull = pull(True)
        self.mutate("request.json", lambda value: value.update(issue_number=99))
        with self.assertRaisesRegex(ValueError, "마커"):
            self.freeze()
        self.assertEqual(self.api.writes, [])

    def test_pending_ai_incomplete_and_no_primary_rejected(self):
        original = copy.deepcopy(self.files)
        for name, update in (
            ("evaluation-plan.json", lambda value: value.update(ai_status="pending")),
            ("eda-plan.json", lambda value: value.update(planning_complete=False)),
            ("selection.json", lambda value: value["evaluation"].update(primary_metric=None)),
            ("evaluation-plan.json", lambda value: value.update(primary_metric=None)),
            ("ai-recommendations.json", lambda value: value.update(context_hash="0" * 64)),
        ):
            with self.subTest(name=name):
                self.files = copy.deepcopy(original)
                self.mutate(name, update)
                with self.assertRaises(ValueError):
                    self.freeze()
        self.assertEqual(self.api.writes, [])

    def test_blocked_selected_metric_rejected(self):
        self.mutate("selection.json", lambda value: value["evaluation"]["add"].append("recall"))
        source = json.loads(self.files["config/prepared-source.json"])
        eda = json.loads(self.files["eda.json"])
        manifest = {
            "data_id": eda["data_id"], "prepared_source": source, "files": {"metadata.csv": None},
            "feature_columns": source["features"],
            "split_rows": {name: source["splits"][name]["rows"]
                           for name in ("train", "validation", "test")}}
        plans = planning.build_plans(eda, manifest, json.loads(self.files["selection.json"]),
                                     json.loads(self.files["ai-recommendations.json"]))
        self.files["evaluation-plan.json"] = encoded(plans["evaluation"])
        self.api.set_files(MERGE, self.remote_files())
        with self.assertRaisesRegex(ValueError, "blocked"):
            self.freeze()
        self.assertEqual(self.api.writes, [])

    def test_unpinned_or_mismatched_images_and_model_execution_rejected(self):
        original = copy.deepcopy(self.files)
        for name, update in (
            ("config/assets.json", lambda value: value.update(data_image="ghcr.io/example/data:latest")),
            ("config/assets.json", lambda value: value.update(model_image="ghcr.io/example/model:latest")),
            ("eda.json", lambda value: value["provenance"].update(
                data_image="ghcr.io/example/data@sha256:" + "f" * 64)),
            ("eda.json", lambda value: value.update(model_execution_performed=True)),
        ):
            with self.subTest(name=name):
                self.files = copy.deepcopy(original)
                self.mutate(name, update)
                with self.assertRaises(ValueError):
                    self.freeze()
        self.assertEqual(self.api.writes, [])

    def test_approval_reads_merge_not_latest_main_reports(self):
        self.api.set_files(MAIN, {"preserved.txt": b"concurrent main change\n",
                                 PREFIX + "evaluation-plan.json": b'{"ai_status":"pending"}'})
        self.assertEqual(self.freeze()["status"], "frozen")

    def test_protected_or_racing_main_ref_is_not_forced(self):
        for attribute in ("block_ref", "race_ref"):
            with self.subTest(attribute=attribute):
                setattr(self.api, attribute, True)
                with self.assertRaisesRegex(RuntimeError, "force=false"):
                    self.freeze()
                self.assertEqual(self.api.refs["main"], MAIN)
                setattr(self.api, attribute, False)


class IsolationAndWorkflowTests(unittest.TestCase):
    def test_normalized_internal_refresh_human_actor_and_source_unchanged(self):
        runtime = ROOT.parent / "review-plan-synthetic-runner"
        source = research.ROOT
        calls = []

        def command(arguments, **kwargs):
            calls.append((arguments, kwargs))
            if arguments[:3] == ["git", "status", "--porcelain"]:
                return SimpleNamespace(stdout="")
            if arguments[:3] == ["git", "rev-parse", "HEAD"]:
                return SimpleNamespace(stdout=CODE)
            if arguments[:2] == ["git", "clone"]:
                Path(arguments[-1]).mkdir()
            return SimpleNamespace(stdout="", returncode=0)

        def run(*arguments, **kwargs):
            self.assertNotEqual(research.ROOT, source)
            self.assertFalse(research.ROOT.resolve().is_relative_to(source.resolve()))
            self.assertEqual(arguments[:6], (
                {"repository": {"full_name": REPO, "default_branch": "main"}},
                "workflow_dispatch", REPO, "maintainer", "main", arguments[5]))
            self.assertEqual(kwargs, {"manual_number": ISSUE})
            return {"status": "updated"}

        environment = {"RUNNER_TEMP": str(runtime), "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2"}
        with patch.dict(os.environ, environment), patch.object(research, "command", side_effect=command), \
                patch.object(research, "run_workflow", side_effect=run) as runner:
            try:
                self.assertEqual(review._refresh(REPO, "main", "maintainer", ISSUE)["status"], "updated")
            finally:
                if runtime.exists():
                    runtime.rmdir()
        self.assertEqual(research.ROOT, source)
        self.assertEqual(runner.call_count, 1)
        self.assertFalse(any(arguments[:3] == ["git", "remote", "set-url"]
                             and kwargs.get("cwd") == source for arguments, kwargs in calls))

    def test_remote_cli_disabled_locally(self):
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}), \
                patch.object(research, "command", side_effect=AssertionError("no CLI")):
            with self.assertRaises(SystemExit):
                review.main()

    def test_no_model_or_metric_imports(self):
        tree = ast.parse((ROOT / "app" / "review.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            self.assertFalse(any(name.startswith(("app.infer", "app.blocks.metrics", "sklearn",
                                                  "torch", "joblib")) for name in names))

    def test_workflow_trusted_ref_native_token_minimal_events_and_concurrency(self):
        import yaml
        workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "review-plan.yml").read_text(encoding="utf-8"))
        trigger = workflow.get("on", workflow.get(True))
        self.assertEqual(set(trigger), {"issue_comment", "pull_request"})
        self.assertEqual(trigger["issue_comment"]["types"], ["created"])
        self.assertEqual(trigger["pull_request"]["types"], ["closed"])
        self.assertFalse(workflow["concurrency"]["cancel-in-progress"])
        self.assertIn("github.event.issue.number || github.event.pull_request.number",
                      workflow["concurrency"]["group"])
        job = workflow["jobs"]["review-plan"]
        self.assertEqual(job["permissions"],
                         {"contents": "write", "pull-requests": "write", "issues": "read"})
        checkout = job["steps"][0]
        self.assertEqual(checkout["with"]["ref"], "${{ github.event.repository.default_branch }}")
        self.assertFalse(checkout["with"]["persist-credentials"])
        execution = next(step for step in job["steps"] if step.get("run") == "python -B -m app.review")
        self.assertEqual(execution["env"]["GH_TOKEN"], "${{ github.token }}")
        self.assertEqual(job["steps"][-1]["with"]["if-no-files-found"], "error")
        self.assertIn("research-ai-target", job["steps"][-1]["with"]["name"])
        for step in job["steps"]:
            if "run" in step:
                self.assertNotIn("${{ github.event.comment", step["run"])
                self.assertNotIn("${{ github.event.pull_request", step["run"])


if __name__ == "__main__":
    unittest.main()
