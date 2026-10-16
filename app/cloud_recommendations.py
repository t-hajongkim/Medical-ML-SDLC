"""Trusted preparation and application for Copilot's constrained recommendations."""

import json
import hashlib
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
    "feedback-requests.json", "feedback-applied.json", "coordination-plan.json",
)
OUTPUT_FILES = (
    "ai-recommendations.json", "eda-plan.json", "evaluation-plan.json",
    "eda.json", "report.md", "recommendation-request.json",
)
MAPPING_OUTPUTS = ("input-schema.json", "input-mapping.json")
COORDINATION_OUTPUTS = ("coordination-plan.json", "feedback-applied.json")


def _outputs(session):
    names = (*OUTPUT_FILES, *MAPPING_OUTPUTS) if session.get("has_input_mapping") else OUTPUT_FILES
    if session.get("has_coordination"):
        names = (*names, *COORDINATION_OUTPUTS)
    if session.get("selection_changed"):
        names = (*names, "selection.json")
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


def _pending_feedback(flow, repository, number, folder, *, applied_ids=None):
    path, applied_path = folder / "feedback-requests.json", folder / "feedback-applied.json"
    from app.pr1_intent import validate_feedback_state
    references, applied = validate_feedback_state(
        read_json(path) if path.is_file() else None,
        read_json(applied_path) if applied_path.is_file() else None,
        repository, number, int(folder.name.removeprefix("issue-")))
    if applied_ids is not None:
        applied = applied_ids
    pending, permissions = [], {}
    for reference in references:
        identifier = flow.issue_number(reference.get("comment_id"))
        if identifier in applied:
            continue
        comment = flow.gh_json(f"repos/{repository}/issues/comments/{identifier}")
        author = comment.get("user", {})
        actor = author.get("login", "")
        if (comment.get("id") != identifier or author.get("type") != "User"
                or not re.fullmatch(r"[A-Za-z0-9-]+", actor)
                or actor != reference.get("actor")
                or comment.get("issue_url") != f"https://api.github.com/repos/{repository}/issues/{number}"
                or not isinstance(comment.get("body"), str)
                or hashlib.sha256(comment["body"].encode("utf-8")).hexdigest()
                != reference.get("body_sha256")):
            raise ValueError("피드백 작성자·본문·대상 PR이 변경되었습니다. 검토 실행을 다시 시작하세요.")
        if actor not in permissions:
            permissions[actor] = flow.gh_json(f"repos/{repository}/collaborators/{actor}/permission").get("permission")
        if permissions[actor] not in ("admin", "maintain", "write"):
            raise ValueError("현재 write 이상 권한이 있는 사람의 피드백만 AI에 전달합니다.")
        pending.append({"comment_id": identifier, "actor": actor, "body": comment["body"],
                        "body_sha256": reference["body_sha256"]})
    return pending, applied


def _verify_research_inputs(flow, repository, number, folder, work, session):
    if canonical_hash(read_json(folder / "request.json")) != session["request_hash"]:
        raise ValueError("연구 요청이 조율 준비 이후 변경되었습니다. 다시 시작하세요.")
    pending, _ = _pending_feedback(
        flow, repository, number, folder, applied_ids=session["applied_feedback_ids"])
    if pending != read_json(work / "RESEARCH_CONTEXT.json")["feedback"]:
        raise ValueError("조율 준비 이후 피드백이 변경되었습니다. 다시 시작하세요.")


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
        for name in INPUT_FILES:
            path = destination / name
            if path.is_symlink():
                raise ValueError("Research review files must not be symlinks.")
            if name in content:
                path.write_text(content[name], encoding="utf-8")
            elif path.exists():
                path.unlink()
        selection = read_json(destination / "selection.json")
        flow.image_assets(reference, work / "assets")
        from app.input_contracts import load_profile
        profile = load_profile()
        binding = read_json(destination / "input-mapping.json") if "input-mapping.json" in content else None
        phase, context, schema_request = "coordination", None, None
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
            "initial_selection_hash": canonical_hash(selection), "has_coordination": True,
            "selection_changed": False,
            "has_input_mapping": phase == "input_mapping" or context.get("input_contract") is not None,
        }
        session["allowed_outputs"] = _outputs(session)
        if context is not None:
            schema_request = context.get("_input_schema_request")
        if not isinstance(schema_request, dict):
            raise ValueError("연구 목적 조율에 검증된 입력 스키마가 필요합니다.")
        manifest = read_json(work / "assets" / "data_manifest.json")
        coordination_schema = {**schema_request,
                               "feature_columns": manifest["feature_columns"],
                               "model_ref": read_json(ROOT / "config" / "assets.json").get("model_image"),
                               "current_bindings": context["input_contract"]["bindings"]
                               if context is not None and context.get("input_contract") else None}
        feedback, applied_ids = _pending_feedback(flow, repository, number, destination)
        from app.pr1_intent import build_context
        research_context = build_context(
            request, coordination_schema, selection, feedback, catalogs=catalogs, policy=policy)
        session.update(context_hash=research_context["context_hash"],
                       coordination_schema=coordination_schema,
                       applied_feedback_ids=applied_ids)
        write_json(work / "RESEARCH_CONTEXT.json", research_context)
        write_json(work / "INPUT_SCHEMA_REQUEST.json", {"status": phase, "request": schema_request})
        write_json(work / "session.json", session)
        return {"status": "ready_for_input_mapping" if phase == "input_mapping" else "ready_for_coordination",
                "phase": phase, "pull_request": number, "context_hash": session["context_hash"],
                "request_path": str(work / "RESEARCH_CONTEXT.json")}
    except Exception:
        shutil.rmtree(work)
        raise


