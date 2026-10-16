"""Trusted preparation and application for Copilot's constrained recommendations."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from app.asset_contract import canonical_hash, read_json, write_json
from app.dataset import ROOT
from app.eda import InputMappingRequired, load_context, report
from app import planning


INPUT_FILES = (
    "request.json", "eda.json", "report.md", "evaluation-plan.json",
    "eda-plan.json", "recommendation-request.json", "selection.json",
    "ai-recommendations.json",
    "input-schema.json", "input-mapping.json",
)
OUTPUT_FILES = (
    "ai-recommendations.json", "eda-plan.json", "evaluation-plan.json",
    "eda.json", "report.md", "recommendation-request.json",
)
MAPPING_OUTPUTS = ("input-schema.json", "input-mapping.json")


def _outputs(session):
    names = (*OUTPUT_FILES, *MAPPING_OUTPUTS) if session.get("has_input_mapping") else OUTPUT_FILES
    return [session["report_dir"] + "/" + name for name in names]


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
            flow, head, report_dir + "/" + name, optional=name not in ("request.json", "selection.json"))
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
        from app.input_contracts import load_profile
        profile = load_profile()
        binding = read_json(destination / "input-mapping.json") if "input-mapping.json" in content else None
        phase, context, schema_request = "recommendation", None, None
        try:
            context = load_context(work / "assets", input_profile=profile, input_mapping=binding)
        except InputMappingRequired as pending:
            phase, schema_request = "input_mapping", pending.schema_request
        session = {
            "schema_version": 1, "repository": repository, "pull_request": number,
            "issue_number": issue, "head_sha": head, "head_branch": branch,
            "default_branch": default, "report_dir": report_dir,
            "data_image": reference, "code_commit": code_commit,
            "catalogs": catalogs, "policy": policy, "phase": phase, "input_profile": profile,
            "selection_hash": canonical_hash(selection), "request_hash": canonical_hash(request),
            "has_input_mapping": phase == "input_mapping" or context.get("input_contract") is not None,
        }
        session["allowed_outputs"] = _outputs(session)
        if context is not None:
            ai_request = _prepare_block_request(flow, destination, work, session, context, selection)
            schema_request = context.get("_input_schema_request")
            session["context_hash"] = ai_request["context_hash"]
        else:
            session["context_hash"] = schema_request["context_hash"]
        write_json(work / "INPUT_SCHEMA_REQUEST.json", {"status": phase, "request": schema_request})
        write_json(work / "session.json", session)
        return {"status": "ready_for_input_mapping" if phase == "input_mapping" else "ready_for_copilot",
                "phase": phase, "pull_request": number, "context_hash": session["context_hash"],
                "request_path": str(work / ("INPUT_SCHEMA_REQUEST.json" if phase == "input_mapping"
                                           else "AI_REQUEST.json"))}
    except Exception:
        shutil.rmtree(work)
        raise


def _prepare_block_request(flow, folder, work, session, context, selection):
    baseline = report(context, session["policy"]["eda"]["required"])
    flow.validate_aggregate(baseline, context["data_manifest"]["prepared_source"],
                            session["policy"]["eda"]["required"])
    plans = planning.build_plans(
        baseline, context["data_manifest"], selection, None,
        catalogs=session["catalogs"], policy=session["policy"])
    request = plans["recommendation_request"]
    write_json(folder / "recommendation-request.json", request)
    write_json(work / "AI_REQUEST.json", request)
    if context.get("input_contract") is not None:
        write_json(folder / "input-mapping.json", context["input_contract"])
        write_json(folder / "input-schema.json", context["_input_schema_request"])
    return request


def apply_input_mapping(pull_request, work):
    flow, repository = _runtime()
    number, work = flow.issue_number(pull_request), Path(work)
    session = read_json(work / "session.json")
    if (session.get("repository") != repository or session.get("pull_request") != number
            or session.get("phase") != "input_mapping"):
        raise ValueError("현재 입력 매핑 단계의 세션만 처리합니다.")
    pull, _, _, _ = _pull(flow, repository, number)
    if pull["head"]["sha"] != session["head_sha"]:
        raise ValueError("입력 매핑 중 PR이 변경되었습니다. 최신 입력으로 다시 시작하세요.")
    folder = flow.report_directory(ROOT, session["issue_number"])
    selection = read_json(folder / "selection.json")
    if canonical_hash(selection) != session["selection_hash"]:
        raise ValueError("입력 매핑 단계에서 사람의 선택을 바꿀 수 없습니다.")
    from app.input_contracts import _read_json, apply_mapping
    proposal = _read_json(work / "input-mapping-proposal.json")
    bundle = apply_mapping(work / "assets", proposal, profile=session["input_profile"])
    context = bundle["context"]
    context["_input_schema_request"] = bundle["schema_request"]
    request = _prepare_block_request(flow, folder, work, session, context, selection)
    session.update(phase="recommendation", has_input_mapping=True,
                   context_hash=request["context_hash"])
    session["allowed_outputs"] = _outputs(session)
    write_json(work / "session.json", session)
    write_json(work / "INPUT_SCHEMA_REQUEST.json",
               {"status": "recommendation", "request": bundle["schema_request"]})
    return {"status": "input_mapping_validated", "next_tool": "read-recommendation-context",
            "context_hash": request["context_hash"], "model_execution_performed": False}


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
    if session.get("phase", "recommendation") != "recommendation":
        raise ValueError("먼저 입력 매핑을 검증한 뒤 EDA·블록 추천을 처리해야 합니다.")
    pull, branch, issue, default = _pull(flow, repository, number)
    if (session.get("head_sha") != pull["head"]["sha"] or session.get("head_branch") != branch
            or session.get("issue_number") != issue or session.get("default_branch") != default
            or session.get("report_dir") != f"research/reports/issue-{issue}"):
        raise ValueError("The review PR changed; rerun recommendations on its current head.")
    expected_outputs = _outputs(session)
    if session.get("allowed_outputs") != expected_outputs:
        raise ValueError("Recommendation output allowlist changed.")
    folder = flow.report_directory(ROOT, issue)
    selection = read_json(folder / "selection.json")
    if canonical_hash(selection) != session["selection_hash"]:
        raise ValueError("Copilot must not modify the user's selections.")
    ai = read_json(folder / "ai-recommendations.json")
    if ai.get("context_hash") != session["context_hash"]:
        raise ValueError("Copilot recommendations belong to a different context.")
    if "input_profile" in session:
        binding_path = folder / "input-mapping.json"
        context = load_context(
            work / "assets", input_profile=session["input_profile"],
            input_mapping=read_json(binding_path) if binding_path.is_file() else None)
    else:
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
