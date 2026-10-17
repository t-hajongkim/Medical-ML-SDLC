"""Publish an aggregate-only PR2 from the exact successful model evaluation."""

import argparse
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import re
from urllib.parse import quote

from app import executor_eval, model_evaluation, research
from app.asset_contract import canonical_hash, read_json
from app.github_io import git_sha, read_file, read_object, read_tree
from app.review_eval_plan import _commit_files, _write_api


RESULT_FIELDS = {
    "schema_version", "kind", "provenance", "data_image", "model_image", "data_id", "model_id",
    "plan_hash", "eda_execution_result_hash", "eda_assessment_status", "inference",
    "selected_ids", "primary_metric", "metrics", "clinical_use_approved", "limitations",
}
WORKFLOW_PATH = ".github/workflows/evaluate-model.yml"
WORKFLOW_NAME = "Evaluate merged research plan"


def ancestor(earlier, later):
    result = research.command(
        ["git", "merge-base", "--is-ancestor", git_sha(earlier), git_sha(later)],
        cwd=research.ROOT, allowed=(0, 1))
    if result.returncode:
        raise ValueError("Evaluation provenance is not on the trusted default-branch history.")


def validate_source(source, repository, run, attempt):
    if (str(source.get("id")) != research.run_identifier(run)
            or str(source.get("run_attempt")) != research.run_identifier(attempt)
            or source.get("path") != WORKFLOW_PATH or source.get("name") != WORKFLOW_NAME
            or source.get("event") not in ("pull_request", "workflow_dispatch")
            or source.get("status") != "completed" or source.get("conclusion") != "success"
            or source.get("repository", {}).get("full_name") != repository
            or source.get("head_repository", {}).get("full_name") != repository):
        raise ValueError("PR2 requires the exact successful evaluation workflow in this repository.")
    workflow = research.gh_json(f"repos/{repository}/actions/workflows/evaluate-model.yml")
    if (type(source.get("workflow_id")) is not int or source["workflow_id"] != workflow.get("id")
            or workflow.get("path") != WORKFLOW_PATH or workflow.get("name") != WORKFLOW_NAME
            or workflow.get("state") != "active"):
        raise ValueError("The source is not the registered evaluation workflow.")
    model_evaluation.verify_code(git_sha(source.get("head_sha")))
    checkout = git_sha(research.command(
        ["git", "rev-parse", "HEAD"], cwd=research.ROOT).stdout.strip())
    linked = source.get("pull_requests", [])
    if source["event"] == "pull_request" and linked:
        if len(linked) != 1:
            raise ValueError("The evaluation source must identify one merged research PR.")
        number = research.issue_number(linked[0].get("number"))
        default = research.gh_json(f"repos/{repository}")["default_branch"]
        pull = research.gh_json(f"repos/{repository}/pulls/{number}")
        _, merge = model_evaluation.validate_pull(pull, repository, number, default)
        if source["head_sha"] not in (merge, pull.get("head", {}).get("sha")):
            raise ValueError("The evaluation source does not match the merged research PR.")
        ancestor(merge, checkout)
    else:
        ancestor(source["head_sha"], checkout)
    # A squash replaces the PR head; the actual merged commit must still be trusted.
    return source


def resolve_source(repository, run, attempt):
    repository = research.repository_name(repository)
    source = research.gh_json(f"repos/{repository}/actions/runs/{research.run_identifier(run)}")
    validate_source(source, repository, run, attempt)
    artifacts = research.gh_json(f"repos/{repository}/actions/runs/{run}/artifacts?per_page=100")
    if (not isinstance(artifacts.get("artifacts"), list)
            or artifacts.get("total_count") != len(artifacts["artifacts"])):
        raise ValueError("The exact source artifact list is incomplete.")
    matches = [item for item in artifacts["artifacts"]
               if item.get("name") == f"model-evaluation-{run}-{attempt}"]
    if (len(matches) != 1 or matches[0].get("expired") is not False
            or type(matches[0].get("id")) is not int or matches[0]["id"] < 1
            or matches[0].get("workflow_run", {}).get("id") != int(run)
            or matches[0]["workflow_run"].get("head_sha") != source["head_sha"]):
        raise ValueError("A unique, unexpired artifact from this exact run/attempt is required.")
    return {**source, "artifact_id": matches[0]["id"]}