def _prepare_block_request(flow, folder, work, session, context, selection, baseline=None):
    baseline = report(context, session["policy"]["eda"]["required"]) if baseline is None else baseline
    flow.validate_aggregate(baseline, context["data_manifest"]["prepared_source"],
                            [item["block"] for item in baseline["results"]])
    plans = planning.build_plans(
        baseline, context["data_manifest"], selection, None,
        catalogs=session["catalogs"], policy=session["policy"])
    request = plans["recommendation_request"]
    write_json(folder / "recommendation-request.json", request)
    transient = dict(request)
    research_context = read_json(work / "RESEARCH_CONTEXT.json")
    transient["research_context"] = research_context
    transient["eda_results"] = baseline
    write_json(work / "AI_REQUEST.json", transient)
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
    write_json(folder / "input-mapping.json", bundle["binding"])
    write_json(folder / "input-schema.json", bundle["schema_request"])
    session.update(phase="coordination", has_input_mapping=True)
    session["allowed_outputs"] = _outputs(session)
    write_json(work / "session.json", session)
    write_json(work / "INPUT_SCHEMA_REQUEST.json",
               {"status": "coordination", "request": bundle["schema_request"]})
    return {"status": "input_mapping_validated", "next_tool": "run-data-checks",
            "model_execution_performed": False}


def run_data_checks(pull_request, work):
    flow, repository = _runtime()
    number, work = flow.issue_number(pull_request), Path(work)
    session = read_json(work / "session.json")
    if (session.get("repository") != repository or session.get("pull_request") != number
            or session.get("phase") != "coordination"):
        raise ValueError("입력 매핑을 검증하고 연구 목적 조율 단계에서만 EDA를 실행합니다.")
    pull, _, _, _ = _pull(flow, repository, number)
    if pull["head"]["sha"] != session["head_sha"]:
        raise ValueError("점검 계획 중 PR이 변경되었습니다. 최신 상태로 다시 시작하세요.")
    folder = flow.report_directory(ROOT, session["issue_number"])
    selection = read_json(folder / "selection.json")
    if canonical_hash(selection) != session["selection_hash"]:
        raise ValueError("사람 선택이 변경되어 조율을 중단했습니다.")
    _verify_research_inputs(flow, repository, number, folder, work, session)
    from app.input_contracts import _read_json
    from app.pr1_intent import apply_proposal
    result = apply_proposal(
        selection, _read_json(work / "coordination-proposal.json"),
        read_json(work / "RESEARCH_CONTEXT.json"),
        catalogs=session["catalogs"], policy=session["policy"])
    updated, record = result["selection"], result["record"]
    mapping_path = folder / "input-mapping.json"
    context = load_context(
        work / "assets", input_profile=session["input_profile"],
        input_mapping=read_json(mapping_path) if mapping_path.is_file() else None)
    baseline = report(context, result["eda_ids"])
    flow.validate_aggregate(baseline, context["data_manifest"]["prepared_source"], result["eda_ids"])
    wrapper = {"record": record, "code_commit": session["code_commit"],
               "schema": session["coordination_schema"],
               "eda_evidence": {item["block"]: {"status": item["status"], "details": item["details"]}
                                for item in baseline["results"]}}
    wrapper["coordination_hash"] = canonical_hash(wrapper)
    context["_pr1_coordination"] = wrapper
    baseline.update(coordination_hash=wrapper["coordination_hash"],
                    research_request_hash=record["request_hash"],
                    coordination_evidence=wrapper["eda_evidence"])
    write_json(folder / "selection.json", updated)
    write_json(folder / "coordination-plan.json", wrapper)
    feedback_ids = record["feedback_ids"]
    write_json(folder / "feedback-applied.json", {
        "schema_version": 1, "repository": repository, "pull_request": number,
        "comment_ids": sorted(set(session["applied_feedback_ids"]) | set(feedback_ids)),
        "coordination_hash": wrapper["coordination_hash"],
    })
    session.update(
        phase="recommendation", selection_hash=canonical_hash(updated),
        selection_changed=canonical_hash(updated) != session["initial_selection_hash"],
        initial_eda_ids=result["eda_ids"], coordination=wrapper)
    request = _prepare_block_request(flow, folder, work, session, context, updated, baseline)
    session["context_hash"] = request["context_hash"]
    session["allowed_outputs"] = _outputs(session)
    write_json(work / "session.json", session)
    return {"status": "data_checks_completed", "next_tool": "read-recommendation-context",
            "context_hash": request["context_hash"], "selected_eda_ids": result["eda_ids"],
            "feedback_applied": len(feedback_ids), "model_execution_performed": False}


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
        raise ValueError("선택이 검증된 AI 조율 결과와 다릅니다. 최신 상태로 다시 시작하세요.")
    if session.get("has_coordination"):
        _verify_research_inputs(flow, repository, number, folder, work, session)
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
    if session.get("has_coordination"):
        context["_pr1_coordination"] = session["coordination"]
    baseline = report(context, session["policy"]["eda"]["required"])
    plans = planning.build_plans(
        baseline, context["data_manifest"], selection, ai,
        catalogs=session["catalogs"], policy=session["policy"],
    )
    if any(plans[stage]["ai_status"] != "provided" for stage in ("eda", "evaluation")):
        raise ValueError("A validated, non-stale Copilot recommendation is required.")
    if session.get("has_coordination"):
        initial = set(session["coordination"]["record"]["eda_add"]) - set(selection["eda"]["exclude"])
        if not initial.issubset(plans["eda"]["ai_recommended_ids"]):
            raise ValueError("후단 추천에는 앞단 AI가 요청한 추가 점검을 포함해야 합니다.")
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
