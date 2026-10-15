import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app import cloud_recommendations as cloud
from app import research


class CloudContractTests(unittest.TestCase):
    def test_runtime_rejects_local_execution(self):
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}):
            with self.assertRaisesRegex(ValueError, "restricted"):
                cloud._runtime()

    def test_pull_requires_generated_same_repository_branch(self):
        repository = "fixture-owner/fixture-workshop"
        pull = {
            "number": 7, "state": "open", "title": "연구검토 #2: fixture",
            "head": {"ref": "research/review-issue-2", "sha": "a" * 40,
                     "repo": {"full_name": repository}},
            "base": {"ref": "main"},
        }
        flow = SimpleNamespace(gh_json=Mock(side_effect=[pull, {"default_branch": "main"}]))
        actual, branch, issue, default = cloud._pull(flow, repository, 7)
        self.assertEqual((actual, branch, issue, default),
                         (pull, "research/review-issue-2", 2, "main"))
        for change in (
            {"state": "closed"},
            {"head": {"ref": "other", "repo": {"full_name": repository}}},
            {"head": {"ref": "research/review-issue-2", "repo": {"full_name": "other/repo"}}},
            {"title": "Unrelated PR"},
        ):
            flow = SimpleNamespace(gh_json=Mock(return_value={**pull, **change}))
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    cloud._pull(flow, repository, 7)

    def test_branch_inputs_must_be_regular_tracked_files(self):
        flow = SimpleNamespace(command=Mock(return_value=SimpleNamespace(
            stdout="120000 blob " + "a" * 40 + "\tresearch/reports/issue-2/selection.json\n")))
        with self.assertRaisesRegex(ValueError, "regular"):
            cloud._read_branch_file(flow, "b" * 40, "research/reports/issue-2/selection.json")

    def test_optional_missing_ai_file_is_not_a_completed_recommendation(self):
        flow = SimpleNamespace(command=Mock(return_value=SimpleNamespace(stdout="")))
        self.assertIsNone(cloud._read_branch_file(flow, "b" * 40, "fixture.json", optional=True))
        with self.assertRaisesRegex(ValueError, "missing"):
            cloud._read_branch_file(flow, "b" * 40, "fixture.json")