def model_identity(manifest):
    source = manifest["prepared_source"]
    model = source["model"]
    return canonical_hash({
        "schema_version": 2, "kind": "model-artifact", "data_id": manifest["data_id"],
        "model_source": model, "raw_source_sha256": manifest["raw_source_sha256"],
        "feature_columns": manifest["feature_columns"],
        "input_contract": "already-standardized ordered prepared features; no additional scaler",
        "target_mapping": source["target_mapping"], "classes": [0, 1], "positive_class": 1,
        "training_performed": False, "inference_performed_by_publisher": False,
        "files": {"logistic_regression.pkl": model["checkpoint"]["sha256"],
                  "MODEL_SOURCE_README.txt": model["readme"]["sha256"],
                  "NOTICE.txt": manifest["files"]["NOTICE.txt"]},
    })


def read_result(directory):
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir() or {
            path.name for path in directory.iterdir()} != {"evaluation.json", "report.md"}:
        raise ValueError("PR2 input must contain only the two aggregate evaluation files.")
    for name in ("evaluation.json", "report.md"):
        path = directory / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_000_000:
            raise ValueError("PR2 aggregate files must be bounded regular files.")
    result = read_object((directory / "evaluation.json").read_bytes())
    if set(result) != RESULT_FIELDS:
        raise ValueError("PR2 result fields do not match the aggregate-only contract.")
    if (directory / "report.md").read_text(encoding="utf-8") != model_evaluation.markdown(result):
        raise ValueError("PR2 Markdown differs from the structured evaluation result.")
    return result


def validate_result(result, repository, config, default, run, source):
    if (type(result.get("schema_version")) is not int or result["schema_version"] != 1
            or result.get("kind") != "fixed-model-evaluation"
            or result.get("clinical_use_approved") is not False):
        raise ValueError("PR2 requires an educational fixed-model aggregate result.")
    origin = result["provenance"]
    if (set(origin) != {"repository", "pull_request", "issue_number", "merge_commit",
                       "plan_code_commit", "execution_code_commit", "workflow_run_url"}
            or origin["repository"] != repository
            or origin["workflow_run_url"] != f"https://github.com/{repository}/actions/runs/{run}"):
        raise ValueError("PR2 result provenance does not match the evaluation run.")
    number = research.issue_number(origin["pull_request"])
    pull = research.gh_json(f"repos/{repository}/pulls/{number}")
    issue, merge = model_evaluation.validate_pull(pull, repository, number, default)
    if origin["issue_number"] != issue or origin["merge_commit"] != merge:
        raise ValueError("PR2 does not match the actual merged research PR.")
    if source["event"] == "pull_request":
        linked = source.get("pull_requests", [])
        if (linked and (len(linked) != 1 or linked[0].get("number") != number)
                or source["head_sha"] not in (merge, pull.get("head", {}).get("sha"))):
            raise ValueError("The evaluation source run belongs to a different research PR.")
        if result["inference"]["split"] != "validation":
            raise ValueError("Automatic merged-PR evaluation only selects validation.")
    execution = git_sha(origin["execution_code_commit"])
    model_evaluation.verify_code(execution)
    checkout = git_sha(research.command(
        ["git", "rev-parse", "HEAD"], cwd=research.ROOT).stdout.strip())
    ancestor(execution, checkout)
    ancestor(merge, execution)
    reports = model_evaluation.read_reports(repository, merge, issue, research.gh_json)
    manifest = model_evaluation.validate_reports(reports, config, repository, issue)
    model_evaluation.verify_code(git_sha(reports["execution-plan.json"]["code_commit"]))
    evaluation = reports["evaluation-plan.json"]
    if (origin["plan_code_commit"] != reports["execution-plan.json"]["code_commit"]
            or result["plan_hash"] != canonical_hash(evaluation)
            or result["eda_execution_result_hash"] != reports["eda.json"]["execution_result_hash"]
            or result["eda_assessment_status"] != reports["eda.json"]["assessment_status"]
            or result["data_id"] != manifest["data_id"]
            or any(result[key] != config[key] for key in ("data_image", "model_image"))
            or result["model_id"] != model_identity(manifest)
            or result["selected_ids"] != evaluation["selected_ids"]
            or result["primary_metric"] != evaluation["primary_metric"]
            or result["limitations"] != model_evaluation.result_limitations(manifest)):
        raise ValueError("PR2 result differs from its approved plan, EDA or fixed asset contract.")
    summary = result["inference"]
    if (set(summary) != {"split", "rows", "feature_count", "positive_class", "model_class",
                        "runtime_scikit_learn", "training_performed", "preprocessing_performed",
                        "inference_performed"}
            or summary["split"] not in ("validation", "test")
            or type(summary["rows"]) is not int or summary["rows"] != manifest["split_rows"][summary["split"]]
            or type(summary["feature_count"]) is not int
            or summary["feature_count"] != len(manifest["feature_columns"])
            or type(summary["positive_class"]) is not int or summary["positive_class"] != 1
            or summary["model_class"] != "sklearn.linear_model.LogisticRegression"
            or summary["runtime_scikit_learn"] != "1.2.2"
            or summary["training_performed"] is not False
            or summary["preprocessing_performed"] is not False
            or summary["inference_performed"] is not True):
        raise ValueError("PR2 inference summary violates the frozen evaluation contract.")
    if not isinstance(result["metrics"], list) or len(result["metrics"]) != len(result["selected_ids"]):
        raise ValueError("PR2 metrics do not match the selected evaluation blocks.")
    validate_metrics(result, evaluation)
    return issue, number, summary["split"]


