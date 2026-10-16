"""Queue authorized free-form PR feedback for AI planning, never execute comments."""

import base64
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import quote

from app import research
from app.github_io import git_sha as _sha, read_file, read_object as _object, read_tree


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, indent=2) + "\n"


def _hash(content):
    return hashlib.sha256(content).hexdigest()


def _write_api(repository, endpoint, payload, method="POST"):
    result = research.command(
        ["gh", "api", "--method", method, f"repos/{repository}/{endpoint}", "--input", "-"],
        input_text=_json(payload))
    return _object(result.stdout.encode("utf-8"))


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
                           "기존 PR·선택은 덮어쓰지 않았습니다.") from None
    return commit["sha"]


def _human_permission(repository, user):
    login = user.get("login")
    if (user.get("type") != "User" or not isinstance(login, str)
            or not re.fullmatch(r"[A-Za-z0-9-]+", login)):
        return False
    result = research.gh_json(f"repos/{repository}/collaborators/{login}/permission")
    return result.get("permission") in ("write", "maintain", "admin")


def _pull(repository, number, default, *, pull=None):
    pull = research.gh_json(f"repos/{repository}/pulls/{number}") if pull is None else pull
    match = re.fullmatch(r"research/review-issue-([1-9][0-9]{0,9})",
                         pull.get("head", {}).get("ref", ""))
    if (pull.get("number") != number or not match
            or pull.get("head", {}).get("repo", {}).get("full_name") != repository
            or pull.get("base", {}).get("repo", {}).get("full_name") != repository
            or pull.get("base", {}).get("ref") != default
            or not isinstance(pull.get("title"), str)
            or not pull["title"].startswith("연구검토 #")
            or pull.get("state") != "open"):
        raise ValueError("같은 저장소·생성 브랜치·기본 브랜치의 연구검토 PR만 처리합니다.")
    _sha(pull["head"]["sha"])
    return pull, int(match.group(1))


def _marker(request, repository, issue):
    if (request.get("schema_version") != 1 or request.get("repository") != repository
            or request.get("issue_number") != issue
            or request.get("issue_url") != f"https://github.com/{repository}/issues/{issue}"):
        raise ValueError("request.json의 저장소·Issue·브랜치 소유 마커가 일치하지 않습니다.")


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


def _queue_natural_feedback(repository, number, identifier, actor, default, target):
    raw = research.gh_json(f"repos/{repository}/pulls/{number}")
    if not re.fullmatch(r"research/review-issue-[1-9][0-9]*",
                        raw.get("head", {}).get("ref", "")):
        return {"status": "not_applicable", "reason": "연구검토 PR의 자연어 피드백만 처리합니다."}
    if not _human_permission(repository, target.get("user", {})):
        return {"status": "deferred", "reason": "자연어 피드백에는 사람의 write/maintain/admin 권한이 필요합니다."}
    permissions = {target["user"]["login"]: True}
    pull, issue = _pull(repository, number, default, pull=raw)
    tree = read_tree(repository, pull["head"]["sha"], research.gh_json)
    directory = f"research/reports/issue-{issue}/"
    _marker(_object(read_file(repository, tree, directory + "request.json", research.gh_json)), repository, issue)
    previous = read_file(repository, tree, directory + "feedback-requests.json", research.gh_json, optional=True)
    ledger = _object(previous) if previous else {
        "schema_version": 1, "kind": "pr1-natural-feedback",
        "repository": repository, "pull_request": number, "issue_number": issue, "comments": []}
    applied_data = read_file(repository, tree, directory + "feedback-applied.json", research.gh_json, optional=True)
    from app.planning_eda import validate_feedback_state
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
        comment = target if listed["id"] == identifier else listed
        body = comment.get("body")
        user = comment.get("user", {})
        if user.get("type") != "User" or not isinstance(body, str) or not body.strip():
            continue
        login = user.get("login")
        if login not in permissions:
            permissions[login] = _human_permission(repository, user)
        if not permissions[login]:
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
    return _queue_natural_feedback(repository, number, identifier, actor, default, target)


def run_event(event, event_name, repository, actor):
    research.repository_name(repository)
    if event.get("repository", {}).get("full_name") != repository:
        raise ValueError("이벤트 저장소가 일치하지 않습니다.")
    if event_name != "issue_comment":
        return {"status": "not_applicable"}
    default = research.gh_json(f"repos/{repository}").get("default_branch")
    if not isinstance(default, str) or not default:
        raise ValueError("저장소 기본 브랜치를 확인할 수 없습니다.")
    return review_comments(event, repository, actor, default)


def _summary(outcome):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with Path(path).open("a", encoding="utf-8") as handle:
            # No request text, raw comments or patient rows are included.
            handle.write("## 연구 계획 피드백\n\n```json\n" + _json(outcome) + "```\n")


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
        _summary({"status": "failed", "feedback_queued": False,
                  "reason": str(error) if isinstance(error, (ValueError, RuntimeError))
                  else "검토 파일/실행환경 구조가 잘못되었습니다.",
                  "policy": "기존 PR/선택은 보존됩니다. 자연어 피드백 전달 실패를 확인하고 다시 시도하세요."})
        raise


if __name__ == "__main__":
    main()
