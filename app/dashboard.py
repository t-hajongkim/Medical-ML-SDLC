"""Publish aggregate-only stage snapshots; never run EDA, inference or Pages."""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import quote
import zipfile

from app import input_contracts, model_evaluation, recommendation_events, research, result_review
from app.asset_contract import canonical_hash, read_json, write_json
from app.github_io import git_sha, read_file, read_object, read_tree
from app.review_eval_plan import _commit_files, _write_api


BRANCH = "dashboard-data"
DATA_PATH = "dashboard/assets/dashboard-data.json"
TARGET_FILE = "dashboard-target.json"
WORKFLOWS = {
    ".github/workflows/prepare-eda-pr.yml": ("Prepare research review", {"issues", "workflow_dispatch"}),
    ".github/workflows/review-eval-plan.yml": ("Review evaluation plan feedback", {"issue_comment"}),
    ".github/workflows/research-plan.lock.yml": ("연구 점검·평가계획", {"workflow_run", "workflow_dispatch"}),
    ".github/workflows/evaluate-model.yml": ("Evaluate merged research plan", {"pull_request", "workflow_dispatch"}),
    ".github/workflows/publish-evaluation.yml": ("Prepare evaluation result PR2", {"workflow_run"}),
}
PRIVATE_KEYS = {"patient_key", "row_key", "sample_ids", "selected_indices", "y_probability", "y_true"}