def validate_metrics(result, plan):
    entries, _ = executor_eval._registry()
    parameters = dict(plan["parameters"])
    if plan["threshold"] is not None:
        parameters["threshold"] = plan["threshold"]
    rows = result["inference"]["rows"]
    for item, identifier in zip(result["metrics"], result["selected_ids"]):
        checked = deepcopy(item)
        # The executor strips these positions before publication, but its raw
        # registry validator still requires that field for completed top-k blocks.
        if identifier in ("recall_at_k", "precision_at_k") and "selected_indices" not in checked["details"]:
            checked["details"]["selected_indices"] = []
        if executor_eval._aggregate(checked, identifier) != item:
            raise ValueError("PR2 cannot contain row-level metric outputs.")
        details = item["details"]
        entry = entries[identifier]
        missing = [name for name in entry["requires_parameters"] if parameters.get(name) is None]
        context = [name for name in entry["requires_context"] if plan.get(name) is None]
        expected = None
        if identifier == "categorical_nri":
            expected = {"reason": "missing_inputs", "missing_inputs": ["reference_probabilities"]}
        elif missing:
            expected = {"reason": "missing_parameters", "missing_parameters": [missing[0]]}
        elif context:
            expected = {"reason": "missing_context", "missing_context": [context[0]]}
        if expected is not None:
            if item["status"] != "not_applicable" or details != expected:
                raise ValueError("Metric result invents a missing approved prerequisite.")
            continue
        if (details.get("sample_count") != rows
                or any(details[name] != parameters.get(name) for name in
                       ("threshold", "top_fraction", "risk_thresholds") if name in details)
                or "n_bins" in details and details["n_bins"] != parameters.get("n_bins", 10)
                or any(details[name] != plan[name] for name in
                       ("clinical_action", "comparison_model") if name in details)):
            raise ValueError("Metric aggregates differ from the saved plan or inference row count.")
        reason = details.get("reason")
        if item["status"] == "not_applicable":
            if (reason == "zero_denominator" and details.get("denominator") == 0
                    or reason == "both_classes_required" and details.get("positive_count") in (0, rows)
                    or reason == "positive_class_required" and details.get("positive_count") == 0):
                continue
            raise ValueError("Metric not_applicable result has no valid aggregate justification.")
        if "counts" in details:
            counts = details["counts"]
            if sum(counts.values()) != rows:
                raise ValueError("Threshold metric counts do not cover the inference rows.")
            if identifier == "confusion_matrix" and item["value"] != {
                    **counts, "labels": [0, 1],
                    "matrix": [[counts["tn"], counts["fp"]], [counts["fn"], counts["tp"]]]}:
                raise ValueError("Confusion matrix differs from its aggregate counts.")
        if "numerator" in details:
            denominator = details["denominator"]
            if not denominator or not math.isclose(
                    item["value"], details["numerator"] / denominator, rel_tol=1e-12, abs_tol=1e-12):
                raise ValueError("Metric value differs from its aggregate numerator and denominator.")
        if identifier == "decision_curve" and [
                row["threshold"] for row in item["value"]] != parameters["thresholds"]:
            raise ValueError("Decision curve differs from the approved thresholds.")


def semantic_result(result):
    value = deepcopy(result)
    value["provenance"].pop("workflow_run_url", None)
    return value


