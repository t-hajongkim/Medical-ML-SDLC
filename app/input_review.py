"""Input-mapping review files and dataset-neutral aggregate presentation."""

import base64
import copy
import hashlib
import json
import re

from app.asset_contract import write_json


INPUT_FILES = ("input-schema.json", "input-mapping.json")
STALE_FILES = ("eda.json", "eda-plan.json", "evaluation-plan.json",
               "recommendation-request.json", "ai-recommendations.json", "input-mapping.json",
               "coordination-plan.json", "execution-plan.json")


def read_binding(repository, pull):
    from app import research
    head = pull.get("head", {}).get("sha")
    if not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40}", head):
        raise ValueError("입력 매핑을 읽을 PR head가 올바르지 않습니다.")
    branch = pull["head"]["ref"]
    match = re.fullmatch(r"research/review-issue-([1-9][0-9]*)", branch)
    if not match:
        raise ValueError("등록된 연구검토 브랜치의 입력 매핑만 읽습니다.")
    path = f"research/reports/issue-{match.group(1)}/input-mapping.json"
    tree = research.gh_json(f"repos/{repository}/git/trees/{head}?recursive=1")
    if tree.get("truncated") is not False:
        raise ValueError("잘린 Git tree에서 입력 매핑을 선택할 수 없습니다.")
    entries = [entry for entry in tree["tree"] if entry["path"] == path]
    if not entries:
        return None
    entry = entries[0]
    if (entry.get("type") != "blob" or entry.get("mode") != "100644"
            or entry.get("size", 0) > 1_000_000):
        raise ValueError("입력 매핑은 허용된 크기의 일반 JSON 파일이어야 합니다.")
    blob = research.gh_json(f"repos/{repository}/git/blobs/{entry['sha']}")
    if blob.get("encoding") != "base64":
        raise ValueError("입력 매핑 blob 인코딩이 올바르지 않습니다.")
    content = base64.b64decode("".join(blob["content"].split()), validate=True)
    digest = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
    if digest != entry["sha"] or len(content) > 1_000_000:
        raise ValueError("입력 매핑 blob 무결성 확인 실패입니다.")
    from app.input_contracts import _json
    value = _json(content.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("입력 매핑은 JSON 객체여야 합니다.")
    return value


def write_pending(root, request, schema_request, provenance, selection, phase="input_mapping"):
    from app import research
    directory = research.report_directory(root, request["issue_number"])
    directory.mkdir(parents=True, exist_ok=True)
    names = ["request.json", "selection.json", "input-schema.json", "report.md"]
    for name in {*names, *STALE_FILES}:
        if (directory / name).is_symlink():
            raise ValueError("입력 검토 파일에 심볼릭 링크를 사용할 수 없습니다.")
    write_json(directory / "request.json", request)
    write_json(directory / "input-schema.json", schema_request)
    if not (directory / "selection.json").exists():
        write_json(directory / "selection.json", copy.deepcopy(selection))
    lines = [
        "# 연구검토: AI 입력·점검 계획 준비",
        "", f"원본 Issue: {request['issue_url']}",
        "", "**입력·점검 계획을 AI가 확인하기 전이므로 EDA는 실행하지 않았습니다.**",
        ("미확정 매핑은 AI가 먼저 추천하고 Python이 검증합니다."
         if phase == "input_mapping" else
         "등록된 config 매핑을 재사용하되 AI가 연구 목적·스키마·블록 입력 계약을 먼저 확인합니다."),
        "AI가 점검 실행을 요청하면 Python이 등록된 입력·타입·매개변수와 블록 ID를 검증해 계산합니다.",
        "매핑이 불명확하면 추정한 환자 키로 중복 0명을 보고하지 않습니다.",
        "", f"입력 프로필: `{schema_request['profile_id']}`",
        f"data_id: `{schema_request['data_id']}`",
        f"스키마 context_hash: `{schema_request['context_hash']}`",
        f"데이터 이미지: `{provenance['data_image']}`",
        f"실행 코드: `{provenance['code_commit']}`",
        "",         "공개 연구 목적·질문은 AI에 제공하지만 환자 원본 행·식별자 값·인증정보는 제공하지 않습니다.",
        "기본 역할 검사를 통과한 Copilot이 입력 매핑을 추천합니다. 새 Python/SQL 코드는 생성·실행하지 않습니다.",
        "등록된 후보와 근거로 해결할 수 없으면 데이터 사전·프로필을 먼저 보완해야 합니다.",
        "이 단계에서는 Merge로 승인 계획을 확정할 수 없습니다.",
        "",
    ]
    (directory / "report.md").write_text("\n".join(lines), encoding="utf-8")
    for name in STALE_FILES:
        target = directory / name
        if target.exists():
            target.unlink()
            names.append(name)
    return [str((directory / name).relative_to(root)).replace("\\", "/") for name in names]


def mapping_lines(binding):
    from app.research import cell
    return [
        "", "## 입력 역할 매핑",
        f"출처: `{binding['provider']}` / 프로필: `{binding['profile_id']}`.",
        "검증은 등록된 컬럼·의미·형식·변환 계약에 대한 것이며 임상적 타당성 인증은 아닙니다.",
        "", "| 표준 역할 | 원본 컬럼 |", "|---|---|",
        *(f"| `{cell(role)}` | `{cell(column)}` |"
          for role, column in binding["bindings"].items()),
        "", "원래 추천·프로필·출처 명세는 `input-mapping.json`에 보존합니다.",
    ]


def generic_report(request, eda, provenance, plans):
    from app.research import cell, plan_report_lines
    by_name = {item["block"]: item for item in eda["results"]}
    schema = by_name["schema"]["details"]
    context = eda["dataset_context"]
    lines = [
        "# 연구검토: 등록 입력의 데이터 점검과 평가 계획",
        "", f"원본 Issue: {request['issue_url']}",
        "", "**보고서 생성 성공과 독립 연구 적합성 통과는 다릅니다.**",
        f"- 데이터 점검 판정: `{eda['assessment_status']}`",
        f"- 입력 프로필: `{eda['input_profile']}`",
        f"- data_id: `{eda['data_id']}`",
        f"- 입력 매핑 hash: `{eda['input_mapping_hash']}`",
        f"- 데이터 이미지: `{provenance['data_image']}`",
        f"- 실행 코드: `{provenance['code_commit']}`",
        f"- 실행 근거: {provenance['workflow_run_url']}",
        "- 모델 학습·추론·지표 값 계산·임상 사용 승인: 수행하지 않음",
        "", "## 점검 블록",
        "", "| ID | 상태 |", "|---|---|",
        *(f"| `{item['block']}` | `{item['status']}` |" for item in eda["results"]),
        "", f"수치 입력: {schema['rows']}행 × {schema['actual_count']}특성.",
        "등록된 분할·변수 순서·숫자 배열을 유지하며 재학습·재분할하지 않습니다.",
        "", "### 분할별 행수", "", "| 분할 | 행수 |", "|---|---:|",
        *(f"| {name} | {count} |" for name, count in schema["split_rows"].items()),
        "", "### 환자 집합의 교집합", "", "| 분할 쌍 | 고유 환자 중복 |", "|---|---:|",
        *(f"| {name} | {count} |"
          for name, count in by_name["patient_overlap"]["details"]["overlaps"].items()),
        "", "동일한 환자 키가 양쪽에 있음을 계산합니다. 다른 누수가 없다는 인증은 아닙니다.",
        "", "## 데이터셋별 의미와 한계",
        cell(context.get("target_definition", "정답 정의 근거가 없습니다.")),
        *(f"- {cell(item)}" for item in context.get("limitations", [])),
        "다른 데이터셋의 관찰기간·대치·선정조건을 이 데이터에 적용하지 않습니다.",
        "미제공 관찰기간·과거 fit 범위·외부 사건 포착은 확인 불가입니다.",
        "", "## 평가 계획 추천 초안",
    ]
    lines.extend(plan_report_lines(plans))
    return "\n".join(lines)
