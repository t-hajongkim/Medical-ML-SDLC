"""Correlate a completed trusted workflow with its exact Issue or review PR."""

import io
import json
import os
from pathlib import Path
import re
import subprocess
import zipfile

from app import research
from app.asset_contract import write_json


TARGET_FILE = "research-ai-target.json"
SOURCES = {
    ".github/workflows/prepare-eda-pr.yml": {"issues", "workflow_dispatch"},
    ".github/workflows/review-eval-plan.yml": {"issue_comment"},
}


def _target(value, *, legacy=False):
    kind = value.get("target_kind", "pull_request" if legacy else None)
    if kind not in ("issue", "pull_request"):
        raise ValueError("추천 대상은 Issue 또는 기존 연구검토 PR이어야 합니다.")
    number = research.issue_number(value.get(
        "target_number", value.get("pull_request") if legacy else None))
    aliases = ("issue", "issue_number") if kind == "issue" else ("pull_request",)
    for key in aliases:
        if key in value and research.issue_number(value[key]) != number:
            raise ValueError("추천 대상 번호와 호환 필드가 일치하지 않습니다.")
    if kind == "issue" and value.get("pull_request") is not None:
        raise ValueError("최초 Issue 전달에는 PR 번호를 지정할 수 없습니다.")
    return kind, number


def write_target(outcome, repository):
    """Publish control metadata only; never copy Issue text or patient records."""
    research.repository_name(repository)
    run = research.run_identifier(os.environ.get("GITHUB_RUN_ID"))
    attempt = research.run_identifier(os.environ.get("GITHUB_RUN_ATTEMPT"))
    sha = os.environ.get("GITHUB_SHA", "")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("추천 전달에 유효한 source commit이 필요합니다.")
    runner = Path(os.environ["RUNNER_TEMP"]).resolve()
    if not runner.is_dir() or runner.is_relative_to(research.ROOT.resolve()):
        raise ValueError("추천 전달 파일은 체크아웃 외부의 runner 임시 경로에만 저장합니다.")
    target = runner / TARGET_FILE
    if target.is_symlink():
        raise ValueError("추천 전달 파일에 심볼릭 링크를 사용할 수 없습니다.")
    candidates = [
        outcome.get("ai_dispatch", {}),
        outcome.get("recommendation", {}),
    ]
    targets = set()
    for candidate in candidates:
        if candidate.get("status") == "requested":
            if candidate.get("workflow") != "research-plan.lock.yml":
                raise ValueError("등록되지 않은 추천 워크플로입니다.")
            targets.add(_target(candidate, legacy=True))
    if len(targets) > 1:
        raise ValueError("하나의 검토 실행이 서로 다른 추천 대상을 지정했습니다.")
    payload = {
        "schema_version": 2, "repository": repository, "source_run_id": run,
        "source_run_attempt": attempt, "source_head_sha": sha, "requested": bool(targets),
    }
    if targets:
        kind, number = targets.pop()
        payload.update(target_kind=kind, target_number=number)
        payload["issue_number" if kind == "issue" else "pull_request"] = number
    write_json(target, payload)
    return target


