"""Read a merged research plan, infer once, and publish aggregate metrics only."""

import argparse
import json
import os
from pathlib import Path
import re
import tempfile

from app import executor_eda, executor_eval, inference, input_contracts, planning_eda, planning_eval, research
from app.asset_contract import canonical_hash, read_json, validate_image_reference, write_json
from app.github_io import git_sha, read_file, read_object, read_tree


REPORT_NAMES = (
    "request.json", "selection.json", "ai-recommendations.json", "eda.json",
    "eda-plan.json", "evaluation-plan.json", "execution-plan.json",
    "input-schema.json", "input-mapping.json",
)


def validate_pull(pull, repository, number, default):
    branch = pull.get("head", {}).get("ref", "")
    match = re.fullmatch(r"research/review-issue-([1-9][0-9]{0,9})", branch)
    if (type(pull.get("number")) is not int or pull["number"] != number
            or pull.get("state") != "closed" or pull.get("merged") is not True or not match
            or pull.get("head", {}).get("repo", {}).get("full_name") != repository
            or pull.get("base", {}).get("repo", {}).get("full_name") != repository
            or pull.get("base", {}).get("ref") != default
            or not isinstance(pull.get("title"), str)
            or not pull["title"].startswith(f"연구검토 #{match.group(1)}:")):
        raise ValueError("Evaluation requires the exact merged research PR in this repository.")
    return int(match.group(1)), git_sha(pull.get("merge_commit_sha"))


def read_reports(repository, merge, issue, api):
    tree = read_tree(repository, merge, api)
    return {
        name: read_object(read_file(repository, tree, f"research/reports/issue-{issue}/{name}", api))
        for name in REPORT_NAMES
    }


def verify_code(code):
    result = research.command([
        "git", "diff", "--quiet", git_sha(code), "HEAD", "--", "app", "config",
        "requirements-inference.txt", "requirements-workflow.txt", ".github/workflows/evaluate-model.yml",
    ], cwd=research.ROOT, allowed=(0, 1))
    if result.returncode:
        raise ValueError("The merged plan is stale for the current execution code or asset configuration.")


def validate_reports(reports, config, repository, issue, *, require_complete=True):
    request, execution, eda, binding = (reports[name] for name in (
        "request.json", "execution-plan.json", "eda.json", "input-mapping.json"))
    if (request.get("repository") != repository or request.get("issue_number") != issue
            or request.get("issue_url") != f"https://github.com/{repository}/issues/{issue}"):
        raise ValueError("Merged report ownership does not match this research Issue.")
    if config.get("schema_version") != 1:
        raise ValueError("Unsupported inference asset configuration.")
    for key in ("data_image", "model_image"):
        if execution.get(key) != validate_image_reference(config.get(key)):
            raise ValueError("Merged plan and trusted inference image digests differ.")
    planning_eda.validate_execution_plan(execution)
    manifest = binding["source_manifest"]
    profile = input_contracts.profile_for_data(manifest)
    schema = reports["input-schema.json"]
    if (binding.get("profile_snapshot") != profile
            or binding.get("profile_hash") != canonical_hash(profile)
            or binding.get("schema_request") != schema or binding.get("status") != "validated"):
        raise ValueError("Merged input mapping metadata differs from its declared source.")
    context = input_contracts.planning_context(manifest, profile, binding, schema)
    if planning_eda.input_signature(context) != execution["input_signature"]:
        raise ValueError("Merged execution plan and input mapping signatures differ.")
    ids = [call["tool_id"] for call in execution["calls"]]
    research.validate_aggregate(eda, context["data_manifest"]["prepared_source"], ids)
    if (eda.get("execution_plan_hash") != execution["plan_hash"]
            or eda.get("execution_result_hash") != executor_eda.execution_result_hash(eda)
            or eda.get("input_mapping_hash") != execution["input_signature"]["input_mapping_hash"]):
        raise ValueError("Merged EDA and execution-plan hashes differ.")
    rebuilt = planning_eval.build_plans(
        eda, context["data_manifest"], reports["selection.json"], reports["ai-recommendations.json"])
    if (rebuilt["eda"] != reports["eda-plan.json"]
            or rebuilt["evaluation"] != reports["evaluation-plan.json"]
            or rebuilt["evaluation"]["ai_status"] != "provided"
            or (require_complete and rebuilt["evaluation"]["planning_complete"] is not True)):
        raise ValueError("Merged evaluation plan is incomplete or differs from its saved selection and evidence.")
    return manifest


