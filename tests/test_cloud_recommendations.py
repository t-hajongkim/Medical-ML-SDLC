import json
import hashlib
import contextlib
import io
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
    def workflow_config(self):
        import yaml
        root = Path(__file__).resolve().parents[1]
        text = (root / ".github" / "workflows" / "recommend-blocks.md").read_text(encoding="utf-8")
        return yaml.load(text.split("---", 2)[1], Loader=yaml.BaseLoader)

    def test_publication_retry_returns_verified_flags_without_running_application(self):
        from app.asset_contract import write_json
        config = self.workflow_config()
        script = config["mcp-scripts"]["apply-recommendations"]["run"].split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
        with tempfile.TemporaryDirectory(prefix="publication-retry-") as temporary:
            folder = Path(temporary)
            work, root = folder / "work", folder / "checkout"
            work.mkdir()
            root.mkdir()
            branch = "research/review-issue-2"
            head, commit, tree = "b" * 40, "a" * 40, "c" * 40
            repo, directory = "fixture-owner/workshop", "research/reports/issue-2"
            payload = {
                "schema_version": 1, "provider": "github-copilot-cloud-agent",
                "status": "provided", "context_hash": "d" * 64, "eda": [], "evaluation": []}
            session = {
                "repository": repo, "pull_request": 7, "issue_number": 2,
                "report_dir": directory, "head_branch": branch, "head_sha": head,
                "code_commit": "e" * 40, "allowed_outputs": [directory + "/report.md"]}
            write_json(work / "session.json", session)
            write_json(work / "AI_REQUEST.json", {"context_hash": payload["context_hash"]})
            body = b"Synthetic generated review"
            (work / "pr-body.md").write_bytes(body)
            meta = {
                "schema_version": 1, "repository": repo, "pull_request": 7, "head_branch": branch,
                "previous_head_sha": head, "publication_commit": commit,
                "expected_tree_sha": tree, "body_refresh_required": True,
                "body_sha256": hashlib.sha256(body).hexdigest(),
                "previous_body_sha256": hashlib.sha256(b"Human note").hexdigest(),
                "recommendations_sha256": hashlib.sha256(json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
            write_json(work / "publication.json", meta)
            current = {"state": "open", "body": "Human note",
                       "head": {"sha": head, "ref": branch, "repo": {"full_name": repo}}}
            git_results = {
                ("rev-parse", "HEAD"): commit, ("rev-parse", "HEAD^{tree}"): tree,
                ("rev-parse", commit + "^"): head,
                ("symbolic-ref", "--short", "HEAD"): branch,
                ("diff", "--name-only", head, commit, "--"): directory + "/report.md"}
            def cli(arguments, **kwargs):
                if arguments[0] == "git":
                    return SimpleNamespace(stdout=git_results[tuple(arguments[1:])])
                if arguments[:2] == ["gh", "api"]:
                    return SimpleNamespace(stdout=json.dumps(current))
                raise AssertionError("A retry must not execute application code or EDA")
            program = script.replace(
                'Path("/tmp/gh-aw/research-recommendation")', f"Path({str(work)!r})")
            def execute(value):
                output = io.StringIO()
                with patch.dict(os.environ, {
                    "GITHUB_WORKSPACE": str(root), "GITHUB_REPOSITORY": repo,
                    "REQUESTED_PR": "7", "INPUT_RECOMMENDATIONS": json.dumps(value, indent=2)}), \
                        patch("subprocess.run", side_effect=cli), contextlib.redirect_stdout(output):
                    with self.assertRaises(SystemExit) as stopped:
                        exec(compile(program, "<trusted-mcp-retry>", "exec"), {})
                return stopped.exception.code, output.getvalue()
            code, output = execute(payload)
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output), {
                "status": "validated_copilot_recommendations", "pull_request": 7,
                "publication_ready": True, "push_required": True, "body_refresh_required": True})
            self.assertNotEqual(execute({**payload, "eda": [{"id": "schema"}]})[0], 0)
            for change in ({"repository": "other/repo"}, {"expected_tree_sha": "f" * 40},
                           {"body_sha256": "f" * 64}):
                with self.subTest(change=change):
                    write_json(work / "publication.json", {**meta, **change})
                    self.assertNotEqual(execute(payload)[0], 0)
            write_json(work / "publication.json", meta)
            current["head"]["sha"] = "f" * 40
            self.assertNotEqual(execute(payload)[0], 0)
            (work / "publication.json").unlink()
            (work / "application.lock").mkdir()
            self.assertIn("already running", execute(payload)[0])

    def test_incomplete_or_unrequested_publication_fails_post_step(self):
        from app.asset_contract import write_json
        config = self.workflow_config()
        script = config["post-steps"][-1]["run"].split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
        with tempfile.TemporaryDirectory(prefix="publication-audit-") as temporary:
            folder = Path(temporary)
            meta_path, output_path = folder / "publication.json", folder / "agent.json"
            write_json(meta_path, {
                "publication_commit": "a" * 40, "body_refresh_required": True,
                "pull_request": 7, "repository": "fixture-owner/workshop",
                "head_branch": "research/review-issue-2"})
            program = script.replace(
                'Path("/tmp/gh-aw/research-recommendation/publication.json")', f"Path({str(meta_path)!r})")
            push = {"type": "push_to_pull_request_branch", "branch": "research/review-issue-2",
                    "pull_request_number": "7"}
            refresh = {"type": "refresh_review_body", "confirmed": True, "message": "합성 변경 안내"}
            with patch.dict(os.environ, {"GH_AW_AGENT_OUTPUT": str(output_path)}):
                for items in ([{"type": "report_incomplete"}], [], [push], [refresh],
                              [{**push, "branch": "other"}, refresh],
                              [{**push, "pull_request_number": "8"}, refresh],
                              [{**push, "pull_request_number": "07"}, refresh],
                              [{**push, "pull_request_number": 7.0}, refresh],
                              [{**push, "pull_request_number": True}, refresh]):
                    with self.subTest(items=items):
                        write_json(output_path, {"items": items})
                        with self.assertRaises(SystemExit):
                            exec(compile(program, "<publication-audit>", "exec"), {})
                write_json(output_path, {"items": [push, refresh]})
                exec(compile(program, "<publication-audit>", "exec"), {})
                write_json(output_path, {"items": [{**push, "pull_request_number": 7}, refresh]})
                exec(compile(program, "<publication-audit>", "exec"), {})

    def test_completed_workflow_triggers_keep_default_human_roles(self):
        import yaml
        root = Path(__file__).resolve().parents[1]
        text = (root / ".github" / "workflows" / "recommend-blocks.md").read_text(encoding="utf-8")
        frontmatter = text.split("---", 2)[1]
        config = yaml.load(frontmatter, Loader=yaml.BaseLoader)
        trigger = config["on"]
        self.assertNotIn("bots", trigger)
        self.assertNotIn("roles", trigger)
        self.assertIn("workflow_dispatch", trigger)
        self.assertEqual(trigger["workflow_run"]["workflows"],
                         ["Prepare research review", "Review selections and freeze approved plan"])
        self.assertEqual(trigger["workflow_run"]["types"], ["completed"])
        self.assertNotIn("pull_request_target", trigger)
        self.assertNotIn("repository_dispatch", trigger)
        self.assertEqual(config["checkout"]["ref"], "${{ github.event.repository.default_branch }}")
        self.assertEqual(config["permissions"]["contents"], "read")

    def test_compiled_workflow_keeps_default_roles_and_exact_target_dependencies(self):
        import yaml
        root = Path(__file__).resolve().parents[1]
        path = root / ".github" / "workflows" / "recommend-blocks.lock.yml"
        config = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        pre = config["jobs"]["pre_activation"]
        membership = next(step for step in pre["steps"] if step.get("id") == "check_membership")
        self.assertNotIn("GH_AW_ALLOWED_BOTS", membership["env"])
        self.assertEqual(membership["env"]["GH_AW_REQUIRED_ROLES"], "admin,maintainer,write")
        self.assertIn("is_team_member", pre["outputs"]["activated"])
        self.assertIn("resolve_review", config["jobs"]["agent"]["needs"])
        self.assertIn("resolve_review", config["jobs"]["safe_outputs"]["needs"])
        self.assertIn("needs.resolve_review.outputs.ready", config["jobs"]["activation"]["if"])
        self.assertNotIn("resolve_review", config["jobs"]["refresh_review_body"]["needs"])

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

    def test_ai_comment_is_posted_only_after_verified_push_and_body_update(self):
        import yaml
        root = Path(__file__).resolve().parents[1]
        text = (root / ".github" / "workflows" / "recommend-blocks.md").read_text(encoding="utf-8")
        config = yaml.load(text.split("---", 2)[1], Loader=yaml.BaseLoader)
        self.assertNotIn("add-comment", config["safe-outputs"])
        job = config["safe-outputs"]["jobs"]["refresh-review-body"]
        script = job["steps"][-1]["with"]["script"]
        self.assertEqual(job["inputs"]["message"]["required"], "true")
        harness = r"""
const fs = require("node:fs"), path = require("node:path"), crypto = require("node:crypto");
const {script, folder} = JSON.parse(fs.readFileSync(0, "utf8"));
const hash = value => crypto.createHash("sha256").update(value, "utf8").digest("hex");
const artifact = path.join(folder, "research-review-body"); fs.mkdirSync(artifact);
const output = path.join(folder, "output.json");
process.env.RUNNER_TEMP = folder; process.env.GH_AW_AGENT_OUTPUT = output;
process.env.GITHUB_REPOSITORY = "fixture-owner/workshop";
const body = "Preserved human note\nSynthetic report";
fs.writeFileSync(path.join(artifact, "pr-body.md"), body);
const meta = {
  schema_version: 1, repository: process.env.GITHUB_REPOSITORY, pull_request: 7,
  head_branch: "research/review-issue-2", publication_commit: "a".repeat(40),
  previous_head_sha: "b".repeat(40), expected_tree_sha: "c".repeat(40),
  body_refresh_required: true, body_sha256: hash(body), previous_body_sha256: hash("Human note")
};
fs.writeFileSync(path.join(artifact, "publication.json"), JSON.stringify(meta));
const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
const execute = new AsyncFunction("require", "github", "context", "core", script);
(async()=>{
  const results = [];
  for (const kind of ["success", "unpublished", "body_failure", "bad_summary"]) {
    const writes = [];
    fs.writeFileSync(output, JSON.stringify({items: [
      {type: "push_to_pull_request_branch"},
      {type: "refresh_review_body", confirmed: true,
       message: kind === "bad_summary" ? "" : "합성 변경 안내: F1·AP·threshold 0.3을 반영한 계획 검토를 기다립니다."}
    ]}));
    const github = {rest: {
      pulls: {
        get: async()=>({data: {state:"open", title:"연구검토 #2", body:"Human note",
          head: {repo: {full_name:meta.repository}, ref:meta.head_branch, sha:meta.publication_commit}}}),
        update: async()=>{writes.push("body"); if(kind==="body_failure")throw Error("synthetic failure");}
      },
      git: {getCommit: async()=>({data:{
        tree: {sha:kind==="unpublished" ? "d".repeat(40) : meta.expected_tree_sha},
        parents:[{sha:meta.previous_head_sha}]}})},
      issues: {createComment: async args=>{
        if(args.issue_number!==7 || !args.body.includes("사람의 승인 대기"))throw Error("wrong comment");
        writes.push("comment");
      }}
    }};
    let failed = false;
    try { await execute(require, github, {repo:{owner:"fixture-owner",repo:"workshop"}}, {info:()=>{}}); }
    catch { failed = true; }
    results.push({kind, failed, writes});
  }
  console.log(JSON.stringify(results));
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        with tempfile.TemporaryDirectory(prefix="publication-notice-") as folder:
            result = subprocess.run(["node", "-e", harness], input=json.dumps({
                "script": script, "folder": folder}), capture_output=True,
                text=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [
            {"kind": "success", "failed": False, "writes": ["body", "comment"]},
            {"kind": "unpublished", "failed": True, "writes": []},
            {"kind": "body_failure", "failed": True, "writes": ["body"]},
            {"kind": "bad_summary", "failed": True, "writes": []},
        ])


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
        self.run_controller()

    def test_authorized_natural_feedback_changes_same_pr_and_publishes_audit(self):
        self.run_controller(feedback=True)

    def run_controller(self, *, feedback=False):
        from app.asset_contract import read_json, write_json
        from test_research_workflow import context_and_source, issue
        repository = "fixture-owner/fixture-workshop"
        image = "ghcr.io/fixture-owner/fixture-db@sha256:" + "a" * 64
        context, _ = context_and_source()
        original = issue({"research_goal": "PUBLIC_SYNTHETIC_GOAL"})
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
        body = "합성 피드백: F1을 추가하고 AP를 주지표로, threshold를 0.3으로 설정해 주세요."
        if feedback:
            selection["evaluation"].update(add=["recall"], primary_metric="roc_auc", threshold=0.7)
            write_json(self.report / "feedback-requests.json", {
                "schema_version": 1, "kind": "pr1-natural-feedback", "repository": repository,
                "pull_request": 7, "issue_number": 2, "comments": [{
                    "comment_id": 17, "actor": "fixture-owner",
                    "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest()}],
            })
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
        self.report.mkdir(parents=True, exist_ok=True)
        write_json(self.report / "feedback-requests.json", {"stale_default_branch_record": True})
        self.git("add", "--", "config/assets.json", self.directory)
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
            if path.endswith("/issues/comments/17"):
                return {"id": 17, "body": body, "user": {"type": "User", "login": "fixture-owner"},
                        "issue_url": f"https://api.github.com/repos/{repository}/issues/7"}
            if path.endswith("/collaborators/fixture-owner/permission"):
                return {"permission": "write"}
            return pull if path.endswith("/pulls/7") else {"default_branch": "main"}
        work = self.folder / "controller"
        def image_assets(ref, path):
            path.mkdir()
            write_json(path / "data_manifest.json", {
                "feature_columns": context["feature_columns"], "data_id": aggregate["data_id"]})
        with patch.object(cloud, "ROOT", self.root), \
                patch.object(cloud, "_runtime", return_value=(research, repository)), \
                patch.object(research, "gh_json", side_effect=api), \
                patch.object(research, "image_assets", side_effect=image_assets), \
                patch.object(cloud, "report", wraps=cloud.report) as measured_report, \
                patch.object(cloud, "load_context", return_value=context), \
                patch.dict(os.environ, {"GITHUB_RUN_ID": "38722808263"}):
            prepared = cloud.prepare_ai(7, work)
            self.assertEqual(prepared["phase"], "coordination")
            self.assertFalse((work / "AI_REQUEST.json").exists())
            coordination = read_json(work / "RESEARCH_CONTEXT.json")
            self.assertEqual(coordination["request"]["research_goal"], "PUBLIC_SYNTHETIC_GOAL")
            write_json(work / "coordination-proposal.json", {
                "schema_version": 1, "context_hash": coordination["context_hash"],
                "eda_add": ["prediction_timing"],
                "objectives": {"eda": [], "evaluation": []},
                "feedback_ids": [17] if feedback else [],
                "selection_updates": {"evaluation": {
                    "add": ["f1"], "primary_metric": "average_precision", "threshold": 0.3,
                }} if feedback else {},
                "reason": "공개 연구 목적과 스키마를 확인하고 예측 시점 점검을 추가합니다.",
            })
            coordinated = cloud.run_data_checks(7, work)
            ai_request = read_json(work / "AI_REQUEST.json")
            self.assertIn("PUBLIC_SYNTHETIC_GOAL", json.dumps(ai_request))
            self.assertNotIn("metadata", ai_request["research_context"]["schema_request"])
            evidence = ai_request["allowed_evidence_ids"][0]
            recommendation = {
                "schema_version": 1, "provider": "github-copilot-cloud-agent",
                "status": "provided", "context_hash": coordinated["context_hash"],
                "eda": [{"id": "prediction_timing", "reason": "합성 AI 응답: 시점 근거 확인",
                         "evidence": [evidence]}],
                "evaluation": [{"id": "precision", "reason": "합성 AI 응답: 운영 판정 검토",
                                "evidence": [evidence]}],
            }
            write_json(self.report / "ai-recommendations.json", recommendation)
            original_request = read_json(work / "AI_REQUEST.json")
            changed_request = {**original_request, "eda_results": {
                **original_request["eda_results"], "assessment_status": "passed"}}
            write_json(work / "AI_REQUEST.json", changed_request)
            with self.assertRaisesRegex(ValueError, "EDA 집계"):
                cloud.apply_ai(7, work)
            write_json(work / "AI_REQUEST.json", original_request)
            outcome = cloud.apply_ai(7, work)
            self.assertEqual(measured_report.call_count, 1, "Validated EDA must not be rescanned during application")
        self.assertEqual(outcome["status"], "validated_copilot_recommendations")
        self.assertFalse(outcome["remote_publication_performed"])
        self.assertEqual(self.git("rev-parse", outcome["publication_commit"] + "^"), head)
        if feedback:
            self.assertNotEqual((self.report / "selection.json").read_bytes(), selected_bytes)
            self.assertIn(self.directory + "/selection.json", outcome["allowed_outputs"])
            self.assertEqual(read_json(self.report / "feedback-applied.json")["comment_ids"], [17])
            record = read_json(self.report / "coordination-plan.json")["record"]
            self.assertEqual(record["feedback_ids"], [17])
            self.assertNotIn(body, json.dumps(record, ensure_ascii=False))
            self.assertIn("recall", read_json(self.report / "selection.json")["evaluation"]["add"])
        else:
            self.assertEqual((self.report / "selection.json").read_bytes(), selected_bytes)
            self.assertNotIn(self.directory + "/selection.json", outcome["allowed_outputs"])
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


class FeedbackContextTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="feedback-contract-")
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name) / "issue-2"
        self.folder.mkdir()
        self.repo = "fixture-owner/workshop"
        self.body = "합성 피드백: F1을 추가해 주세요."
        self.comment = {
            "id": 17, "body": self.body, "user": {"type": "User", "login": "writer"},
            "issue_url": f"https://api.github.com/repos/{self.repo}/issues/7"}
        from app.asset_contract import write_json
        self.write = write_json
        self.ledger = {
            "schema_version": 1, "kind": "pr1-natural-feedback", "repository": self.repo,
            "pull_request": 7, "issue_number": 2, "comments": [{
                "comment_id": 17, "actor": "writer",
                "body_sha256": hashlib.sha256(self.body.encode("utf-8")).hexdigest()}]}
        self.write(self.folder / "feedback-requests.json", self.ledger)
        self.flow = SimpleNamespace(issue_number=research.issue_number, gh_json=Mock(
            side_effect=lambda path: self.comment if "/issues/comments/" in path else {"permission": "write"}))

    def test_current_authorized_body_is_returned_only_transiently(self):
        pending, applied = cloud._pending_feedback(self.flow, self.repo, 7, self.folder)
        self.assertEqual(pending[0]["body"], self.body)
        self.assertEqual(applied, [])
        self.assertNotIn(self.body, (self.folder / "feedback-requests.json").read_text(encoding="utf-8"))

    def test_changed_body_author_pr_bot_or_revoked_role_stops_ai(self):
        cases = (
            {"body": "편집된 합성 피드백"},
            {"user": {"type": "User", "login": "other"}},
            {"issue_url": f"https://api.github.com/repos/{self.repo}/issues/8"},
            {"user": {"type": "Bot", "login": "writer"}},
        )
        original = dict(self.comment)
        for change in cases:
            with self.subTest(change=change):
                self.comment = {**original, **change}
                with self.assertRaises(ValueError):
                    cloud._pending_feedback(self.flow, self.repo, 7, self.folder)
        self.comment = original
        self.flow.gh_json.side_effect = lambda path: self.comment if "/issues/comments/" in path else {"permission": "read"}
        with self.assertRaisesRegex(ValueError, "write"):
            cloud._pending_feedback(self.flow, self.repo, 7, self.folder)

    def test_duplicate_or_cross_repository_ledger_is_rejected(self):
        for changed in (
            {**self.ledger, "comments": self.ledger["comments"] * 2},
            {**self.ledger, "repository": "other/workshop"},
            {**self.ledger, "pull_request": 8},
            {**self.ledger, "issue_number": 3},
            {**self.ledger, "kind": "untrusted"},
        ):
            with self.subTest(changed=changed):
                self.write(self.folder / "feedback-requests.json", changed)
                with self.assertRaises(ValueError):
                    cloud._pending_feedback(self.flow, self.repo, 7, self.folder)

    def test_applied_metadata_requires_matching_origin_and_known_ids(self):
        for changed in (
            {"repository": "other/workshop"}, {"pull_request": 8},
            {"comment_ids": [18]}, {"comment_ids": [17, 17]},
            {"comment_ids": [True]}, {"coordination_hash": "invalid"},
        ):
            with self.subTest(changed=changed):
                self.write(self.folder / "feedback-applied.json", {
                    "schema_version": 1, "repository": self.repo, "pull_request": 7,
                    "comment_ids": [17], "coordination_hash": "f" * 64, **changed})
                with self.assertRaises(ValueError):
                    cloud._pending_feedback(self.flow, self.repo, 7, self.folder)

    def test_final_publication_rechecks_feedback_even_after_local_mark_applied(self):
        from app.asset_contract import canonical_hash
        request = {"public_request": "synthetic"}
        self.write(self.folder / "request.json", request)
        pending, _ = cloud._pending_feedback(self.flow, self.repo, 7, self.folder)
        work = Path(self.temporary.name)
        self.write(work / "RESEARCH_CONTEXT.json", {"feedback": pending})
        session = {"request_hash": canonical_hash(request), "applied_feedback_ids": []}
        self.write(self.folder / "feedback-applied.json", {
            "schema_version": 1, "repository": self.repo, "pull_request": 7,
            "comment_ids": [17], "coordination_hash": "f" * 64})
        cloud._verify_research_inputs(self.flow, self.repo, 7, self.folder, work, session)
        self.comment["body"] = "게시 직전 편집된 합성 피드백"
        with self.assertRaises(ValueError):
            cloud._verify_research_inputs(self.flow, self.repo, 7, self.folder, work, session)

    def test_request_change_stops_data_checks(self):
        from app.asset_contract import canonical_hash
        self.write(self.folder / "request.json", {"changed": True})
        with self.assertRaisesRegex(ValueError, "연구 요청"):
            cloud._verify_research_inputs(self.flow, self.repo, 7, self.folder,
                                         Path(self.temporary.name),
                                         {"request_hash": canonical_hash({"original": True})})


if __name__ == "__main__":
    unittest.main()
