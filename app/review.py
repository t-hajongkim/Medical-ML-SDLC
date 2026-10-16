"""Trusted comment selections and immutable, educational-only merge approvals.

Only /plan lines are interpreted. Edits to processed comments require a new
comment; their bodies are never copied into the audit record or executed.
"""

import base64
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import uuid
from urllib.parse import quote

from app import planning, research
from app.asset_contract import canonical_hash, validate_image_reference


EDIT_POLICY = "처리한 댓글의 편집은 다시 적용하지 않습니다. 변경하려면 새 /plan 댓글을 작성하세요."
STAGES = ("eda", "evaluation")
REPORT_FILES = ("request.json", "eda.json", "eda-plan.json", "evaluation-plan.json",
                "selection.json", "ai-recommendations.json", "recommendation-request.json")
CONFIG_FILES = ("config/assets.json", "config/prepared-source.json",
                "config/eda-catalog.json", "config/metric-catalog.json",
                "config/planning-policy.json")


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, indent=2) + "\n"


def _hash(content):
    return hashlib.sha256(content).hexdigest()


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError("40자리 Git commit SHA가 필요합니다.")
    return value


def _object(content):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("JSON 중복 키는 허용하지 않습니다.")
            result[key] = value
        return result
    try:
        result = json.loads(content.decode("utf-8"), object_pairs_hook=unique,
                            parse_constant=lambda _: (_ for _ in ()).throw(
                                ValueError("JSON 유한 숫자만 허용합니다.")))
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError("검토 파일이 올바른 UTF-8 JSON이 아닙니다.") from None
    if not isinstance(result, dict):
        raise ValueError("검토 파일은 JSON 객체여야 합니다.")
    return result


def _write_api(repository, endpoint, payload, method="POST"):
    result = research.command(
        ["gh", "api", "--method", method, f"repos/{repository}/{endpoint}", "--input", "-"],
        input_text=_json(payload))
    return _object(result.stdout.encode("utf-8"))


def _tree(repository, commit):
    result = research.gh_json(f"repos/{repository}/git/trees/{_sha(commit)}?recursive=1")
    if result.get("truncated") is not False or not isinstance(result.get("tree"), list):
        raise ValueError("잘린 Git tree로는 검토 파일을 확인할 수 없습니다.")
    return {entry["path"]: entry for entry in result["tree"]}


def _file(repository, tree, path, optional=False):
    entry = tree.get(path)
    if entry is None and optional:
        return None
    if (entry is None or entry.get("mode") != "100644" or entry.get("type") != "blob"
            or entry.get("size", 0) > 10_000_000):
        raise ValueError(f"일반 검토 파일이 없거나 안전하지 않습니다: {path}")
    result = research.gh_json(f"repos/{repository}/git/blobs/{_sha(entry['sha'])}")
    if result.get("encoding") != "base64":
        raise ValueError("검토 파일의 Git blob 인코딩이 잘못되었습니다.")
    try:
        content = base64.b64decode("".join(result["content"].split()), validate=True)
    except (ValueError, KeyError, TypeError):
        raise ValueError("검토 파일의 Git blob을 해독할 수 없습니다.") from None
    digest = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
    if len(content) > 10_000_000 or digest != entry["sha"]:
        raise ValueError("검토 파일 Git blob 무결성 확인에 실패했습니다.")
    return content


def _commit_files(repository, branch, parent, files, message):
    """One tree/commit, followed by a non-force ref update; never touch an index."""
    parent = _sha(parent)
    ref = "heads/" + quote(branch, safe="/")
    current = research.gh_json(f"repos/{repository}/git/ref/{ref}")
    if current.get("object", {}).get("sha") != parent:
        raise ValueError("브랜치가 변경되어 저장하지 않았습니다. 새 실행으로 재시도하세요.")
    base = research.gh_json(f"repos/{repository}/git/commits/{parent}")
    entries = []
    for path, content in sorted(files.items()):
        blob = _write_api(repository, "git/blobs", {
            "content": base64.b64encode(content).decode("ascii"), "encoding": "base64"})
        entries.append({"path": path, "mode": "100644", "type": "blob", "sha": _sha(blob["sha"])})
    tree = _write_api(repository, "git/trees", {
        "base_tree": _sha(base["tree"]["sha"]), "tree": entries})
    commit = _write_api(repository, "git/commits", {
        "message": message, "tree": _sha(tree["sha"]), "parents": [parent]})
    try:
        _write_api(repository, "git/refs/" + ref,
                   {"sha": _sha(commit["sha"]), "force": False}, method="PATCH")
    except RuntimeError:
        raise RuntimeError("force=false 브랜치 갱신 실패: 보호 정책 또는 동시 변경을 확인하세요. "
                           "기존 PR·선택·승인 기록은 덮어쓰지 않았습니다.") from None
    return commit["sha"]