def evaluate(data_assets, model_assets, reports, config, provenance, split="validation"):
    issue = reports["request.json"]["issue_number"]
    expected = validate_reports(reports, config, provenance["repository"], issue)
    prediction = inference.predict(data_assets, model_assets, split, expected_manifest=expected)
    metrics = executor_eval.execute_plan(reports["evaluation-plan.json"], prediction["context"])
    return {
        "schema_version": 1, "kind": "fixed-model-evaluation", "provenance": provenance,
        "data_image": config["data_image"], "model_image": config["model_image"],
        "data_id": prediction["data_manifest"]["data_id"],
        "model_id": prediction["model_manifest"]["model_id"],
        "plan_hash": canonical_hash(reports["evaluation-plan.json"]),
        "eda_execution_result_hash": reports["eda.json"]["execution_result_hash"],
        "eda_assessment_status": reports["eda.json"]["assessment_status"],
        "inference": prediction["summary"],
        "selected_ids": reports["evaluation-plan.json"]["selected_ids"],
        "primary_metric": reports["evaluation-plan.json"]["primary_metric"],
        "metrics": metrics, "clinical_use_approved": False,
        "limitations": result_limitations(prediction["data_manifest"]),
    }


def result_limitations(manifest):
    source = manifest.get("prepared_source", {})
    profile = input_contracts.profile_for_data(manifest)
    return list(dict.fromkeys([*source.get("limitations", []), *profile["limitations"]])) + [
        "Frozen prepared features only; no new training, preprocessing or split.",
        "Patient overlap and development reuse of validation remain; this is not an independent clinical validation.",
        "Thresholds, reference predictions and clinical actions are never invented.",
    ]


def markdown(result):
    lines = [
        "# 고정 모델 추론·평가 결과", "",
        f"- 분할: `{result['inference']['split']}` / 행수: `{result['inference']['rows']}`",
        f"- 원본 EDA 판정: `{result['eda_assessment_status']}`",
        f"- 주지표: `{result['primary_metric']}` / 계획 hash: `{result['plan_hash']}`",
        "- 새 학습·전처리·재분할·임상 사용 승인: 수행하지 않음",
        "- 확률·정답·식별자·특성 벡터는 게시하지 않습니다.", "",
        "## 출처", "", "```json", json.dumps(result["provenance"], ensure_ascii=False, indent=2), "```",
    ]
    for item in result["metrics"]:
        lines += ["", f"## {item['block']} · {item['status']}", "", "```json",
                  json.dumps({"value": item["value"], "details": item["details"]},
                             ensure_ascii=False, indent=2, allow_nan=False), "```"]
    lines += ["", "## 해석 한계", "", *("- " + research.cell(value) for value in result["limitations"]), ""]
    return "\n".join(lines)


def run(repository, number, output, split="validation"):
    repository = research.repository_name(repository)
    number = research.issue_number(number)
    default = research.gh_json(f"repos/{repository}")["default_branch"]
    pull = research.gh_json(f"repos/{repository}/pulls/{number}")
    issue, merge = validate_pull(pull, repository, number, default)
    reports = read_reports(repository, merge, issue, research.gh_json)
    config = read_json(research.ROOT / "config" / "assets.json")
    validate_reports(reports, config, repository, issue)
    verify_code(reports["execution-plan.json"]["code_commit"])
    ancestry = research.command(["git", "merge-base", "--is-ancestor", merge, "HEAD"],
                                cwd=research.ROOT, allowed=(0, 1))
    if ancestry.returncode:
        raise ValueError("The merged research commit is not on the trusted default-branch checkout.")
    provenance = {
        "repository": repository, "pull_request": number, "issue_number": issue, "merge_commit": merge,
        "plan_code_commit": reports["execution-plan.json"]["code_commit"],
        "execution_code_commit": git_sha(research.command(
            ["git", "rev-parse", "HEAD"], cwd=research.ROOT).stdout.strip()),
        "workflow_run_url": f"https://github.com/{repository}/actions/runs/"
                            + research.run_identifier(os.environ.get("GITHUB_RUN_ID")),
    }
    with tempfile.TemporaryDirectory(prefix="fixed-model-evaluation-") as temporary:
        private = Path(temporary)
        research.image_assets(config["data_image"], private / "data")
        research.image_assets(config["model_image"], private / "model")
        result = evaluate(private / "data", private / "model", reports, config, provenance, split)
    output = Path(output)
    if output.exists():
        raise ValueError("Evaluation output must be a new aggregate-only directory.")
    output.mkdir(parents=True)
    write_json(output / "evaluation.json", result)
    text = markdown(result)
    (output / "report.md").write_text(text, encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as handle:
            handle.write(text)
    return {"status": "completed", "rows": result["inference"]["rows"],
            "split": split, "selected_ids": result["selected_ids"], "clinical_use_approved": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pull-request", required=True, type=research.issue_number)
    parser.add_argument("--split", choices=("validation", "test"), default="validation")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if os.environ.get("GITHUB_ACTIONS") != "true":
        parser.error("Remote evaluation is restricted to the Actions runner.")
    try:
        result = run(os.environ["GITHUB_REPOSITORY"], args.pull_request, args.output, args.split)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        reason = str(error) if type(error) in (ValueError, RuntimeError) else "Input, model or API contract failed."
        parser.error(reason + " No private rows or predictions are logged.")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