def pr_body(result, repository, directory, branch):
    origin = result["provenance"]
    lines = [
        "# 고정 모델 평가 결과 검토", "",
        f"승인한 계획: https://github.com/{repository}/pull/{origin['pull_request']}",
        f"연구 요청: https://github.com/{repository}/issues/{origin['issue_number']}",
        f"평가 분할: `{result['inference']['split']}` / 행수: `{result['inference']['rows']}`",
        f"주지표: `{result['primary_metric']}` / 원본 EDA 판정: `{result['eda_assessment_status']}`",
        "", "| 지표 | 계산 상태 | 값 / 사유 |", "|---|---|---|",
        *(f"| `{item['block']}` | `{item['status']}` | "
          + research.cell(json.dumps(item["value"], ensure_ascii=False)
                          if item["status"] == "completed" else
                          json.dumps(item["details"], ensure_ascii=False)) + " |"
          for item in result["metrics"]),
        "", "임계값·참조 확률 등 선행조건이 없으면 not_applicable로 남으며 수치를 만들지 않습니다.",
        "PR1의 계획상 blocked와 실제 계산의 not_applicable은 다른 상태입니다.",
        "새 학습·전처리·재분할은 수행하지 않았으며, 사람의 결과 검토를 기다립니다.",
        "조건부 등록된 교육용 모델이며 원래 전처리를 독립 재현한 결과는 아닙니다.",
        "환자 중복·validation 개발 재사용 한계를 보존합니다. 결과 검토가 임상 사용 승인은 아닙니다.",
        "", f"[전체 보고서](https://github.com/{repository}/blob/{branch}/{directory}/report.md)",
        f"[구조화 집계](https://github.com/{repository}/blob/{branch}/{directory}/evaluation.json)",
        f"실행 근거: {origin['workflow_run_url']}",
    ]
    return "\n".join(lines)