def _human_permission(repository, user):
    login = user.get("login")
    if (user.get("type") != "User" or not isinstance(login, str)
            or not re.fullmatch(r"[A-Za-z0-9-]+", login)):
        return False
    result = research.gh_json(f"repos/{repository}/collaborators/{login}/permission")
    return result.get("permission") in ("write", "maintain", "admin")


def _pull(repository, number, default, *, merged=False):
    pull = research.gh_json(f"repos/{repository}/pulls/{number}")
    match = re.fullmatch(r"research/review-issue-([1-9][0-9]{0,9})",
                         pull.get("head", {}).get("ref", ""))
    if (pull.get("number") != number or not match
            or pull.get("head", {}).get("repo", {}).get("full_name") != repository
            or pull.get("base", {}).get("repo", {}).get("full_name") != repository
            or pull.get("base", {}).get("ref") != default
            or not isinstance(pull.get("title"), str)
            or not pull["title"].startswith("연구검토 #")
            or pull.get("state") != ("closed" if merged else "open")
            or (merged and pull.get("merged") is not True)):
        raise ValueError("같은 저장소·생성 브랜치·기본 브랜치의 연구검토 PR만 처리합니다.")
    _sha(pull["head"]["sha"])
    return pull, int(match.group(1))


def _marker(request, repository, issue):
    if (request.get("schema_version") != 1 or request.get("repository") != repository
            or request.get("issue_number") != issue
            or request.get("issue_url") != f"https://github.com/{repository}/issues/{issue}"):
        raise ValueError("request.json의 저장소·Issue·브랜치 소유 마커가 일치하지 않습니다.")


def _catalogs():
    return {stage: planning.load_catalog(stage) for stage in STAGES}


def _selection(selection, catalogs, policy):
    # _selection resolves aliases and validates without importing model/metric code.
    normalized = planning._selection(selection, policy, catalogs)
    if normalized["evaluation"]["threshold"] is not None:
        normalized["evaluation"]["parameters"].pop("threshold", None)
    primary = normalized["evaluation"]["primary_metric"]
    if primary in normalized["evaluation"]["exclude"]:
        raise ValueError("주지표는 제외 목록에 있을 수 없습니다.")
    return normalized


def _identifier(value, stage, catalogs):
    return planning._ids([value], planning._aliases(catalogs[stage], stage), stage)[0]


def plan_lines(body):
    if not isinstance(body, str):
        return []
    lines = [(number, line.strip()) for number, line in enumerate(body.splitlines(), 1)
             if line.strip()]
    if any(not re.match(r"^/plan(?:\s|$)", line) for _, line in lines):
        return []
    return lines


