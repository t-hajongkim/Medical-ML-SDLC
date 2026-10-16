"""Offline native-publication proofs; no data image, EDA, model, or live API."""

from copy import deepcopy
import contextlib
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import warnings
import zipfile

from app import executor_eda, research
from app.asset_contract import canonical_hash, write_json


REPO = "fixture-owner/workshop"
RUN = "12345678901"
CODE = "a" * 40
HEAD = "b" * 40
PUBLISHED_TREE = "c" * 40
CURRENT_TREE = "d" * 40
ACTOR = "fixture-maintainer"
DIRECTORY = "research/reports/issue-14"


def blob_oid(raw):
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


def archive_bytes(files):
    buffer = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in files:
                archive.writestr(name, content)
    return buffer.getvalue()


class EDACacheProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="eda-proof-", dir=research.ROOT)
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.folder, self.work = self.base / "report", self.base / "private"
        self.folder.mkdir()
        self.work.mkdir()
        self.session = {
            "schema_version": 2, "repository": REPO, "target_kind": "pull_request",
            "target_number": 43, "pull_request": 43, "issue_number": 14,
            "report_dir": DIRECTORY, "head_branch": "research/review-issue-14",
            "head_sha": HEAD, "default_branch": "main", "code_commit": CODE,
            "data_image": "ghcr.io/fixture-owner/data@sha256:" + "e" * 64,
            "model_image": "ghcr.io/fixture-owner/model@sha256:" + "f" * 64,
            "catalogs": {"eda": {}}, "policy": {},
        }
        self.prior_plan = {
            "code_commit": CODE, "data_image": self.session["data_image"],
            "model_image": self.session["model_image"], "input_signature": {"fixture": True},
            "calls": [{"tool_id": "schema"}, {"tool_id": "cohort"}], "plan_hash": "1" * 64,
        }
        self.prior = {
            "data_id": "fixture", "input_mapping_hash": "2" * 64,
            "execution_plan_hash": self.prior_plan["plan_hash"],
            "results": [{"block": "schema", "status": "pass", "details": {"count": 5}}],
            "provenance": {"workflow_run_url": f"https://github.com/{REPO}/actions/runs/{RUN}"},
        }
        self.prior["execution_result_hash"] = executor_eda.execution_result_hash(self.prior)
        values = {
            "eda.json": self.prior, "execution-plan.json": self.prior_plan,
            "request.json": {"repository": REPO, "issue_number": 14},
            "input-mapping.json": {"bindings": {"patient_id": "registered_patient"}},
            "input-schema.json": {"columns": ["registered_patient"]},
        }
        for name, value in values.items():
            write_json(self.folder / name, value)
        entries = [
            {"path": DIRECTORY + "/" + name, "mode": "100644", "type": "blob",
             "sha": blob_oid((self.folder / name).read_bytes())} for name in values]
        self.published_tree = {"sha": PUBLISHED_TREE, "truncated": False, "tree": deepcopy(entries)}
        self.current_tree = {"sha": CURRENT_TREE, "truncated": False, "tree": deepcopy(entries)}
        self.current_tree["tree"].append({
            "path": DIRECTORY + "/feedback-requests.json", "mode": "100644",
            "type": "blob", "sha": "9" * 40})
        self.current_commit = {"sha": HEAD, "tree": {"sha": CURRENT_TREE}}
        self.source = {
            "id": int(RUN), "run_attempt": 2,
            "path": ".github/workflows/research-plan.lock.yml", "event": "workflow_run",
            "repository": {"id": 77, "full_name": REPO},
            "head_repository": {"id": 77, "full_name": REPO},
            "head_branch": "main", "head_sha": CODE, "status": "completed", "conclusion": "success",
            "actor": {"login": ACTOR, "type": "User"},
            "triggering_actor": {"login": ACTOR, "type": "User"},
        }
        self.artifact = {
            "id": 19, "name": f"research-review-body-{RUN}-2", "expired": False,
            "workflow_run": {"id": int(RUN), "repository_id": 77, "head_repository_id": 77,
                             "head_branch": "main", "head_sha": CODE}}
        self.artifacts = [deepcopy(self.artifact)]
        self.pages = None
        self.permission = "write"
        self.receipt = {
            "schema_version": 2, "operation": "create", "repository": REPO,
            "target_kind": "issue", "target_number": 14, "issue_number": 14, "pull_request": None,
            "head_branch": self.session["head_branch"], "previous_head_sha": CODE,
            "publication_commit": "8" * 40, "expected_tree_sha": PUBLISHED_TREE,
            "recommendations_sha256": "3" * 64, "previous_body_sha256": "4" * 64,
            "body_sha256": "5" * 64, "body_refresh_required": False,
        }
        self.zip_payload = None
        self.download_result = 0
        self.api = Mock(side_effect=self.read_api)
        self.flow = SimpleNamespace(gh_json=self.api, validate_aggregate=Mock())
        self.transport = patch.object(subprocess, "run", side_effect=self.download).start()
        self.addCleanup(patch.stopall)

    def read_api(self, endpoint, *arguments):
        prefix = f"repos/{REPO}"
        if endpoint == f"{prefix}/actions/runs/{RUN}":
            return deepcopy(self.source)
        if endpoint == f"{prefix}/collaborators/{ACTOR}/permission":
            return {"permission": self.permission}
        if endpoint == f"{prefix}/actions/runs/{RUN}/artifacts?per_page=100":
            self.assertEqual(arguments, ("--paginate", "--slurp"))
            return deepcopy(self.pages if self.pages is not None else [{"artifacts": self.artifacts}])
        if endpoint == f"{prefix}/git/commits/{HEAD}":
            return deepcopy(self.current_commit)
        if endpoint == f"{prefix}/git/trees/{PUBLISHED_TREE}?recursive=1":
            return deepcopy(self.published_tree)
        if endpoint == f"{prefix}/git/trees/{CURRENT_TREE}?recursive=1":
            return deepcopy(self.current_tree)
        raise AssertionError("Unexpected proof API call: " + endpoint)

    def download(self, arguments, **kwargs):
        self.assertEqual(arguments, ["gh", "api", f"repos/{REPO}/actions/artifacts/19/zip"])
        self.assertEqual(kwargs, {"capture_output": True, "timeout": 120})
        data = self.zip_payload if self.zip_payload is not None else archive_bytes([
            ("publication.json", json.dumps(self.receipt)), ("pr-body.md", "Visible aggregate report")])
        return subprocess.CompletedProcess(arguments, self.download_result, stdout=data, stderr=b"PRIVATE")

    def authenticate(self):
        return research._authenticate_prior_eda(
            self.flow, self.folder, self.session, self.prior, self.prior_plan)

    def replace_current(self, name, value):
        write_json(self.folder / name, value)
        for entry in self.current_tree["tree"]:
            if entry["path"] == DIRECTORY + "/" + name:
                entry["sha"] = blob_oid((self.folder / name).read_bytes())

    def begin(self, execution=None):
        context = {
            "data_manifest": {"prepared_source": {}}, "_input_schema_request": {},
            "feature_columns": [], "input_contract": {"bindings": {}},
        }
        execution = Mock(return_value={"results": []}) if execution is None else execution
        patches = [
            patch.object(research.planning_eda, "_eda_ids", return_value=["schema"]),
            patch.object(research.planning_eda, "validate_execution_plan"),
            patch.object(research.planning_eda, "input_signature",
                         return_value=self.prior_plan["input_signature"]),
            patch.object(research.planning_eda, "build_execution_plan", return_value={"fixture": "plan"}),
            patch.object(research.planning_eda, "build_context", return_value={"context_hash": "6" * 64}),
            patch.object(research.executor_eda, "execute_plan", execution),
            patch.object(research, "_pending_feedback", return_value=([], [])),
            patch.object(research, "_cache_input_context"),
        ]
        with contextlib.ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            research._begin_basic_eda(self.flow, self.folder, self.work, self.session, context, {})
        return execution

    def test_native_creation_tree_authenticates_queue_only_head_changes(self):
        self.assertNotEqual(self.receipt["publication_commit"], self.session["head_sha"])
        self.assertTrue(self.authenticate())
        self.assertEqual(self.transport.call_count, 1)
        self.assertFalse(any("/runs?" in call.args[0] for call in self.api.call_args_list))
        self.assertEqual(sum("/permission" in call.args[0] for call in self.api.call_args_list), 1)

    def test_update_receipt_for_the_exact_same_pr_is_supported(self):
        self.receipt.update(operation="update", target_kind="pull_request", target_number=43,
                            pull_request=43, body_refresh_required=True, publication_commit=None)
        self.assertTrue(self.authenticate())

    def test_authenticated_begin_reuses_previous_calls_without_computing_blocks(self):
        execution = self.begin()
        self.assertEqual(self.session["eda_cache_status"], "authenticated_previous_pr_result")
        self.assertEqual(execution.call_args.kwargs["reuse"], self.prior)
        self.assertEqual(execution.call_args.kwargs["reuse_plan"], self.prior_plan)

    def test_forged_results_are_rejected_even_when_all_public_hashes_are_recomputed(self):
        self.prior["results"][0]["details"]["count"] = 9000
        self.prior["execution_result_hash"] = executor_eda.execution_result_hash(self.prior)
        self.replace_current("eda.json", self.prior)
        execute = Mock()
        with self.assertRaisesRegex(ValueError, "authenticated publication"):
            self.begin(execute)
        execute.assert_not_called()
        self.assertNotEqual(self.session["eda_cache_status"], "authenticated_previous_pr_result")

    def test_each_result_or_plan_input_blob_must_match_the_receipt_tree(self):
        for name in ("execution-plan.json", "request.json", "input-mapping.json", "input-schema.json"):
            original_file = (self.folder / name).read_bytes()
            original_tree = deepcopy(self.current_tree)
            with self.subTest(name=name):
                self.replace_current(name, {"edited": "and rehashed"})
                with self.assertRaisesRegex(ValueError, "authenticated publication"):
                    self.authenticate()
            (self.folder / name).write_bytes(original_file)
            self.current_tree = original_tree

    def test_local_cache_bytes_cannot_differ_from_the_authenticated_git_blob(self):
        write_json(self.folder / "eda.json", {"results": [], "execution_result_hash": "0" * 64})
        with self.assertRaisesRegex(ValueError, "Local prior EDA cache"):
            self.authenticate()

    def test_wrong_repository_url_or_unregistered_run_url_is_rejected_before_api(self):
        for url in (
            f"https://github.com/outsider/repo/actions/runs/{RUN}",
            f"https://github.com/{REPO}/actions/runs/01",
            f"https://github.com/{REPO}/actions/runs/{RUN}/attempts/2",
            f"https://github.com/{REPO}/actions/runs/{RUN}?spoof=1",
        ):
            with self.subTest(url=url):
                self.prior["provenance"]["workflow_run_url"] = url
                with self.assertRaises(ValueError):
                    self.authenticate()
        self.api.assert_not_called()

    def test_wrong_workflow_run_repository_branch_code_or_attempt_is_rejected(self):
        original = deepcopy(self.source)
        for change in (
            {"id": int(RUN) + 1}, {"path": ".github/workflows/prepare-eda-pr.yml"},
            {"event": "pull_request"}, {"status": "in_progress"}, {"conclusion": "failure"},
            {"head_branch": "research/review-issue-14"}, {"head_sha": "0" * 40},
            {"repository": {"id": 77, "full_name": "outsider/repo"}},
            {"head_repository": {"id": 99, "full_name": REPO}}, {"head_repository": None},
            {"run_attempt": 0}, {"run_attempt": True},
        ):
            with self.subTest(change=change):
                self.source = {**original, **change}
                with self.assertRaises(ValueError):
                    self.authenticate()
        self.transport.assert_not_called()

    def test_wrong_actor_or_rerun_actor_never_bypasses_permission_checks(self):
        original = deepcopy(self.source)
        for change in (
            {"actor": None}, {"actor": {"login": ACTOR, "type": "Bot"}},
            {"actor": {"login": "github-actions[bot]", "type": "User"}},
            {"triggering_actor": {"login": ACTOR, "type": "Bot"}},
            {"triggering_actor": {"login": "invalid\nactor", "type": "User"}},
        ):
            with self.subTest(change=change):
                self.source = {**original, **change}
                with self.assertRaises(ValueError):
                    self.authenticate()
        self.source = original
        self.permission = "read"
        with self.assertRaisesRegex(ValueError, "write permission"):
            self.authenticate()
        self.transport.assert_not_called()

    def test_wrong_artifact_run_sha_repository_or_branch_is_rejected(self):
        original = deepcopy(self.artifact["workflow_run"])
        for change in (
            {"id": int(RUN) + 1}, {"repository_id": 99}, {"head_repository_id": 99},
            {"head_sha": "0" * 40}, {"head_branch": "untrusted"},
        ):
            with self.subTest(change=change):
                self.artifacts[0]["workflow_run"] = {**original, **change}
                with self.assertRaises(ValueError):
                    self.authenticate()
        self.transport.assert_not_called()

    def test_expired_missing_and_old_attempt_proof_are_explicit_cache_misses(self):
        for artifacts, status in (
            ([], "cache_miss_missing_publication_proof"),
            ([{**self.artifact, "expired": True}], "cache_miss_expired_publication_proof"),
            ([{**self.artifact, "name": f"research-review-body-{RUN}-1"}],
             "cache_miss_missing_publication_proof"),
        ):
            with self.subTest(status=status):
                self.artifacts = artifacts
                self.assertFalse(self.authenticate())
                self.assertEqual(self.session["eda_cache_status"], status)
        self.transport.assert_not_called()

    def test_expired_proof_begin_explicitly_computes_fresh_instead_of_reusing(self):
        self.artifacts[0]["expired"] = True
        execution = self.begin()
        self.assertEqual(self.session["eda_cache_status"], "cache_miss_expired_publication_proof")
        self.assertIsNone(execution.call_args.kwargs["reuse"])
        self.assertIsNone(execution.call_args.kwargs["reuse_plan"])

    def test_old_reports_without_proof_or_changed_code_get_explicit_cache_misses(self):
        original = deepcopy(self.prior)
        self.prior.pop("provenance")
        self.assertFalse(self.authenticate())
        self.assertEqual(self.session["eda_cache_status"], "cache_miss_missing_publication_proof")
        self.prior = original
        self.session["code_commit"] = "0" * 40
        execution = self.begin()
        self.assertEqual(self.session["eda_cache_status"], "cache_miss_changed_input_or_code")
        self.assertIsNone(execution.call_args.kwargs["reuse"])
        self.api.assert_not_called()

    def test_ambiguous_or_malformed_artifact_lists_fail_closed(self):
        self.artifacts = [self.artifact, self.artifact]
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            self.authenticate()
        for pages in ({}, [{"artifacts": None}], [{"artifacts": [None]}], [None]):
            with self.subTest(pages=pages):
                self.pages = pages
                with self.assertRaises(ValueError):
                    self.authenticate()
        self.transport.assert_not_called()

    def test_receipt_cannot_spoof_repository_target_kind_number_or_schema(self):
        original = deepcopy(self.receipt)
        for change in (
            {"schema_version": True}, {"schema_version": 1}, {"repository": "outsider/repo"},
            {"issue_number": 15}, {"target_number": 15}, {"target_number": True},
            {"target_kind": "pull_request"}, {"pull_request": 43},
            {"head_branch": "research/review-issue-15"}, {"expected_tree_sha": "main"},
            {"publication_commit": None}, {"body_refresh_required": True},
        ):
            with self.subTest(change=change):
                self.receipt = {**original, **change}
                with self.assertRaises(ValueError):
                    self.authenticate()
        self.receipt = {**original, "operation": "update", "target_kind": "pull_request",
                        "pull_request": 44, "target_number": 44}
        with self.assertRaises(ValueError):
            self.authenticate()

    def test_bounded_zip_rejects_extra_paths_duplicate_members_symlinks_and_oversized_files(self):
        receipt = json.dumps(self.receipt)
        link = zipfile.ZipInfo("pr-body.md")
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        for data in (
            b"not a zip",
            archive_bytes([("../publication.json", receipt), ("pr-body.md", "body")]),
            archive_bytes([("publication.json", receipt)]),
            archive_bytes([("publication.json", receipt), ("pr-body.md", "body"), ("rows.csv", "PRIVATE")]),
            archive_bytes([("publication.json", receipt), ("publication.json", receipt)]),
            archive_bytes([("publication.json", "x" * 65537), ("pr-body.md", "body")]),
            archive_bytes([("publication.json", receipt), ("pr-body.md", "x" * 524289)]),
            archive_bytes([("publication.json", receipt), (link, "not extracted")]),
            b"x" * 1_000_001,
        ):
            with self.subTest(size=len(data)):
                self.zip_payload = data
                with self.assertRaises((ValueError, RuntimeError)):
                    self.authenticate()
        self.assertFalse((self.base / "rows.csv").exists())

    def test_invalid_or_duplicate_receipt_json_fails_and_download_errors_do_not_leak_stderr(self):
        for content in ('{"schema_version":2,"schema_version":2}', "[]", "not json"):
            with self.subTest(content=content):
                self.zip_payload = archive_bytes([("publication.json", content), ("pr-body.md", "body")])
                with self.assertRaises(ValueError):
                    self.authenticate()
        self.zip_payload = None
        self.download_result = 1
        with self.assertRaises(RuntimeError) as caught:
            self.authenticate()
        self.assertNotIn("PRIVATE", str(caught.exception))
        self.transport.side_effect = subprocess.TimeoutExpired("gh", 120)
        with self.assertRaises(RuntimeError):
            self.authenticate()

    def test_truncated_ambiguous_or_nonregular_git_trees_are_not_proof(self):
        original = deepcopy(self.current_tree)
        for change in (
            {"sha": "0" * 40}, {"truncated": True},
            {"tree": original["tree"] + [original["tree"][0]]},
            {"tree": [{**entry, "mode": "120000"} for entry in original["tree"]]},
        ):
            with self.subTest(change=change):
                self.current_tree = {**original, **change}
                with self.assertRaises(ValueError):
                    self.authenticate()

    def test_corrupt_same_job_private_context_still_fails_instead_of_becoming_a_cache_miss(self):
        manifest = {"fixture": "manifest"}
        trusted = {"fixture": "private validated context"}
        self.session.update(validated_manifest_hash=canonical_hash(manifest),
                            validated_context_hash=canonical_hash(trusted))
        write_json(self.work / "VALIDATED_CONTEXT.json", {**trusted, "changed": True})
        with patch("app.input_contracts.read_manifest", return_value=manifest), \
                patch("app.asset_contract.verify_files"), \
                patch("app.input_contracts._feature_rows") as rows:
            with self.assertRaisesRegex(ValueError, "입력 문맥"):
                research._context_for_session(self.work, self.session)
            rows.assert_not_called()
        self.assertNotIn("eda_cache_status", self.session)


if __name__ == "__main__":
    unittest.main()