def publish(repository, directory, run, attempt="1"):
    repository = research.repository_name(repository)
    run = research.run_identifier(run)
    source = resolve_source(repository, run, attempt)
    result = read_result(directory)
    config = read_json(research.ROOT / "config" / "assets.json")
    default = research.gh_json(f"repos/{repository}")["default_branch"]
    issue, pr1, split = validate_result(result, repository, config, default, run, source)
    branch = f"research/result-issue-{issue}-{split}"
    report_dir = f"research/results/issue-{issue}/{split}"
    pull, deferred = research.existing_pull(repository, branch, default)
    if deferred:
        return deferred
    title = f"평가결과 #{issue}: 고정 모델 · {split}"
    base = git_sha(research.gh_json(
        f"repos/{repository}/git/ref/heads/{quote(default, safe='/')}")["object"]["sha"])
    model_evaluation.verify_code(base)
    refs = research.gh_json(f"repos/{repository}/git/matching-refs/heads/{branch}")
    exact = [item for item in refs if item.get("ref") == "refs/heads/" + branch]
    if len(exact) > 1:
        raise ValueError("The result branch is ambiguous.")
    head = git_sha(exact[0]["object"]["sha"]) if exact else base
    paths = {report_dir + "/evaluation.json", report_dir + "/report.md"}
    previous = None
    previous_files = None
    if exact:
        changed = research.gh_json(f"repos/{repository}/compare/{base}...{head}")["files"]
        if any(item["filename"] not in paths or item.get("previous_filename", item["filename"]) not in paths
               or item.get("status") in ("removed", "renamed") for item in changed):
            raise ValueError("The result branch contains unrelated changes; it was preserved.")
        tree = read_tree(repository, head, research.gh_json)
        previous_files = {name: read_file(repository, tree, report_dir + "/" + name,
                                        research.gh_json, optional=True, max_bytes=1_000_000)
                          for name in ("evaluation.json", "report.md")}
        raw = previous_files["evaluation.json"]
        previous = read_object(raw) if raw is not None else None
        if previous is None:
            raise ValueError("Existing result branch has no matching ownership record.")
        if previous is not None:
            if (set(previous) != RESULT_FIELDS or previous_files["report.md"] is None
                    or previous_files["report.md"] != model_evaluation.markdown(previous).encode("utf-8")):
                raise ValueError("The existing result files were edited or are incomplete; they were preserved.")
            origin = previous.get("provenance", {})
            if (origin.get("repository") != repository or origin.get("pull_request") != pr1
                    or origin.get("issue_number") != issue
                    or previous.get("inference", {}).get("split") != split):
                raise ValueError("Existing result branch belongs to a different research evaluation.")
            match = re.fullmatch(
                rf"https://github\.com/{re.escape(repository)}/actions/runs/([1-9][0-9]*)",
                origin.get("workflow_run_url", ""))
            if not match:
                raise ValueError("The previous result has no valid source run.")
            previous_run = match.group(1)
            previous_source = source if previous_run == run else research.gh_json(
                f"repos/{repository}/actions/runs/{previous_run}")
            if previous_run != run:
                validate_source(previous_source, repository, previous_run,
                                str(previous_source.get("run_attempt")))
            validate_result(previous, repository, config, default, previous_run, previous_source)
            if int(previous_run) > int(run):
                raise ValueError("An older evaluation run cannot overwrite a newer result.")
    if pull and (not exact or pull["head"]["sha"] != head):
        raise ValueError("The existing result PR changed; start a fresh publication attempt.")
    changed = previous is None or semantic_result(previous) != semantic_result(result)
    if not changed:
        result = previous
    else:
        result = json.loads(json.dumps(result, sort_keys=True, ensure_ascii=False, allow_nan=False))
    files = {
        report_dir + "/evaluation.json": (
            json.dumps(result, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8"),
        report_dir + "/report.md": model_evaluation.markdown(result).encode("utf-8"),
    }
    if not changed:
        files = {report_dir + "/" + name: content for name, content in previous_files.items()}
    changed = previous_files is None or any(
        content != previous_files[path.rsplit("/", 1)[1]] for path, content in files.items())
    generated = pr_body(result, repository, report_dir, branch)
    latest, deferred = research.existing_pull(repository, branch, default)
    if deferred:
        return deferred
    if ((pull is None) != (latest is None)
            or pull and latest["number"] != pull["number"]):
        raise ValueError("The result PR changed during publication; retry after reconciliation.")
    pull = latest
    if pull:
        validate_result_pull(pull, repository, branch, default, head)
    body = research.merge_auto_body((pull or {}).get("body") or "", generated)
    if exact and git_sha(research.gh_json(
            f"repos/{repository}/git/ref/heads/{branch}")["object"]["sha"]) != head:
        raise ValueError("The result branch changed during publication.")
    if not exact:
        _write_api(repository, "git/refs", {"ref": "refs/heads/" + branch, "sha": base})
    if changed:
        head = _commit_files(repository, branch, head, files,
                             f"Record fixed evaluation for Issue #{issue} ({split})")
    latest, deferred = research.existing_pull(repository, branch, default)
    if deferred:
        return deferred
    if latest:
        validate_result_pull(latest, repository, branch, default, head)
        if pull and latest["number"] != pull["number"]:
            raise ValueError("The result PR changed during publication.")
        pull = latest
        body = research.merge_auto_body(pull.get("body") or "", generated)
    elif pull:
        raise ValueError("The result PR disappeared during publication.")
    if pull is None:
        try:
            created = _write_api(repository, "pulls", {
                "title": title, "head": branch, "base": default, "body": body, "draft": True})
        except RuntimeError:
            recovered, deferred = research.existing_pull(repository, branch, default)
            if deferred:
                return deferred
            if recovered is None:
                raise RuntimeError("Evaluation succeeded but PR2 publication failed; the branch is retained.") from None
            validate_result_pull(recovered, repository, branch, default, head)
            created = recovered
        number = research.issue_number(created["number"])
        status = "created"
    else:
        number = research.issue_number(pull["number"])
        body_changed = body != (pull.get("body") or "")
        if body_changed:
            _write_api(repository, f"pulls/{number}", {"body": body}, method="PATCH")
        status = "updated" if changed or body_changed else "unchanged"
    return {"status": status, "pull_url": f"https://github.com/{repository}/pull/{number}",
            "source_pull_request": pr1, "split": split, "clinical_use_approved": False}


def validate_result_pull(pull, repository, branch, default, head):
    if (pull.get("state") != "open" or pull.get("head", {}).get("ref") != branch
            or pull.get("head", {}).get("sha") != head
            or pull.get("head", {}).get("repo", {}).get("full_name") != repository
            or pull.get("base", {}).get("ref") != default
            or pull.get("base", {}).get("repo", {}).get("full_name") != repository):
        raise ValueError("The result PR target or head changed; publication was stopped.")
    research.issue_number(pull.get("number"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("resolve-source", "publish"))
    parser.add_argument("--run-id", required=True, type=research.run_identifier)
    parser.add_argument("--attempt", required=True, type=research.run_identifier)
    parser.add_argument("--directory", type=Path)
    args = parser.parse_args()
    if os.environ.get("GITHUB_ACTIONS") != "true":
        parser.error("PR2 publication is restricted to the Actions runner.")
    try:
        repository = research.repository_name(os.environ["GITHUB_REPOSITORY"])
        if args.mode == "resolve-source":
            source = resolve_source(repository, args.run_id, args.attempt)
            with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as handle:
                handle.write(f"artifact=model-evaluation-{args.run_id}-{args.attempt}\n")
                handle.write(f"artifact_id={source['artifact_id']}\n")
            return
        if args.directory is None:
            raise ValueError("PR2 publication requires the exact aggregate artifact directory.")
        print(json.dumps(publish(repository, args.directory, args.run_id, args.attempt),
                         ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        parser.error("PR2 source, aggregate, branch or API validation failed. "
                     "No patient rows or prediction vectors are logged.")


if __name__ == "__main__":
    main()