def apply_commands(selection, body, *, catalogs=None, policy=None):
    """Apply each explicit valid command; rejection is separate and never success."""
    catalogs = _catalogs() if catalogs is None else catalogs
    policy = planning.load_policy() if policy is None else policy
    current = _selection(selection, catalogs, policy)
    applied, rejected = [], []
    recommend = False
    for line_number, line in plan_lines(body):
        tokens = line.split()
        candidate = copy.deepcopy(current)
        try:
            action = tokens[1] if len(tokens) > 1 else ""
            record = {"line": line_number, "action": action}
            if action in ("add", "remove") and len(tokens) == 4 and tokens[2] in STAGES:
                stage = tokens[2]
                identifier = _identifier(tokens[3], stage, catalogs)
                record.update(stage=stage, id=identifier)
                if action == "remove":
                    if identifier in policy[stage]["required"] or (
                            stage == "eda" and identifier in ("schema", "patient_overlap")):
                        raise ValueError("필수 EDA/정책 블록은 제외할 수 없습니다.")
                    if stage == "evaluation" and candidate[stage]["primary_metric"] == identifier:
                        raise ValueError("현재 주지표를 제거하려면 먼저 다른 /plan primary를 선택하세요.")
                include, exclude = (("add", "exclude") if action == "add" else ("exclude", "add"))
                candidate[stage][exclude] = [item for item in candidate[stage][exclude]
                                              if item != identifier]
                if identifier not in candidate[stage][include]:
                    candidate[stage][include].append(identifier)
            elif action == "primary" and len(tokens) == 3:
                identifier = _identifier(tokens[2], "evaluation", catalogs)
                metric = candidate["evaluation"]
                metric["primary_metric"] = identifier
                metric["exclude"] = [item for item in metric["exclude"] if item != identifier]
                if identifier not in metric["add"]:
                    metric["add"].append(identifier)
                record["id"] = identifier
            elif action == "threshold" and len(tokens) == 3:
                try:
                    threshold = float(tokens[2])
                except ValueError:
                    raise ValueError("threshold는 유한한 0..1 숫자여야 합니다.") from None
                if not math.isfinite(threshold) or not 0 <= threshold <= 1:
                    raise ValueError("threshold는 유한한 0..1 숫자여야 합니다.")
                candidate["evaluation"]["threshold"] = threshold
                candidate["evaluation"]["parameters"].pop("threshold", None)
                record["value"] = threshold
            elif action == "parameter" and len(tokens) >= 4:
                name = tokens[2]
                if name not in ("thresholds", "risk_thresholds", "top_fraction", "n_bins"):
                    raise ValueError("지원 parameter: thresholds, risk_thresholds, top_fraction, n_bins")
                try:
                    value = json.loads(" ".join(tokens[3:]))
                except json.JSONDecodeError:
                    raise ValueError("parameter 값은 올바른 JSON 숫자 또는 배열이어야 합니다.") from None
                candidate["evaluation"]["parameters"][name] = value
                record.update(parameter=name, value=value)
            elif action == "clinical-action" and len(tokens) >= 3:
                value = " ".join(tokens[2:])
                candidate["evaluation"]["clinical_action"] = value
                record["value"] = value
            elif action == "recommend" and len(tokens) == 2:
                recommend = True
            else:
                raise ValueError("지원 명령: add/remove eda|evaluation ID, primary ID, "
                                 "threshold 0..1, parameter NAME JSON, clinical-action TEXT, recommend")
            current = _selection(candidate, catalogs, policy)
            applied.append(record)
        except ValueError as error:
            # Avoid echoing arbitrary comment text through an unknown-ID error.
            reason = str(error)
            if reason.startswith(("Unknown ", "eda is", "evaluation is")):
                reason = "등록되지 않은 카탈로그 ID입니다."
            rejected.append({"line": line_number, "reason": reason})
    return current, {"applied": applied, "rejected": rejected, "recommend": recommend}


def _comments(repository, number, until):
    comments, page = {}, 1
    while True:
        result = research.gh_json("--method", "GET", f"repos/{repository}/issues/{number}/comments",
                                  "-f", "per_page=100", "-f", f"page={page}")
        if not isinstance(result, list):
            raise ValueError("PR 댓글 목록이 올바르지 않습니다.")
        for comment in result:
            identifier = research.issue_number(comment.get("id"))
            if identifier <= until:
                comments[identifier] = comment
        if len(result) < 100:
            break
        page += 1
    return [comments[key] for key in sorted(comments)]


def _comment(repository, identifier, number):
    comment = research.gh_json(f"repos/{repository}/issues/comments/{identifier}")
    if (comment.get("id") != identifier
            or comment.get("issue_url") != f"https://api.github.com/repos/{repository}/issues/{number}"):
        raise ValueError("API 댓글과 대상 PR이 일치하지 않습니다.")
    return comment


def _refresh(repository, default, actor, issue):
    """Run trusted helper in an owned clone, preserving source HEAD/index/origin."""
    source = research.ROOT
    runner = Path(os.environ["RUNNER_TEMP"]).resolve()
    run = research.run_identifier(os.environ.get("GITHUB_RUN_ID"))
    attempt = research.run_identifier(os.environ.get("GITHUB_RUN_ATTEMPT"))
    directory = runner / f"review-plan-{run}-{attempt}-{uuid.uuid4().hex}"
    if directory.exists() or directory.resolve().is_relative_to(source.resolve()):
        raise ValueError("검토 작업 디렉터리는 체크아웃 외부의 새 경로여야 합니다.")
    if research.command(["git", "status", "--porcelain"], cwd=source).stdout.strip():
        raise ValueError("검토 갱신은 깨끗한 신뢰 체크아웃이 필요합니다.")
    commit = _sha(research.command(["git", "rev-parse", "HEAD"], cwd=source).stdout.strip())
    directory.mkdir(parents=True)
    clone = directory / "checkout"
    try:
        research.command(["git", "clone", "--no-hardlinks", "--no-checkout",
                          str(source), str(clone)])
        research.command(["git", "checkout", "--detach", commit], cwd=clone)
        research.command(["git", "remote", "set-url", "origin",
                          f"https://github.com/{repository}.git"], cwd=clone)
        research.ROOT = clone
        return research.run_workflow(
            {"repository": {"full_name": repository, "default_branch": default}},
            "workflow_dispatch", repository, actor, default, directory / "assets-work",
            manual_number=issue)
    finally:
        research.ROOT = source
        shutil.rmtree(directory)