def _download_target(repository, identifier):
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{repository}/actions/artifacts/{identifier}/zip"],
            capture_output=True, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError("추천 전달 artifact를 다운로드하지 못했습니다.") from None
    if result.returncode or len(result.stdout) > 1_000_000:
        raise RuntimeError("추천 전달 artifact 다운로드 실패 또는 크기 초과입니다.")
    try:
        with zipfile.ZipFile(io.BytesIO(result.stdout)) as archive:
            entries = archive.infolist()
            if (len(entries) != 1 or entries[0].filename != TARGET_FILE
                    or entries[0].file_size > 65536):
                raise ValueError("추천 전달 artifact에는 작은 대상 JSON 하나만 허용합니다.")
            content = archive.read(entries[0])
    except zipfile.BadZipFile:
        raise ValueError("추천 전달 artifact가 올바른 ZIP이 아닙니다.") from None
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("추천 전달 JSON에 중복 키가 있습니다.")
            value[key] = item
        return value
    value = json.loads(content, object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError("추천 전달 JSON은 객체여야 합니다.")
    return value


def resolve_target(event, event_name, repository, requested=None):
    """Resolve one authorized target, never guess the newest or any open PR."""
    research.repository_name(repository)
    if event.get("repository", {}).get("full_name") != repository:
        raise ValueError("추천 이벤트의 저장소가 일치하지 않습니다.")
    if event_name == "workflow_dispatch":
        kind = "pull_request"
        number = research.issue_number(requested)
    elif event_name == "workflow_run":
        original = event.get("workflow_run", {})
        run = research.run_identifier(str(original.get("id", "")))
        source = research.gh_json(f"repos/{repository}/actions/runs/{run}")
        default = event["repository"]["default_branch"]
        if (not isinstance(source, dict)
                or not isinstance(source.get("repository"), dict)
                or not isinstance(source.get("head_repository"), dict)):
            raise ValueError("선행 실행의 저장소 출처 메타데이터가 없습니다.")
        if (str(source.get("id")) != run or source.get("path") not in SOURCES
                or source.get("event") not in SOURCES[source["path"]]
                or source.get("repository", {}).get("full_name") != repository
                or source.get("head_repository", {}).get("full_name") != repository):
            raise ValueError("같은 저장소·기본 브랜치의 등록된 선행 워크플로만 이어받습니다.")
        if source.get("status") != "completed" or source.get("conclusion") != "success":
            return {"ready": False, "reason": "선행 워크플로가 성공하지 않아 AI를 실행하지 않습니다."}
        if (source.get("head_branch") != default
                or not re.fullmatch(r"[0-9a-f]{40}", source.get("head_sha", ""))
                or ("head_sha" in original and original["head_sha"] != source["head_sha"])):
            raise ValueError("AI 추천은 기본 브랜치의 선행 실행만 이어받습니다.")
        attempt = research.run_identifier(str(source.get("run_attempt", "")))
        if str(original.get("run_attempt")) != attempt:
            raise ValueError("선행 워크플로의 실행 회차가 변경되었습니다. 최신 완료 이벤트를 사용하세요.")
        pages = research.gh_json(
            f"repos/{repository}/actions/runs/{run}/artifacts?per_page=100",
            "--paginate", "--slurp",
        )
        expected = f"research-ai-target-{run}-{attempt}"
        if (not isinstance(pages, list) or any(not isinstance(page, dict)
                or not isinstance(page.get("artifacts"), list) for page in pages)):
            raise ValueError("선행 실행의 artifact 목록 형식이 잘못되었습니다.")
        matches = [item for page in pages for item in page["artifacts"]
                   if item.get("name") == expected and item.get("expired") is False]
        if len(matches) != 1:
            raise ValueError("추천 전달 artifact가 없거나 만료되었습니다. 선행 워크플로를 함께 업데이트·재실행하거나 PR 번호로 수동 추천하세요.")
        payload = _download_target(repository, research.issue_number(matches[0]["id"]))
        fields = {"schema_version", "repository", "source_run_id", "source_run_attempt",
                  "source_head_sha", "requested", "pull_request"}
        version = payload.get("schema_version")
        if version == 2:
            fields |= {"target_kind", "target_number", "issue_number"}
        if (type(version) is not int or version not in (1, 2)
                or set(payload) - fields
                or payload.get("repository") != repository or payload.get("source_run_id") != run
                or payload.get("source_run_attempt") != attempt
                or payload.get("source_head_sha") != source.get("head_sha")
                or type(payload.get("requested")) is not bool):
            raise ValueError("추천 전달 파일과 선행 실행의 출처가 일치하지 않습니다.")
        if not payload["requested"]:
            if set(payload) & {"target_kind", "target_number", "issue_number", "pull_request"}:
                raise ValueError("추천하지 않는 전달 파일에 대상이 포함되어 있습니다.")
            return {"ready": False, "reason": "선행 실행은 AI 추천을 요청하지 않았습니다."}
        kind, number = _target(payload, legacy=version == 1)
        if (kind == "pull_request" and "issue_number" in payload
                or kind == "issue" and "pull_request" in payload):
            raise ValueError("추천 전달 파일에 다른 종류의 대상 번호가 포함되어 있습니다.")
        if kind == "issue":
            if source["path"] != ".github/workflows/prepare-eda-pr.yml":
                raise ValueError("최초 Issue 추천은 등록된 Issue 접수 실행만 이어받습니다.")
            actor = source.get("actor") or {}
            login = actor.get("login")
            if (actor.get("type") != "User" or not isinstance(login, str)
                    or not re.fullmatch(r"[A-Za-z0-9-]+", login)):
                raise ValueError("최초 Issue 추천의 source actor가 유효한 사람이 아닙니다.")
            permission = research.gh_json(f"repos/{repository}/collaborators/{login}/permission")
            if permission.get("permission") not in ("write", "maintain", "admin"):
                raise ValueError("최초 Issue 추천에는 source actor의 쓰기 권한이 필요합니다.")
            issue = research.gh_json(f"repos/{repository}/issues/{number}")
            if (issue.get("number") != number or issue.get("state") != "open"
                    or "pull_request" in issue
                    or (source["event"] == "issues" and issue.get("user", {}).get("login") != login)
                    or research.parse_issue(issue, repository) is None):
                raise ValueError("추천 대상은 권한 검증한 열린 등록 연구 Issue여야 합니다.")
    else:
        raise ValueError("지원하지 않는 추천 시작 이벤트입니다.")
    if kind == "pull_request":
        from app.research import _pull
        _pull(research, repository, number)
    result = {"ready": True, "target_kind": kind, "target_number": number,
              "issue_number" if kind == "issue" else "pull_request": number,
              "trigger": event_name}
    if kind == "issue":
        result["request_actor"] = login
    return result


def write_outputs(result):
    if result["ready"]:
        kind, number = _target(result, legacy=True)
        if kind == "issue":
            actor = result.get("request_actor")
            if not isinstance(actor, str) or not re.fullmatch(r"[A-Za-z0-9-]+", actor):
                raise ValueError("최초 Issue 추천에는 검증된 source actor가 필요합니다.")
    output = Path(os.environ["GITHUB_OUTPUT"])
    with output.open("a", encoding="utf-8") as handle:
        handle.write("ready=" + ("true" if result["ready"] else "false") + "\n")
        if result["ready"]:
            handle.write(f"target_kind={kind}\ntarget_number={number}\n")
            handle.write(f"{'issue_number' if kind == 'issue' else 'pull_request'}={number}\n")
            if kind == "issue":
                handle.write(f"request_actor={actor}\n")