def encoded(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")


def empty_snapshot(repository=None):
    return {
        "schema": 1, "purpose": "GitHub Actions aggregate stage snapshot", "generated_at": None,
        "pipeline": {"repository": repository, "issue_number": None, "stage": "idle",
                     "source_run": None, "status": "pending"},
        "remote_pr1": None, "target": None, "dataset": {},
        "eda": {"assessment_status": "pending", "results": [], "executed_ids": [], "reused_ids": []},
        "evaluation_plan": {}, "local_smoke": None, "remote_model_evaluation": None, "remote_pr2": None,
    }


def check_public(value):
    if isinstance(value, dict):
        if PRIVATE_KEYS & value.keys():
            raise ValueError("Dashboard snapshots cannot contain individual rows or prediction vectors.")
        for item in value.values():
            check_public(item)
    elif isinstance(value, list):
        for item in value:
            check_public(item)


def source_run(repository, identifier, attempt=None):
    identifier = research.run_identifier(str(identifier))
    source = research.gh_json(f"repos/{repository}/actions/runs/{identifier}")
    path = source.get("path")
    if (path not in WORKFLOWS or str(source.get("id")) != identifier
            or source.get("name") != WORKFLOWS[path][0]
            or source.get("event") not in WORKFLOWS[path][1]
            or source.get("repository", {}).get("full_name") != repository
            or source.get("head_repository", {}).get("full_name") != repository
            or source.get("status") != "completed"
            or (attempt is not None and str(source.get("run_attempt")) != str(attempt))):
        raise ValueError("Dashboard source must be the exact registered completed same-repository workflow.")
    git_sha(source.get("head_sha"))
    research.run_identifier(str(source.get("run_attempt")))
    if (path != ".github/workflows/evaluate-model.yml"
            and source.get("head_branch") != research.gh_json(f"repos/{repository}")["default_branch"]):
        raise ValueError("Dashboard control workflows must run on the trusted default branch.")
    workflow = research.gh_json(f"repos/{repository}/actions/workflows/{path.rsplit('/', 1)[1]}")
    if (workflow.get("id") != source.get("workflow_id") or workflow.get("path") != path
            or workflow.get("name") != WORKFLOWS[path][0] or workflow.get("state") != "active"):
        raise ValueError("Dashboard source workflow identity changed.")
    return source


def artifact_files(repository, source, name, limits):
    pages = research.gh_json(
        f"repos/{repository}/actions/runs/{source['id']}/artifacts?per_page=100",
        "--paginate", "--slurp")
    if not isinstance(pages, list) or any(not isinstance(page.get("artifacts"), list) for page in pages):
        raise ValueError("Dashboard artifact listing is incomplete.")
    matches = [item for page in pages for item in page["artifacts"] if item.get("name") == name]
    if (len(matches) != 1 or matches[0].get("expired") is not False
            or matches[0].get("workflow_run", {}).get("id") != source["id"]
            or matches[0]["workflow_run"].get("head_sha") != source["head_sha"]):
        raise ValueError("Dashboard requires one unexpired artifact bound to the exact source run.")
    identifier = research.issue_number(matches[0].get("id"))
    try:
        downloaded = subprocess.run(
            ["gh", "api", f"repos/{repository}/actions/artifacts/{identifier}/zip"],
            capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError("Could not download the dashboard source artifact.") from None
    if downloaded.returncode or len(downloaded.stdout) > sum(limits.values()) + 65536:
        raise RuntimeError("Dashboard source artifact download failed or exceeded its limit.")
    try:
        with zipfile.ZipFile(io.BytesIO(downloaded.stdout)) as archive:
            entries = archive.infolist()
            if (len(entries) != len(limits) or {item.filename for item in entries} != set(limits)
                    or any(item.is_dir() or item.flag_bits & 1
                           or item.file_size > limits[item.filename]
                           or ((item.external_attr >> 16) & 0o170000) not in (0, 0o100000)
                           for item in entries)):
                raise ValueError("Dashboard source artifact contains unexpected or unbounded files.")
            return {item.filename: archive.read(item) for item in entries}
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError):
        raise ValueError("Dashboard source artifact is not a readable bounded ZIP.") from None


def write_target(kind, number, evaluation_attempt=None, *, ready=True):
    if ready and kind not in ("issue", "pull_request", "evaluation_run"):
        raise ValueError("Unsupported dashboard target.")
    repository = research.repository_name(os.environ["GITHUB_REPOSITORY"])
    runner = Path(os.environ["RUNNER_TEMP"]).resolve()
    if not runner.is_dir() or runner.is_relative_to(research.ROOT.resolve()):
        raise ValueError("Dashboard target metadata belongs outside the source checkout.")
    value = {
        "schema_version": 1, "repository": repository,
        "source_run_id": research.run_identifier(os.environ["GITHUB_RUN_ID"]),
        "source_run_attempt": research.run_identifier(os.environ["GITHUB_RUN_ATTEMPT"]),
        "source_head_sha": git_sha(os.environ["GITHUB_SHA"]),
        "requested": ready,
    }
    if ready:
        value.update(
            target_kind=kind,
            target_number=(int(research.run_identifier(str(number))) if kind == "evaluation_run"
                           else research.issue_number(number)))
        if kind == "evaluation_run":
            value["evaluation_attempt"] = research.run_identifier(evaluation_attempt)
    path = runner / TARGET_FILE
    if path.is_symlink():
        raise ValueError("Dashboard control metadata cannot be a symlink.")
    write_json(path, value)


def read_target(repository, source):
    name = f"dashboard-target-{source['id']}-{source['run_attempt']}"
    value = read_object(artifact_files(repository, source, name, {TARGET_FILE: 65536})[TARGET_FILE])
    fields = {"schema_version", "repository", "source_run_id", "source_run_attempt",
              "source_head_sha", "requested"}
    kind = value.get("target_kind")
    if value.get("requested") is True:
        fields |= {"target_kind", "target_number"}
        if kind == "evaluation_run":
            fields.add("evaluation_attempt")
    if (set(value) != fields or type(value.get("schema_version")) is not int
            or value["schema_version"] != 1 or value.get("repository") != repository
            or value.get("source_run_id") != str(source["id"])
            or value.get("source_run_attempt") != str(source["run_attempt"])
            or value.get("source_head_sha") != source["head_sha"]
            or type(value.get("requested")) is not bool
            or (value["requested"] and kind not in ("issue", "pull_request", "evaluation_run"))):
        raise ValueError("Dashboard target and source run/attempt do not match.")
    if not value["requested"]:
        return value
    if kind == "evaluation_run":
        research.run_identifier(str(value["target_number"]))
        research.run_identifier(value["evaluation_attempt"])
    else:
        research.issue_number(value["target_number"])
    if (kind == "evaluation_run") != (source["path"] == ".github/workflows/publish-evaluation.yml"):
        raise ValueError("Dashboard target kind does not match its source workflow.")
    return value


def evaluation_artifact(repository, source):
    if source.get("path") != ".github/workflows/evaluate-model.yml" or source.get("conclusion") != "success":
        raise ValueError("Only successful model workflows provide evaluation data.")
    files = artifact_files(
        repository, source, f"model-evaluation-{source['id']}-{source['run_attempt']}",
        {"evaluation.json": 1_000_000, "report.md": 1_000_000})
    result = read_object(files["evaluation.json"])
    if (set(result) != result_review.RESULT_FIELDS
            or files["report.md"] != model_evaluation.markdown(result).encode("utf-8")):
        raise ValueError("Dashboard evaluation differs from the canonical aggregate contract.")
    return result


def find_pull(repository, default, branch):
    owner = repository.split("/")[0]
    pages = research.gh_json(
        f"repos/{repository}/pulls?state=all&head={quote(owner + ':' + branch, safe='')}"
        f"&base={quote(default, safe='')}&per_page=100", "--paginate", "--slurp")
    if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
        raise ValueError("Dashboard PR lookup returned an incomplete list.")
    pulls = [pull for page in pages for pull in page]
    if len(pulls) > 1:
        raise ValueError("Multiple PRs own the same dashboard source branch.")
    if not pulls:
        return None
    pull = pulls[0]
    if (pull.get("head", {}).get("ref") != branch
            or pull.get("head", {}).get("repo", {}).get("full_name") != repository
            or pull.get("base", {}).get("ref") != default
            or pull.get("base", {}).get("repo", {}).get("full_name") != repository):
        raise ValueError("Dashboard PR belongs to a different branch or repository.")
    git_sha(pull["head"]["sha"])
    research.issue_number(pull.get("number"))
    return pull


def validate_evaluation(result, reports, manifest, repository, pull, merge):
    check_public(result)
    origin = result.get("provenance", {})
    plan = reports["evaluation-plan.json"]
    issue = reports["request.json"]["issue_number"]
    if (set(result) != result_review.RESULT_FIELDS or result.get("schema_version") != 1
            or type(result["schema_version"]) is not int
            or result.get("kind") != "fixed-model-evaluation"
            or result.get("clinical_use_approved") is not False
            or origin.get("repository") != repository or origin.get("issue_number") != issue
            or origin.get("pull_request") != pull["number"] or origin.get("merge_commit") != merge
            or origin.get("plan_code_commit") != reports["execution-plan.json"]["code_commit"]
            or result.get("data_id") != manifest["data_id"]
            or result.get("model_id") != result_review.model_identity(manifest)
            or any(result.get(key) != reports["execution-plan.json"][key]
                   for key in ("data_image", "model_image"))
            or result.get("plan_hash") != canonical_hash(plan)
            or result.get("eda_execution_result_hash") != reports["eda.json"]["execution_result_hash"]
            or result.get("eda_assessment_status") != reports["eda.json"]["assessment_status"]
            or result.get("selected_ids") != plan["selected_ids"]
            or result.get("primary_metric") != plan["primary_metric"]
            or result.get("limitations") != model_evaluation.result_limitations(manifest)):
        raise ValueError("Dashboard evaluation is not bound to this Issue, merged plan and dataset.")
    match = re.fullmatch(re.escape(f"https://github.com/{repository}/actions/runs/") + r"([1-9][0-9]*)",
                         origin.get("workflow_run_url", ""))
    if not match:
        raise ValueError("Dashboard evaluation source URL is not from this repository.")
    source = source_run(repository, match.group(1))
    execution = git_sha(origin.get("execution_code_commit"))
    if (source["path"] != ".github/workflows/evaluate-model.yml" or source["conclusion"] != "success"
            or source["head_sha"] not in (merge, pull["head"]["sha"], execution)
            or (source["event"] == "pull_request" and source.get("pull_requests")
                and (len(source["pull_requests"]) != 1
                     or source["pull_requests"][0].get("number") != pull["number"]))):
        raise ValueError("Dashboard evaluation has no matching successful model execution.")
    summary = result.get("inference", {})
    if (set(summary) != {"split", "rows", "feature_count", "positive_class", "model_class",
                        "runtime_scikit_learn", "training_performed", "preprocessing_performed",
                        "inference_performed"}
            or summary.get("inference_performed") is not True
            or summary.get("training_performed") is not False
            or summary.get("preprocessing_performed") is not False
            or summary.get("split") != "validation"
            or type(summary.get("rows")) is not int
            or summary.get("rows") != manifest["split_rows"]["validation"]
            or type(summary.get("feature_count")) is not int
            or summary.get("feature_count") != len(manifest["feature_columns"])
            or type(summary.get("positive_class")) is not int or summary["positive_class"] != 1
            or summary.get("model_class") != "sklearn.linear_model.LogisticRegression"
            or summary.get("runtime_scikit_learn") != "1.2.2"
            or not isinstance(result.get("metrics"), list)
            or len(result["metrics"]) != len(result["selected_ids"])):
        raise ValueError("Dashboard inference summary differs from its fixed input contract.")
    result_review.validate_metrics(result, plan)


def build_snapshot(repository, issue, default, previous=None, evaluation=None, trigger=None):
    issue = research.issue_number(issue)
    raw_issue = research.gh_json(f"repos/{repository}/issues/{issue}")
    request = research.parse_issue(raw_issue, repository)
    if request is None or request["issue_number"] != issue or "pull_request" in raw_issue:
        raise ValueError("Dashboard target must be a registered research Issue.")
    payload = empty_snapshot(repository)
    payload["target"] = request
    payload["pipeline"].update(issue_number=issue, stage="target")
    if trigger:
        payload["pipeline"].update(source_run=str(trigger["id"]), status=trigger["conclusion"])
    pull = find_pull(repository, default, f"research/review-issue-{issue}")
    if pull:
        detail = research.gh_json(f"repos/{repository}/pulls/{pull['number']}")
        merged = detail.get("merged") is True
        commit = git_sha(detail["merge_commit_sha"] if merged else pull["head"]["sha"])
        reports = model_evaluation.read_reports(repository, commit, issue, research.gh_json)
        config = read_json(research.ROOT / "config" / "assets.json")
        manifest = model_evaluation.validate_reports(
            reports, config, repository, issue, require_complete=False)
        binding = reports["input-mapping.json"]
        context = input_contracts.planning_context(
            manifest, binding["profile_snapshot"], binding, reports["input-schema.json"])
        eda = reports["eda.json"]
        run_url = eda["provenance"]["workflow_run_url"]
        payload.update(
            target=reports["request.json"],
            remote_pr1={
                "repository": repository, "issue": issue, "pr": pull["number"],
                "url": pull["html_url"], "head": commit,
                "source_run": run_url.rsplit("/", 1)[1],
                "code_commit": reports["execution-plan.json"]["code_commit"],
                "status": "merged" if merged else pull["state"],
                "merge_commit": detail.get("merge_commit_sha") if merged else None,
                "merge_at": detail.get("merged_at"),
            },
            dataset={key: deepcopy(manifest[key]) for key in (
                "data_id", "feature_columns", "split_rows")},
            eda={key: deepcopy(eda[key]) for key in (
                "assessment_status", "results", "executed_ids", "reused_ids")},
            evaluation_plan={key: deepcopy(reports["evaluation-plan.json"][key]) for key in (
                "selected_ids", "primary_metric", "threshold", "parameters", "objectives",
                "ai_status", "planning_complete", "items")})
        payload["dataset"].update(
            feature_count=len(manifest["feature_columns"]),
            target_mapping=deepcopy(context["target_mapping"]))
        for key in ("patient_overlap", "race_missing"):
            if key in manifest:
                payload["dataset"][key] = deepcopy(manifest[key])
        payload["pipeline"]["stage"] = "model" if merged else "evaluation-plan"
        if merged:
            result_pull = find_pull(repository, default, f"research/result-issue-{issue}-validation")
            stored = None
            if result_pull:
                tree = read_tree(repository, result_pull["head"]["sha"], research.gh_json)
                prefix = f"research/results/issue-{issue}/validation/"
                stored = read_object(read_file(repository, tree, prefix + "evaluation.json", research.gh_json))
                text = read_file(repository, tree, prefix + "report.md", research.gh_json)
                if text != model_evaluation.markdown(stored).encode("utf-8"):
                    raise ValueError("Dashboard PR2 report was edited or is incomplete.")
                validate_evaluation(stored, reports, manifest, repository, pull, commit)
            retained = (previous or {}).get("remote_model_evaluation")
            result = evaluation or stored or retained
            if result:
                if result is not stored:
                    validate_evaluation(result, reports, manifest, repository, pull, commit)
                payload["remote_model_evaluation"] = result
                payload["pipeline"]["stage"] = "evaluation"
                if stored and result_review.semantic_result(result) == result_review.semantic_result(stored):
                    payload["remote_pr2"] = {
                        "number": result_pull["number"], "url": result_pull["html_url"],
                        "state": result_pull["state"].upper(), "isDraft": result_pull["draft"],
                        "headRefOid": result_pull["head"]["sha"],
                        "review_status": "사람 리뷰 대기" if result_pull["state"] == "open" else "닫힘",
                    }
                    payload["pipeline"]["stage"] = "result"
    check_public(payload)
    return payload


def resolve_event(event, event_name, repository, requested=None):
    if event.get("repository", {}).get("full_name") != repository:
        raise ValueError("Dashboard event repository mismatch.")
    if event_name == "workflow_dispatch":
        return (research.issue_number(requested) if requested else None), None, None
    if event_name != "workflow_run":
        raise ValueError("Unsupported dashboard event.")
    original = event["workflow_run"]
    source = source_run(repository, original["id"], original["run_attempt"])
    if source["conclusion"] == "skipped":
        return None, source, None
    if source["path"] in recommendation_events.SOURCES:
        target = recommendation_events.resolve_target(event, event_name, repository)
        if not target["ready"]:
            return None, source, None
    elif source["path"] == ".github/workflows/evaluate-model.yml":
        if source["conclusion"] == "success":
            result = evaluation_artifact(repository, source)
            return research.issue_number(result["provenance"]["issue_number"]), source, result
        match = re.fullmatch(r"research/review-issue-([1-9][0-9]*)", source.get("head_branch", ""))
        if not match:
            raise ValueError("Failed evaluation source does not identify a research Issue.")
        return int(match.group(1)), source, None
    else:
        target = read_target(repository, source)
        if not target["requested"]:
            return None, source, None
    kind, number = target["target_kind"], target["target_number"]
    if kind == "evaluation_run":
        evaluation_source = source_run(repository, number, target["evaluation_attempt"])
        result = evaluation_artifact(repository, evaluation_source)
        return research.issue_number(result["provenance"]["issue_number"]), source, result
    if kind == "pull_request":
        pull = research.gh_json(f"repos/{repository}/pulls/{number}")
        match = re.fullmatch(r"research/review-issue-([1-9][0-9]*)", pull.get("head", {}).get("ref", ""))
        if not match or pull.get("head", {}).get("repo", {}).get("full_name") != repository:
            raise ValueError("Dashboard target PR is not a same-repository research review.")
        return int(match.group(1)), source, None
    return research.issue_number(number), source, None


def existing_state(repository, default):
    if default == BRANCH:
        raise ValueError("The dashboard data branch must not be the default branch.")
    refs = research.gh_json(f"repos/{repository}/git/matching-refs/heads/{BRANCH}")
    exact = [item for item in refs if item.get("ref") == "refs/heads/" + BRANCH]
    if len(exact) > 1:
        raise ValueError("Dashboard data branch is ambiguous.")
    head = git_sha(exact[0]["object"]["sha"]) if exact else git_sha(research.gh_json(
        f"repos/{repository}/git/ref/heads/{quote(default, safe='')}")["object"]["sha"])
    tree = read_tree(repository, head, research.gh_json)
    previous = read_file(repository, tree, DATA_PATH, research.gh_json, optional=True, max_bytes=2_000_000)
    active = read_object(previous) if previous else empty_snapshot(repository)
    if (active.get("schema") != 1 or not isinstance(active.get("pipeline"), dict)
            or active["pipeline"].get("repository") not in (None, repository)
            or (active["pipeline"].get("repository") is None
                and active["pipeline"].get("issue_number") is not None)):
        raise ValueError("The existing dashboard snapshot belongs to a different repository or schema.")
    check_public(active)
    return bool(exact), head, tree, active


def publish(repository, event, event_name, output, requested=None):
    repository = research.repository_name(repository)
    metadata = research.gh_json(f"repos/{repository}")
    default = metadata["default_branch"]
    issue, trigger, evaluation = resolve_event(event, event_name, repository, requested)
    if event_name == "workflow_run" and issue is None:
        return {"status": "not_applicable", "reason": "The source did not request a research update."}
    exists, head, tree, active = existing_state(repository, default)
    case_path = f"dashboard/data/issue-{issue}.json" if issue else None
    prior = read_file(repository, tree, case_path, research.gh_json, optional=True, max_bytes=2_000_000) if case_path else None
    previous = read_object(prior) if prior else None
    if previous and (previous.get("schema") != 1
                     or previous.get("pipeline", {}).get("repository") != repository
                     or previous.get("pipeline", {}).get("issue_number") != issue):
        raise ValueError("The saved per-Issue dashboard snapshot has a different owner.")
    payload = build_snapshot(repository, issue, default, previous, evaluation, trigger) if issue else empty_snapshot(repository)
    if previous:
        old_run, new_run = previous["pipeline"].get("source_run"), payload["pipeline"].get("source_run")
        if old_run and (not new_run or int(old_run) > int(new_run)):
            payload["pipeline"].update(source_run=old_run, status=previous["pipeline"]["status"])
    comparable = lambda value: {key: item for key, item in value.items() if key != "generated_at"}
    reference = previous if issue else active
    payload["generated_at"] = (
        reference["generated_at"] if reference and comparable(reference) == comparable(payload)
        else datetime.now(timezone.utc).isoformat())
    active_issue = active.get("pipeline", {}).get("issue_number")
    if issue is None and active_issue is not None:
        payload = active
    elif issue is not None and active_issue is not None and active_issue > issue:
        active_payload = active
    else:
        active_payload = payload
    if issue is None:
        active_payload = payload
    check_public(active_payload)
    output = Path(output).resolve()
    if output.exists() or output.is_relative_to(research.ROOT.resolve()):
        raise ValueError("Dashboard output must be a new directory outside the source checkout.")
    template = research.ROOT / "dashboard"
    shutil.copytree(template, output)
    source = {"repository": repository, "branch": BRANCH, "path": DATA_PATH,
              "poll_seconds": 60, "public": metadata.get("private") is False}
    (output / "assets" / "dashboard-data.json").write_bytes(encoded(active_payload))
    (output / "assets" / "dashboard-data.js").write_bytes(b"window.DASHBOARD_DATA = " + encoded(active_payload) + b";\n")
    (output / "assets" / "dashboard-source.js").write_bytes(b"window.DASHBOARD_SOURCE = " + encoded(source) + b";\n")
    files = {}
    for path in output.rglob("*"):
        if path.is_symlink():
            raise ValueError("Dashboard template cannot contain symlinks.")
        if path.is_file():
            files["dashboard/" + path.relative_to(output).as_posix()] = path.read_bytes()
    if case_path:
        files[case_path] = encoded(payload)
    changed = {}
    for path, content in files.items():
        blob = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
        if tree.get(path, {}).get("sha") != blob:
            changed[path] = content
    if changed:
        if not exists:
            _write_api(repository, "git/refs", {"ref": "refs/heads/" + BRANCH, "sha": head})
        head = _commit_files(repository, BRANCH, head, changed, "Update aggregate stage dashboard")
    return {"status": "updated" if changed else "unchanged", "issue": issue,
            "stage": active_payload["pipeline"]["stage"], "head": head,
            "branch_url": f"https://github.com/{repository}/tree/{BRANCH}/dashboard",
            "pages_deployed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("target", "publish"))
    parser.add_argument("--kind", choices=("issue", "pull_request", "evaluation_run"),
                        default=os.environ.get("TARGET_KIND"))
    parser.add_argument("--number", default=os.environ.get("TARGET_NUMBER"))
    parser.add_argument("--evaluation-attempt")
    parser.add_argument("--issue-number", default=os.environ.get("REQUESTED_ISSUE"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if os.environ.get("GITHUB_ACTIONS") != "true":
        parser.error("Dashboard publication is restricted to Actions.")
    try:
        if args.mode == "target":
            ready = os.environ.get("TARGET_READY", "true")
            if ready not in ("true", "false"):
                raise ValueError("Dashboard target readiness must be explicit.")
            write_target(args.kind, args.number, args.evaluation_attempt, ready=ready == "true")
            return
        if args.output is None:
            raise ValueError("Dashboard publication requires an output directory.")
        result = publish(
            os.environ["GITHUB_REPOSITORY"], read_json(Path(os.environ["GITHUB_EVENT_PATH"])),
            os.environ["GITHUB_EVENT_NAME"], args.output, args.issue_number)
        if os.environ.get("GITHUB_OUTPUT"):
            with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as handle:
                handle.write("ready=" + ("false" if result["status"] == "not_applicable" else "true") + "\n")
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as handle:
                handle.write("## Stage dashboard\n\n" + json.dumps(result, ensure_ascii=False) + "\n")
        print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        parser.error("Dashboard source, aggregate or publication validation failed. "
                     "No patient rows or predictions are logged.")


if __name__ == "__main__":
    main()