def _queue_natural_feedback(repository, number, identifier, actor, default, target):
    raw = research.gh_json(f"repos/{repository}/pulls/{number}")
    if not re.fullmatch(r"research/review-issue-[1-9][0-9]*",
                        raw.get("head", {}).get("ref", "")):
        return {"status": "not_applicable", "reason": "연구검토 PR의 자연어 피드백만 처리합니다."}
    if not _human_permission(repository, target.get("user", {})):
        return {"status": "deferred", "reason": "자연어 피드백에는 사람의 write/maintain/admin 권한이 필요합니다."}
    pull, issue = _pull(repository, number, default)
    tree = _tree(repository, pull["head"]["sha"])
    directory = f"research/reports/issue-{issue}/"
    _marker(_object(_file(repository, tree, directory + "request.json")), repository, issue)
    previous = _file(repository, tree, directory + "feedback-requests.json", optional=True)
    ledger = _object(previous) if previous else {
        "schema_version": 1, "kind": "pr1-natural-feedback",
        "repository": repository, "pull_request": number, "issue_number": issue, "comments": []}
    applied_data = _file(repository, tree, directory + "feedback-applied.json", optional=True)
    from app.pr1_intent import validate_feedback_state
    references, applied_ids = validate_feedback_state(
        ledger, _object(applied_data) if applied_data is not None else None,
        repository, number, issue)
    applied = set(applied_ids)
    records = {item["comment_id"]: item for item in references}
    comments = _comments(repository, number, identifier)
    if identifier not in {item["id"] for item in comments}:
        raise ValueError("이벤트 댓글이 현재 PR 댓글 목록에 없습니다.")
    for listed in comments:
        if listed["id"] in applied:
            continue
        comment = target if listed["id"] == identifier else _comment(repository, listed["id"], number)
        body = comment.get("body")
        if (not isinstance(body, str) or not body.strip() or plan_lines(body)
                or not _human_permission(repository, comment.get("user", {}))):
            continue
        record = {"comment_id": comment["id"], "actor": comment["user"]["login"],
                  "body_sha256": _hash(body.encode("utf-8"))}
        old = records.get(comment["id"])
        if old is not None and old["body_sha256"] != record["body_sha256"]:
            record["superseded_hash"] = old["body_sha256"]
        records[comment["id"]] = record
    ledger["comments"] = [records[key] for key in sorted(records)]
    content = _json(ledger).encode("utf-8")
    if content != previous:
        _commit_files(repository, pull["head"]["ref"], pull["head"]["sha"],
                      {directory + "feedback-requests.json": content},
                      f"Queue public research feedback for Issue #{issue}")
    pending = [item["comment_id"] for item in ledger["comments"] if item["comment_id"] not in applied]
    if not pending:
        return {"status": "unchanged", "reason": "이미 반영된 댓글이며 편집은 재적용하지 않습니다."}
    return {"status": "feedback_queued", "pending_comment_ids": pending,
            "selection_changed": False, "review_pr_preserved": True,
            "recommendation": research.dispatch_ai(
                {"status": "updated", "ai_status": "stale",
                 "pull_url": f"https://github.com/{repository}/pull/{number}"},
                repository, default)}