class PublicationGitFixtureTests(unittest.TestCase):
    """Exercise local Git objects only, never the project checkout or a remote."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="cloud-publication-fixture-"))
        self.addCleanup(self.clean)
        self.root = self.folder / "checkout"
        self.root.mkdir()
        self.work = self.folder / "work"
        self.work.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.autocrlf", "false")
        (self.root / "never-execute.py").write_text("TRUSTED_DEFAULT = True\n", encoding="utf-8")
        self.git("add", "--", "never-execute.py")
        self.git("commit", "-q", "-m", "Synthetic default fixture")
        self.default_sha = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "-b", "research/review-issue-2")
        (self.root / "never-execute.py").write_text(
            'raise RuntimeError("PR source must never be executed by this test")\n', encoding="utf-8")
        self.directory = "research/reports/issue-2"
        self.report = self.root / self.directory
        self.report.mkdir(parents=True)
        self.original = {}
        for name in cloud.INPUT_FILES:
            value = json.dumps({"fixture": name, "version": "original"}) + "\n"
            self.original[name] = value
            (self.report / name).write_text(value, encoding="utf-8")
        self.git("add", "--", ".")
        self.git("commit", "-q", "-m", "Synthetic review head")
        self.head_sha = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "main")
        self.report.mkdir(parents=True)
        for name, value in self.original.items():
            (self.report / name).write_text(value, encoding="utf-8")
        self.session = {
            "head_sha": self.head_sha, "issue_number": 2, "report_dir": self.directory,
            "allowed_outputs": [self.directory + "/" + name for name in cloud.OUTPUT_FILES],
        }

    def clean(self):
        def writable(function, path, error):
            os.chmod(path, stat.S_IWRITE)
            function(path)
        shutil.rmtree(self.folder, onerror=writable)

    def git(self, *args):
        result = subprocess.run(["git", *args], cwd=self.root, capture_output=True,
                                text=True, encoding="utf-8", timeout=30)
        if result.returncode:
            raise AssertionError(result.stderr)
        return result.stdout.strip()

    def test_publication_parent_is_pr_head_and_only_generated_files_change(self):
        for name in cloud.OUTPUT_FILES:
            (self.report / name).write_text(
                json.dumps({"fixture": name, "version": "new"}) + "\n", encoding="utf-8")
        flow = SimpleNamespace(command=research.command)
        commit = cloud._git_tree_commit(flow, self.root, self.work, self.session)
        self.assertEqual(self.git("rev-parse", commit + "^"), self.head_sha)
        changed = set(self.git("diff-tree", "--no-commit-id", "--name-only", "-r", commit).splitlines())
        self.assertEqual(changed, set(self.session["allowed_outputs"]))
        for name in ("selection.json", "request.json"):
            content = self.git("show", commit + ":" + self.directory + "/" + name)
            self.assertEqual(content, self.original[name].strip())
        self.assertIn("PR source must never be executed",
                      self.git("show", commit + ":never-execute.py"))
        self.assertEqual(self.git("rev-parse", "HEAD"), commit)

    def test_unchanged_outputs_do_not_create_commit_or_switch_trusted_head(self):
        flow = SimpleNamespace(command=research.command)
        self.assertIsNone(cloud._git_tree_commit(flow, self.root, self.work, self.session))
        self.assertEqual(self.git("rev-parse", "HEAD"), self.default_sha)

    def test_prepare_and_apply_use_real_composition_with_mock_cloud_inputs(self):
        from app.asset_contract import read_json, write_json
        from test_research_workflow import context_and_source, issue
        repository = "fixture-owner/fixture-workshop"
        image = "ghcr.io/fixture-owner/fixture-db@sha256:" + "a" * 64
        context, _ = context_and_source()
        original = issue({"research_goal": "PRIVATE_SYNTHETIC_NOTE"})
        original["number"] = 2
        request = research.parse_issue(original, repository)
        aggregate = research.report(context)
        provenance = {
            "data_image": image, "code_commit": "b" * 40,
            "workflow_run_url": f"https://github.com/{repository}/actions/runs/12",
            "data_access": "Synthetic controller fixture",
        }
        shutil.rmtree(self.report)
        self.git("checkout", "-q", "research/review-issue-2")
        for name in cloud.INPUT_FILES:
            (self.report / name).unlink(missing_ok=True)
        research.write_payload(self.root, request, aggregate, provenance, context=context)
        selection = read_json(self.report / "selection.json")
        selection["eda"]["add"] = ["preprocessing_lineage"]
        selection["evaluation"].update(
            add=["f1"], primary_metric="average_precision", threshold=0.3)
        write_json(self.report / "selection.json", selection)
        selected_bytes = (self.report / "selection.json").read_bytes()
        self.git("add", "--", self.directory)
        self.git("commit", "-q", "-m", "Synthetic composable review input")
        head = self.git("rev-parse", "HEAD")
        prior_body = research.merge_review_body(
            "Human reviewer note must remain.", (self.report / "report.md").read_text(encoding="utf-8"))
        self.git("checkout", "-q", "main")
        (self.root / "config").mkdir()
        write_json(self.root / "config" / "assets.json",
                   {"schema_version": 1, "data_image": image})
        self.git("add", "--", "config/assets.json")
        self.git("commit", "-q", "-m", "Synthetic trusted asset reference")
        remote = self.folder / "remote.git"
        result = subprocess.run(
            ["git", "clone", "-q", "--bare", str(self.root), str(remote)],
            capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.git("remote", "add", "origin", str(remote))
        pull = {
            "number": 7, "state": "open", "title": "연구검토 #2: fixture", "body": prior_body,
            "head": {"sha": head, "ref": "research/review-issue-2",
                     "repo": {"full_name": repository}}, "base": {"ref": "main"},
        }
        def api(path):
            return pull if path.endswith("/pulls/7") else {"default_branch": "main"}
        work = self.folder / "controller"
        with patch.object(cloud, "ROOT", self.root), \
                patch.object(cloud, "_runtime", return_value=(research, repository)), \
                patch.object(research, "gh_json", side_effect=api), \
                patch.object(research, "image_assets", side_effect=lambda ref, path: path.mkdir()), \
                patch.object(cloud, "load_context", return_value=context), \
                patch.dict(os.environ, {"GITHUB_RUN_ID": "38722808263"}):
            prepared = cloud.prepare_ai(7, work)
            ai_request = read_json(work / "AI_REQUEST.json")
            self.assertNotIn("PRIVATE_SYNTHETIC_NOTE", json.dumps(ai_request))
            evidence = ai_request["allowed_evidence_ids"][0]
            recommendation = {
                "schema_version": 1, "provider": "github-copilot-cloud-agent",
                "status": "provided", "context_hash": prepared["context_hash"],
                "eda": [{"id": "prediction_timing", "reason": "합성 AI 응답: 시점 근거 확인",
                         "evidence": [evidence]}],
                "evaluation": [{"id": "precision", "reason": "합성 AI 응답: 운영 판정 검토",
                                "evidence": [evidence]}],
            }
            write_json(self.report / "ai-recommendations.json", recommendation)
            outcome = cloud.apply_ai(7, work)
        self.assertEqual(outcome["status"], "validated_copilot_recommendations")
        self.assertFalse(outcome["remote_publication_performed"])
        self.assertEqual(self.git("rev-parse", outcome["publication_commit"] + "^"), head)
        self.assertEqual((self.report / "selection.json").read_bytes(), selected_bytes)
        eda_plan = read_json(self.report / "eda-plan.json")
        metric_plan = read_json(self.report / "evaluation-plan.json")
        self.assertIn("prediction_timing", eda_plan["selected_ids"])
        self.assertIn("preprocessing_lineage", eda_plan["selected_ids"])
        self.assertEqual(metric_plan["primary_metric"], "average_precision")
        self.assertIn("f1", metric_plan["selected_ids"])
        self.assertIn("precision", metric_plan["selected_ids"])
        self.assertEqual(metric_plan["ai_status"], "provided")
        self.assertIn("Human reviewer note must remain.",
                      (work / "pr-body.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
