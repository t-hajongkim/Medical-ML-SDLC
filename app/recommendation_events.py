"""Correlate a completed trusted workflow with its exact recommendation PR."""

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
    ".github/workflows/review-eval-plan.yml": {"issue_comment", "pull_request"},
}


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
        outcome.get("refresh", {}).get("ai_dispatch", {}),
        outcome.get("recommendation", {}),
    ]
    numbers = set()
    for candidate in candidates:
        if candidate.get("status") == "requested":
            if candidate.get("workflow") != "research-plan.lock.yml":
                raise ValueError("등록되지 않은 추천 워크플로입니다.")
            numbers.add(research.issue_number(candidate.get("pull_request")))
    if len(numbers) > 1:
        raise ValueError("하나의 검토 실행이 서로 다른 PR을 추천 대상으로 지정했습니다.")
    payload = {
        "schema_version": 1, "repository": repository, "source_run_id": run,
        "source_run_attempt": attempt, "source_head_sha": sha, "requested": bool(numbers),
    }
    if numbers:
        payload["pull_request"] = numbers.pop()
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
    """Resolve one exact PR, never the newest or an arbitrary open PR."""
    research.repository_name(repository)
    if event.get("repository", {}).get("full_name") != repository:
        raise ValueError("추천 이벤트의 저장소가 일치하지 않습니다.")
    if event_name == "workflow_dispatch":
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
        if source["path"] == ".github/workflows/review-eval-plan.yml" and source["event"] == "pull_request":
            return {"ready": False, "reason": "병합된 승인 계획은 AI 추천을 다시 요청하지 않습니다."}
        if (source.get("head_branch") != default
                or not re.fullmatch(r"[0-9a-f]{40}", source.get("head_sha", ""))):
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
        if (type(payload.get("schema_version")) is not int or payload["schema_version"] != 1
                or set(payload) - fields
                or payload.get("repository") != repository or payload.get("source_run_id") != run
                or payload.get("source_run_attempt") != attempt
                or payload.get("source_head_sha") != source.get("head_sha")
                or type(payload.get("requested")) is not bool):
            raise ValueError("추천 전달 파일과 선행 실행의 출처가 일치하지 않습니다.")
        if not payload["requested"]:
            return {"ready": False, "reason": "선행 실행은 AI 추천을 요청하지 않았습니다."}
        number = research.issue_number(payload.get("pull_request"))
    else:
        raise ValueError("지원하지 않는 추천 시작 이벤트입니다.")
    from app.research import _pull
    _pull(research, repository, number)
    return {"ready": True, "pull_request": number, "trigger": event_name}


def write_outputs(result):
    output = Path(os.environ["GITHUB_OUTPUT"])
    with output.open("a", encoding="utf-8") as handle:
        handle.write("ready=" + ("true" if result["ready"] else "false") + "\n")
        if result["ready"]:
            handle.write(f"pull_request={result['pull_request']}\n")