def review_comments(event, repository, actor, default):
    if event.get("action") != "created" or "pull_request" not in event.get("issue", {}):
        return {"status": "not_applicable"}
    number = research.issue_number(event["issue"]["number"])
    identifier = research.issue_number(event.get("comment", {}).get("id"))
    target = _comment(repository, identifier, number)
    if target.get("user", {}).get("login") != actor or event["comment"].get("user", {}).get("login") != actor:
        raise ValueError("댓글 작성자와 이벤트 actor가 일치하지 않습니다.")
    if not plan_lines(target.get("body")):
        return _queue_natural_feedback(repository, number, identifier, actor, default, target)
    if not _human_permission(repository, target.get("user", {})):
        return {"status": "deferred", "reason": "사람의 write/maintain/admin 권한이 필요합니다."}
    pull, issue = _pull(repository, number, default)
    head = pull["head"]["sha"]
    tree = _tree(repository, head)
    directory = f"research/reports/issue-{issue}/"
    _marker(_object(_file(repository, tree, directory + "request.json")), repository, issue)
    selection = _object(_file(repository, tree, directory + "selection.json"))
    original = copy.deepcopy(selection)
    state_bytes = _file(repository, tree, directory + "review-state.json", optional=True)
    state = _object(state_bytes) if state_bytes is not None else {
        "schema_version": 1, "kind": "research-plan-comment-audit",
        "repository": repository, "issue_number": issue, "pull_request": number,
        "edit_policy": EDIT_POLICY, "comments": {}}
    if (state.get("schema_version") != 1 or state.get("kind") != "research-plan-comment-audit"
            or state.get("repository") != repository or state.get("issue_number") != issue
            or state.get("pull_request") != number or not isinstance(state.get("comments"), dict)):
        raise ValueError("댓글 처리 기록의 소유 마커가 올바르지 않습니다.")
    catalogs, policy = _catalogs(), planning.load_policy()
    results, recommend, refresh_actor = [], False, None
    comments = _comments(repository, number, identifier)
    if identifier not in {item["id"] for item in comments}:
        raise ValueError("이벤트 댓글이 현재 PR 댓글 목록에 없습니다.")
    for listed in comments:
        key = str(listed["id"])
        if key in state["comments"]:
            continue
        comment = target if listed["id"] == identifier else _comment(repository, listed["id"], number)
        body = comment.get("body")
        if not plan_lines(body):
            continue
        author = comment.get("user", {})
        if _human_permission(repository, author):
            selection, result = apply_commands(selection, body, catalogs=catalogs, policy=policy)
            if result["applied"]:
                refresh_actor = author["login"]
            request_recommendation = result.pop("recommend")
            recommend = recommend or request_recommendation
        else:
            result = {"applied": [], "rejected": [
                {"reason": "봇·외부 작성자는 보류합니다. 사람의 write/maintain/admin 권한이 필요합니다."}]}
        record = {"comment_id": comment["id"], "body_sha256": _hash(body.encode("utf-8")),
                  "actor": author.get("login"), **result}
        state["comments"][key] = record
        results.append(record)
    if results:
        files = {directory + "selection.json": _json(selection).encode("utf-8"),
                 directory + "review-state.json": _json(state).encode("utf-8")}
        commit = _commit_files(repository, pull["head"]["ref"], head, files,
                               f"Apply explicit plan comments for Issue #{issue}")
        outcome = {"status": "partially_applied" if any(item["rejected"] for item in results)
                   and any(item["applied"] for item in results) else
                   ("rejected" if any(item["rejected"] for item in results) else "applied"),
                   "comments": results, "selection_commit": commit, "edit_policy": EDIT_POLICY,
                   "review_pr_preserved": True}
    else:
        record = state["comments"].get(str(identifier), {})
        outcome = {"status": "unchanged", "edit_policy": EDIT_POLICY, "comments": [],
                   "review_pr_preserved": True}
        if record.get("applied"):
            # A prior attempt may have committed selections before refresh failed.
            refresh_actor = actor
            recommend = any(item.get("action") == "recommend" for item in record["applied"])
    try:
        if refresh_actor is not None:
            outcome["refresh"] = _refresh(repository, default, refresh_actor, issue)
            if outcome["refresh"].get("status") not in ("created", "updated", "unchanged"):
                raise RuntimeError("권한 또는 Issue 상태에 따라 보고서 갱신이 보류되었습니다.")
        if recommend and outcome.get("refresh", {}).get("ai_dispatch", {}).get("status") != "requested":
            # Explicit refresh must dispatch even when a provided recommendation exists.
            outcome["recommendation"] = research.dispatch_ai(
                {"status": "updated", "ai_status": "stale",
                 "pull_url": f"https://github.com/{repository}/pull/{number}"},
                repository, default)
    except (RuntimeError, ValueError, OSError):
        _summary({**outcome, "status": "failed", "reason":
                  "선택과 PR은 보존되었습니다. 보고서 갱신/AI 요청 실패를 확인하고 다시 실행하세요."})
        raise
    outcome["selection_changed"] = selection != original
    return outcome


