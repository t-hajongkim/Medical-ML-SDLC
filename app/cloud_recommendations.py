"""Trusted preparation and application for Copilot's constrained recommendations."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from app.asset_contract import canonical_hash, read_json, write_json
from app.dataset import ROOT
from app.eda import load_context, report
from app import planning


INPUT_FILES = (
    "request.json", "eda.json", "report.md", "evaluation-plan.json",
    "eda-plan.json", "recommendation-request.json", "selection.json",
    "ai-recommendations.json",
)
OUTPUT_FILES = (
    "ai-recommendations.json", "eda-plan.json", "evaluation-plan.json",
    "eda.json", "report.md", "recommendation-request.json",
)


def _runtime():
    from app import research
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise ValueError("Copilot recommendation preparation is restricted to Actions.")
    return research, research.repository_name(os.environ.get("GITHUB_REPOSITORY"))


def _sha(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError(f"Invalid {label} commit.")
    return value


def _pull(flow, repository, number):
    pull = flow.gh_json(f"repos/{repository}/pulls/{number}")
    branch = pull.get("head", {}).get("ref", "")
    match = re.fullmatch(r"research/review-issue-([1-9][0-9]{0,9})", branch)
    if (pull.get("number") != number or pull.get("state") != "open" or not match
            or pull.get("head", {}).get("repo", {}).get("full_name") != repository
            or not isinstance(pull.get("title"), str)
            or not pull["title"].startswith("연구검토 #")):
        raise ValueError("Recommendations require an open, same-repository research review PR.")
    default = flow.gh_json(f"repos/{repository}")["default_branch"]
    if pull.get("base", {}).get("ref") != default:
        raise ValueError("Research review must target the repository default branch.")
    return pull, branch, int(match.group(1)), default


def _read_branch_file(flow, commit, relative, optional=False):
    entry = flow.command(["git", "ls-tree", commit, "--", relative], cwd=ROOT).stdout.strip()
    if not entry:
        if optional:
            return None
        raise ValueError("The review branch is missing a required report file.")
    parts = entry.split(None, 3)
    if len(parts) != 4 or parts[0] != "100644" or parts[1] != "blob" or parts[3] != relative:
        raise ValueError("Research review inputs must be regular tracked files.")
    content = flow.command(["git", "show", f"{commit}:{relative}"], cwd=ROOT).stdout
    if len(content.encode("utf-8")) > 10_000_000:
        raise ValueError("Research review input exceeds its size limit.")
    return content


def prepare_ai(pull_request, work):
    flow, repository = _runtime()
    number = flow.issue_number(pull_request)
    work = Path(work)
    if work.exists() or work.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("Use a new recommendation directory outside the checkout.")
    if flow.command(["git", "status", "--porcelain"], cwd=ROOT).stdout.strip():
        raise ValueError("Copilot preparation requires a clean trusted checkout.")
    pull, branch, issue, default = _pull(flow, repository, number)
    head = _sha(pull["head"]["sha"], "PR head")
    code_commit = _sha(flow.command(["git", "rev-parse", "HEAD"], cwd=ROOT).stdout.strip(), "code")
    flow.command(["git", "fetch", "origin", f"refs/heads/{branch}"], cwd=ROOT)
    if flow.command(["git", "rev-parse", "FETCH_HEAD"], cwd=ROOT).stdout.strip() != head:
        raise ValueError("The review branch changed while preparing recommendations.")
    catalogs = {stage: planning.load_catalog(stage) for stage in ("eda", "evaluation")}
    policy = planning.load_policy()
    report_dir = f"research/reports/issue-{issue}"
    content = {}
    for name in INPUT_FILES:
        value = _read_branch_file(
            flow, head, report_dir + "/" + name, optional=name == "ai-recommendations.json")
        if value is not None:
            content[name] = value
    request = json.loads(content["request.json"])
    if request.get("repository") != repository or request.get("issue_number") != issue:
        raise ValueError("The PR's request marker is inconsistent.")
    reference = flow.data_reference(read_json(ROOT / "config" / "assets.json"))
    work.mkdir(parents=True)
    try:
        destination = flow.report_directory(ROOT, issue)
        destination.mkdir(parents=True, exist_ok=True)
        for name, value in content.items():
            path = destination / name
            if path.is_symlink():
                raise ValueError("Research review files must not be symlinks.")
            path.write_text(value, encoding="utf-8")
        selection = read_json(destination / "selection.json")
        flow.image_assets(reference, work / "assets")
        context = load_context(work / "assets")
        required = policy["eda"]["required"]
        baseline = report(context, required)
        plans = planning.build_plans(
            baseline, context["data_manifest"], selection, None,
            catalogs=catalogs, policy=policy,
        )
        ai_request = plans["recommendation_request"]
        write_json(destination / "recommendation-request.json", ai_request)
        write_json(work / "AI_REQUEST.json", ai_request)
        session = {
            "schema_version": 1, "repository": repository, "pull_request": number,
            "issue_number": issue, "head_sha": head, "head_branch": branch,
            "default_branch": default, "report_dir": report_dir,
            "allowed_outputs": [report_dir + "/" + name for name in OUTPUT_FILES],
            "data_image": reference, "code_commit": code_commit,
            "context_hash": ai_request["context_hash"], "catalogs": catalogs, "policy": policy,
            "selection_hash": canonical_hash(selection), "request_hash": canonical_hash(request),
        }
        write_json(work / "session.json", session)
        return {"status": "ready_for_copilot", "pull_request": number,
                "context_hash": ai_request["context_hash"], "request_path": str(work / "AI_REQUEST.json")}
    except Exception:
        shutil.rmtree(work)
        raise


def _git_tree_commit(flow, root, work, session):
    environment = {**os.environ, "GIT_INDEX_FILE": str(work / "publication.index")}
    def git(*args, data=None):
        result = subprocess.run(
            ["git", *args], cwd=root, env=environment, input=data,
            capture_output=True, text=True, encoding="utf-8", timeout=120,
        )
        if result.returncode:
            raise RuntimeError("Building the constrained recommendation commit failed.")
        return result.stdout.strip()
    git("read-tree", session["head_sha"])
    for relative in session["allowed_outputs"]:
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError("A permitted recommendation output is missing or is a symlink.")
        blob = _sha(git("hash-object", "-w", "--no-filters", str(path)), "output blob")
        git("update-index", "--add", "--cacheinfo", "100644", blob, relative)
    tree = _sha(git("write-tree"), "publication tree")
    if tree == git("rev-parse", session["head_sha"] + "^{tree}"):
        return None
    commit = _sha(git(
        "-c", "user.name=github-actions[bot]",
        "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
        "commit-tree", tree, "-p", session["head_sha"],
        "-m", f"Update registered block recommendations for Issue #{session['issue_number']}",
    ), "publication")
    changed = git("diff-tree", "--no-commit-id", "--name-only", "-r", commit).splitlines()
    if not set(changed).issubset(session["allowed_outputs"]):
        raise ValueError("Recommendation commit includes files outside the permitted outputs.")
    # Stage only restored report inputs so checkout cannot overwrite untracked user files.
    paths = [session["report_dir"] + "/" + name for name in INPUT_FILES
             if (root / session["report_dir"] / name).is_file()]
    flow.command(["git", "add", "--", *paths], cwd=root)
    staged = flow.command(["git", "diff", "--cached", "--name-only"], cwd=root).stdout.splitlines()
    if not set(staged).issubset(paths):
        raise ValueError("Unrelated staged changes block recommendation publication.")
    # All Python work is complete before selecting the PR-based publication commit.
    flow.command(["git", "checkout", "--detach", commit], cwd=root)
    return commit


def apply_ai(pull_request, work):
    flow, repository = _runtime()
    number = flow.issue_number(pull_request)
    work = Path(work)
    session = read_json(work / "session.json")
    if (session.get("schema_version") != 1 or session.get("repository") != repository
            or session.get("pull_request") != number):
        raise ValueError("Recommendation session does not match this repository and PR.")
    pull, branch, issue, default = _pull(flow, repository, number)
    if (session.get("head_sha") != pull["head"]["sha"] or session.get("head_branch") != branch
            or session.get("issue_number") != issue or session.get("default_branch") != default
            or session.get("report_dir") != f"research/reports/issue-{issue}"):
        raise ValueError("The review PR changed; rerun recommendations on its current head.")
    expected_outputs = [session["report_dir"] + "/" + name for name in OUTPUT_FILES]
    if session.get("allowed_outputs") != expected_outputs:
        raise ValueError("Recommendation output allowlist changed.")
    folder = flow.report_directory(ROOT, issue)
    selection = read_json(folder / "selection.json")
    if canonical_hash(selection) != session["selection_hash"]:
        raise ValueError("Copilot must not modify the user's selections.")
    ai = read_json(folder / "ai-recommendations.json")
    if ai.get("context_hash") != session["context_hash"]:
        raise ValueError("Copilot recommendations belong to a different context.")
    context = load_context(work / "assets")
    baseline = report(context, session["policy"]["eda"]["required"])
    plans = planning.build_plans(
        baseline, context["data_manifest"], selection, ai,
        catalogs=session["catalogs"], policy=session["policy"],
    )
    if any(plans[stage]["ai_status"] != "provided" for stage in ("eda", "evaluation")):
        raise ValueError("A validated, non-stale Copilot recommendation is required.")
    request = read_json(folder / "request.json")
    if (request.get("repository") != repository or request.get("issue_number") != issue
            or canonical_hash(request) != session["request_hash"]):
        raise ValueError("The research request marker changed.")
    provenance = {
        "data_image": session["data_image"], "code_commit": session["code_commit"],
        "workflow_run_url": f"https://github.com/{repository}/actions/runs/"
                            + flow.run_identifier(os.environ.get("GITHUB_RUN_ID")),
        "data_access": "Validated prepared files extracted from the fixed data image; no database queries.",
    }
    eda = report(context, plans["eda"]["selected_ids"])
    flow.write_payload(
        ROOT, request, eda, provenance, context=context,
        catalogs=session["catalogs"], policy=session["policy"],
    )
    if canonical_hash(read_json(folder / "selection.json")) != session["selection_hash"]:
        raise ValueError("User selections were not preserved.")
    body = flow.merge_review_body(pull.get("body") or "", (folder / "report.md").read_text(encoding="utf-8"))
    (work / "pr-body.md").write_text(body, encoding="utf-8")
    commit = _git_tree_commit(flow, ROOT, work, session)
    status = "validated_copilot_recommendations" if commit else (
        "body_refresh" if body != (pull.get("body") or "") else "unchanged")
    return {"status": status, "pull_request": number,
            "issue_number": issue, "publication_commit": commit,
            "allowed_outputs": session["allowed_outputs"],
            "pr_body_path": str(work / "pr-body.md"), "remote_publication_performed": False}