def _snapshot(repository, pull, issue, files):
    request, eda, eda_plan, evaluation, selection, ai, recommendation = (
        _object(files[name]) for name in REPORT_FILES)
    _marker(request, repository, issue)
    assets = _object(files["config/assets.json"])
    reference = research.data_reference(assets)
    model_reference = validate_image_reference(assets.get("model_image"))
    provenance = eda.get("provenance", {})
    if provenance.get("data_image") != reference:
        raise ValueError("병합된 EDA의 데이터 이미지와 병합된 config digest가 다릅니다.")
    if provenance.get("model_image", model_reference) != model_reference:
        raise ValueError("병합된 모델 이미지 digest가 다릅니다.")
    code = _sha(provenance.get("code_commit"))
    if (eda.get("kind") != "aggregate-eda" or eda.get("report_generated") is not True
            or any(eda.get(name) is not False for name in (
                "model_execution_performed", "training_performed", "preprocessing_performed"))
            or not re.fullmatch(r"[0-9a-f]{64}", eda.get("data_id") or "")):
        raise ValueError("모델 실행 없는 집계 EDA 계약이 필요합니다.")
    source = _object(files["config/prepared-source.json"])
    input_binding = None
    if "input-mapping.json" in files:
        from app.input_contracts import load_profile, validate_saved_binding
        input_binding = _object(files["input-mapping.json"])
        registry = _object(files["config/input-profiles.json"])
        profile = load_profile(input_binding.get("profile_id"), registry=registry)
        schema_request = (_object(files["input-schema.json"]) if "input-schema.json" in files
                          else input_binding.get("schema_request"))
        input_binding = validate_saved_binding(input_binding, profile, schema_request)
        if profile["source_kind"] == "registered_prepared_v1":
            original = input_binding["source_manifest"]
            source = {
                "source_profile": profile["profile_id"], "target": "target",
                "features": original["feature_columns"],
                "splits": {name: {"rows": count} for name, count in original["split_rows"].items()},
                "target_mapping": profile["target_values"], "limitations": profile["limitations"],
                "probability_output_planned": profile.get("probability_output_planned") is True,
            }
        expected_mapping_hash = canonical_hash({
            name: input_binding[name] for name in
            ("profile_id", "data_id", "bindings", "split_values", "target_values", "profile_snapshot")
        })
        if eda.get("data_id") != input_binding["data_id"] or eda.get("input_mapping_hash") != expected_mapping_hash:
            raise ValueError("병합된 EDA와 검증한 입력 매핑의 data_id/hash가 다릅니다.")
    research.validate_aggregate(eda, source)
    catalogs = {"eda": _object(files["config/eda-catalog.json"]),
                "evaluation": _object(files["config/metric-catalog.json"])}
    policy = _object(files["config/planning-policy.json"])
    from app.pr1_intent import validate_feedback_state
    feedback, applied_ids = validate_feedback_state(
        _object(files["feedback-requests.json"]) if "feedback-requests.json" in files else None,
        _object(files["feedback-applied.json"]) if "feedback-applied.json" in files else None,
        repository, pull["number"], issue)
    if set(item["comment_id"] for item in feedback) - set(applied_ids):
        raise ValueError("미반영 자연어 피드백이 남아 있어 승인 계획을 고정할 수 없습니다.")
    coordination = None
    if eda.get("coordination_hash") is not None:
        if "coordination-plan.json" not in files:
            raise ValueError("AI 조율 hash에 대응하는 승인 전 조율 기록이 없습니다.")
        coordination = _object(files["coordination-plan.json"])
        required = {"record", "code_commit", "schema", "eda_evidence", "coordination_hash"}
        if (set(coordination) != required or coordination["code_commit"] != code
                or coordination["coordination_hash"] != canonical_hash({
                    key: value for key, value in coordination.items() if key != "coordination_hash"})
                or coordination["coordination_hash"] != eda["coordination_hash"]
                or coordination["eda_evidence"] != eda.get("coordination_evidence")):
            raise ValueError("AI 조율 기록·실행 코드·EDA 근거 hash가 일치하지 않습니다.")
        from app.pr1_intent import validate_record
        validate_record(coordination["record"], selection, request, coordination["schema"],
                        catalogs=catalogs, policy=policy)
        if ("feedback-applied.json" not in files
                or _object(files["feedback-applied.json"])["coordination_hash"] != coordination["coordination_hash"]
                or not set(coordination["record"]["feedback_ids"]).issubset(applied_ids)):
            raise ValueError("AI 조율과 피드백 반영 기록이 일치하지 않습니다.")
        if (coordination["record"]["request_hash"] != eda.get("research_request_hash")
                or coordination["schema"].get("data_id") != eda["data_id"]
                or coordination["schema"].get("feature_columns") != source["features"]
                or coordination["schema"].get("model_ref") != model_reference):
            raise ValueError("AI가 읽은 목적·특성·모델과 병합된 계획이 다릅니다.")
        if input_binding is not None:
            bound_schema = input_binding["schema_request"]
            if (set(coordination["schema"]) != set(bound_schema) | {
                    "feature_columns", "model_ref", "current_bindings"}
                    or any(coordination["schema"][key] != value for key, value in bound_schema.items())
                    or coordination["schema"]["current_bindings"] not in (None, input_binding["bindings"])):
                raise ValueError("AI 조율의 입력 스키마와 검증된 바인딩이 다릅니다.")
    manifest = {
        "data_id": eda["data_id"], "prepared_source": source,
        "feature_columns": source["features"],
        "files": input_binding["source_manifest"]["files"] if input_binding else {"metadata.csv": None},
        "split_rows": {name: source["splits"][name]["rows"]
                       for name in ("train", "validation", "test")}}
    plans = planning.build_plans(eda, manifest, selection, ai, catalogs=catalogs, policy=policy)
    if recommendation.get("context_hash") != plans["recommendation_request"]["context_hash"]:
        raise ValueError("병합된 추천 요청의 context_hash가 현재 계획과 다릅니다.")
    for stage, saved in (("eda", eda_plan), ("evaluation", evaluation)):
        expected = plans[stage]
        if (saved.get("stage") != stage or saved.get("schema_version") != 1
                or saved.get("ai_status") != "provided" or expected["ai_status"] != "provided"
                or saved.get("planning_complete") is not True):
            raise ValueError("AI pending/stale 또는 planning_complete 미완료 계획은 고정할 수 없습니다.")
        for field in ("selected_ids", "required_ids", "user_added_ids", "user_excluded_ids",
                      "objectives"):
            if saved.get(field) != expected[field]:
                raise ValueError("병합된 선택과 생성 계획이 일치하지 않습니다.")
        actual_items = saved.get("items")
        if not isinstance(actual_items, list) or [
            (item.get("id"), item.get("state"), item.get("blocked_reasons"))
            for item in actual_items
        ] != [(item["id"], item["state"], item["blocked_reasons"]) for item in expected["items"]]:
            raise ValueError("병합된 단계 상태/blocked 이유가 계획과 다릅니다.")
    primary = evaluation.get("primary_metric")
    readiness = planning.approval_readiness(plans)
    if not readiness["ready"]:
        raise ValueError("승인 계획을 고정할 수 없습니다: " + "; ".join(readiness["reasons"]))
    for field in ("primary_metric", "threshold", "parameters", "clinical_action", "comparison_model"):
        if evaluation.get(field) != plans["evaluation"][field]:
            raise ValueError("선택한 주지표/매개변수와 병합된 계획이 다릅니다.")
    results = eda.get("results", [])
    if (not isinstance(results, list) or [item.get("block") for item in results] != eda_plan["selected_ids"]
            or not {"schema", "patient_overlap"}.issubset(eda_plan["selected_ids"])):
        raise ValueError("EDA 실행 결과와 필수/선택 블록이 일치하지 않습니다.")
    for item in results:
        if item.get("status") not in ("passed", "failed", "unverifiable", "completed"):
            raise ValueError("EDA 단계 상태가 올바르지 않습니다.")
    merged_at = pull.get("merged_at")
    if not isinstance(merged_at, str) or not re.fullmatch(
            r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", merged_at):
        raise ValueError("병합 시각을 확인할 수 없습니다.")
    return {
        "schema_version": 1, "kind": "immutable-approved-research-plan",
        "repository": repository, "issue_number": issue, "pull_request": pull["number"],
        "approval_scope": "educational_demo_only", "clinical_use_approved": False,
        "approval_meaning": "사람의 Merge는 선택한 교육용 실행계획 승인입니다. "
                            "독립 임상 성능·임상 사용 승인이 아니며 승인 기록 없이 후속 평가를 승인으로 간주하지 않습니다.",
        "source_head_commit": _sha(pull["head"]["sha"]),
        "merge_commit": _sha(pull["merge_commit_sha"]), "code_commit": code,
        "merger": pull["merged_by"]["login"], "merged_at": merged_at,
        "file_hashes": {name: _hash(content) for name, content in sorted(files.items())},
        "data_id": eda["data_id"], "data_image": reference, "model_image": model_reference,
        "input_contract": input_binding,
        "coordination": coordination,
        "model": copy.deepcopy(source.get("model", {})),
        "selection": selection, "primary_metric": primary,
        "threshold": evaluation.get("threshold"), "parameters": evaluation.get("parameters"),
        "stages": {"eda": {"ai_status": eda_plan["ai_status"],
                          "planning_complete": True, "assessment_status": eda.get("assessment_status"),
                          "results": [{"block": item["block"], "status": item["status"]}
                                      for item in results]},
                   "evaluation": {"ai_status": evaluation["ai_status"],
                                  "planning_complete": True, "items": evaluation["items"],
                                  "execution_status": "not_executed"}},
        "limitations": {"data": source.get("limitations", []),
                        "model": source.get("model", {}).get("limitations", []),
                        "patient_overlap": {
                            "overlaps": copy.deepcopy(next(
                                item["details"]["overlaps"] for item in results
                                if item["block"] == "patient_overlap")),
                            "status": next(item["status"] for item in results
                                           if item["block"] == "patient_overlap")},
                        "validation_development_reuse": True if "source_profile" not in source else None,
                        "independent_clinical_validation": False,
                        "uncertain_blocks": [item["block"] for item in results
                                             if item["status"] == "unverifiable"],
                        "interpretation": evaluation.get("interpretation")},
        "model_execution_performed": False, "metric_execution_performed": False,
    }


def freeze_merge(event, repository, default):
    if event.get("action") != "closed" or event.get("pull_request", {}).get("merged") is not True:
        return {"status": "not_applicable"}
    number = research.issue_number(event["pull_request"].get("number"))
    pull, issue = _pull(repository, number, default, merged=True)
    merger = pull.get("merged_by", {})
    if not _human_permission(repository, merger):
        raise ValueError("write/maintain/admin 권한을 가진 사람이 Merge한 계획만 승인할 수 있습니다.")
    commit = _sha(pull.get("merge_commit_sha"))
    if event["pull_request"].get("merge_commit_sha") != commit:
        raise ValueError("Merge 이벤트와 API의 merge_commit_sha가 다릅니다.")
    tree = _tree(repository, commit)
    directory = f"research/reports/issue-{issue}/"
    files = {name: _file(repository, tree, directory + name) for name in REPORT_FILES}
    files.update({name: _file(repository, tree, name) for name in CONFIG_FILES})
    binding = _file(repository, tree, directory + "input-mapping.json", optional=True)
    if binding is not None:
        files["input-mapping.json"] = binding
        files["config/input-profiles.json"] = _file(repository, tree, "config/input-profiles.json")
        schema = _file(repository, tree, directory + "input-schema.json", optional=True)
        if schema is not None:
            files["input-schema.json"] = schema
    coordination = _file(repository, tree, directory + "coordination-plan.json", optional=True)
    if coordination is not None:
        files["coordination-plan.json"] = coordination
    for name in ("feedback-requests.json", "feedback-applied.json"):
        value = _file(repository, tree, directory + name, optional=True)
        if value is not None:
            files[name] = value
    snapshot = _snapshot(repository, pull, issue, files)
    path = f"research/approved/issue-{issue}/{commit}.json"
    content = _json(snapshot).encode("utf-8")
    # Read the latest default head only for the additive write, not approval inputs.
    ref = research.gh_json(f"repos/{repository}/git/ref/heads/{quote(default, safe='/')}")
    parent = _sha(ref["object"]["sha"])
    existing = _file(repository, _tree(repository, parent), path, optional=True)
    if existing is not None:
        if existing != content:
            raise ValueError("불변성 오류: 같은 Merge의 승인 기록이 다릅니다. 덮어쓰지 않았습니다.")
        return {"status": "unchanged", "approved_plan": path,
                "approval_scope": "educational_demo_only", "clinical_use_approved": False}
    new_commit = _commit_files(repository, default, parent, {path: content},
                               f"Freeze educational plan for Issue #{issue} at {commit}")
    return {"status": "frozen", "approved_plan": path, "approval_commit": new_commit,
            "approval_scope": "educational_demo_only", "clinical_use_approved": False}


def run_event(event, event_name, repository, actor):
    research.repository_name(repository)
    if event.get("repository", {}).get("full_name") != repository:
        raise ValueError("이벤트 저장소가 일치하지 않습니다.")
    default = research.gh_json(f"repos/{repository}").get("default_branch")
    if not isinstance(default, str) or not default:
        raise ValueError("저장소 기본 브랜치를 확인할 수 없습니다.")
    if event_name == "issue_comment":
        return review_comments(event, repository, actor, default)
    if event_name == "pull_request":
        return freeze_merge(event, repository, default)
    raise ValueError("지원하지 않는 검토 이벤트입니다.")


def _summary(outcome):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with Path(path).open("a", encoding="utf-8") as handle:
            # No request text, raw comments or patient rows are included.
            handle.write("## 연구 계획 검토·고정\n\n```json\n" + _json(outcome) + "```\n")


def main():
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("로컬 원격 실행은 금지합니다. 함수 모의 테스트만 사용하세요.")
    try:
        event = _object(Path(os.environ["GITHUB_EVENT_PATH"]).read_bytes())
        outcome = run_event(event, os.environ["GITHUB_EVENT_NAME"],
                            os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_ACTOR"])
        from app.recommendation_events import write_target
        write_target(outcome, os.environ["GITHUB_REPOSITORY"])
        _summary(outcome)
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as error:
        _summary({"status": "failed", "approval_record_created": False,
                  "reason": str(error) if isinstance(error, (ValueError, RuntimeError))
                  else "검토 파일/실행환경 구조가 잘못되었습니다.",
                  "policy": "기존 PR/선택은 보존됩니다. 승인 기록이 없으면 후속 평가 승인이 아닙니다."})
        raise


if __name__ == "__main__":
    main()
