"""Research-form intake, aggregate review payload and guarded GitHub publication."""

import argparse
import copy
import html
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid

from app.asset_contract import canonical_hash, read_json, validate_image_reference, write_json
from app.blocks.catalog import CHECKS
from app.dataset import ROOT
from app.executor_eda import InputMappingRequired, load_context, report
from app import executor_eda, planning_eda
from app.prepared import CONFIG, SPLITS, docker
from app import planning_eval as planning


OUTPUT_NAMES = ("request.json", "eda.json", "report.md", "evaluation-plan.json",
                "eda-plan.json", "recommendation-request.json", "selection.json")
AUTO_START = "<!-- research-review:auto:start -->"
AUTO_END = "<!-- research-review:auto:end -->"
TRUSTED_ASSOCIATIONS = {"OWNER", "COLLABORATOR"}


def pin_review_code():
    """Cache trusted helpers/checks before selecting a report-only PR tree."""
    # CHECKS already holds eagerly imported functions from the trusted catalog.
    for name in ("app.input_review", "app.recommendation_events", "app.planning_eda", "app.executor_eval"):
        importlib.import_module(name)


def form_contract(path=None):
    try:
        import yaml
    except ImportError:
        raise RuntimeError("Install requirements-workflow.txt before reading the Issue Form.") from None
    class FormLoader(yaml.SafeLoader):
        pass

    def unique_mapping(loader, node):
        result = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node)
            if key in result:
                raise ValueError("Issue Form contains duplicate YAML keys.")
            result[key] = loader.construct_object(value_node)
        return result

    FormLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)
    try:
        document = yaml.load(
            (path or ROOT / ".github" / "ISSUE_TEMPLATE" / "research-request.yml").read_text(encoding="utf-8"),
            Loader=FormLoader,
        )
    except (yaml.YAMLError, TypeError):
        raise ValueError("Issue Form contains invalid YAML.") from None
    if not isinstance(document, dict) or not isinstance(document.get("body"), list):
        raise ValueError("Issue Form must define a body.")
    fields, required, identifiers = {}, [], set()
    consent = None
    reserved = {"schema_version", "repository", "issue_number", "issue_url", "title",
                "selection_execution", "prediction_time"}
    for field in document["body"]:
        if not isinstance(field, dict):
            raise ValueError("Issue Form fields must be objects.")
        if field.get("type") == "markdown":
            continue
        attributes = field.get("attributes", {})
        validations = field.get("validations", {})
        if not isinstance(attributes, dict) or not isinstance(validations, dict):
            raise ValueError("Issue Form attributes and validations must be objects.")
        if "required" in validations and type(validations["required"]) is not bool:
            raise ValueError("Issue Form required flags must be booleans.")
        identifier, label = field.get("id"), attributes.get("label")
        if (not isinstance(identifier, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", identifier)
                or identifier in identifiers or identifier in reserved
                or not isinstance(label, str) or not label.strip()
                or label != label.strip() or "\n" in label or "\r" in label or label in fields):
            raise ValueError("Issue Form IDs and labels must be unique and valid.")
        identifiers.add(identifier)
        fields[label] = identifier
        if identifier == "public_data_confirmation":
            options = attributes.get("options", [])
            if (field.get("type") != "checkboxes" or not isinstance(options, list) or len(options) != 1
                    or not isinstance(options[0], dict)
                    or options[0].get("required") is not True
                    or not isinstance(options[0].get("label"), str) or not options[0]["label"].strip()):
                raise ValueError("Issue Form must require the public-data confirmation.")
            consent = options[0]["label"]
        elif field.get("type") not in ("input", "textarea", "dropdown"):
            raise ValueError("Unsupported research Issue Form field type.")
        elif validations.get("required") is True:
            required.append(identifier)
    if (not {"research_goal", "research_question"}.issubset(required) or consent is None):
        raise ValueError("Issue Form must require the research goal, question and confirmation.")
    return {"fields": fields, "required": required, "consent": consent}


def issue_number(value):
    if type(value) is int and value > 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,9}", value):
        return int(value)
    raise ValueError("Issue number must be a positive decimal integer.")


def run_identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", value):
        raise ValueError("Invalid GitHub Actions run identifier.")
    return value


def repository_name(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*/[A-Za-z0-9_][A-Za-z0-9_.-]*", value
    ):
        raise ValueError("Invalid repository name.")
    return value


def parse_issue(issue, repository, *, form_path=None):
    repository = repository_name(repository)
    number = issue_number(issue.get("number"))
    if "pull_request" in issue:
        return None
    body = issue.get("body")
    title = issue.get("title")
    if not isinstance(body, str):
        return None
    contract = form_contract(form_path)
    fields = contract["fields"]
    headings = list(re.finditer(r"^### ([^\r\n]+)\s*$", body, re.MULTILINE))
    sections = [match for match in headings if match.group(1).strip() in fields]
    if not sections:
        return None
    if (len(body) > 65536 or "\x00" in body or not isinstance(title, str)
            or not title.strip() or len(title) > 256 or "\x00" in title):
        raise ValueError("Research form text is malformed.")
    labels = [match.group(1).strip() for match in sections]
    if len(labels) != len(set(labels)):
        raise ValueError("Research form has duplicate sections.")
    if labels != list(fields) or body[:sections[0].start()].strip():
        raise ValueError("Research form signature, section order or required sections are missing.")
    values = {}
    for index, match in enumerate(sections):
        end = sections[index + 1].start() if index + 1 < len(sections) else len(body)
        value = body[match.end():end].strip()
        values[fields[labels[index]]] = "" if value == "_No response_" else value
    if any(not values[identifier] for identifier in contract["required"]):
        raise ValueError("Research goal and question must be nonempty.")
    if not re.fullmatch(r"- \[[xX]\] " + re.escape(contract["consent"]), values["public_data_confirmation"]):
        raise ValueError("The exact public-data confirmation must be checked.")
    values["public_data_confirmation"] = True
    return {
        "schema_version": 1, "repository": repository, "issue_number": number,
        "issue_url": f"https://github.com/{repository}/issues/{number}",
        "title": title, **values,
        "selection_execution": False, "prediction_time": None,
    }


def command(arguments, cwd=None, allowed=(0,), *, input_text=None):
    try:
        result = subprocess.run(
            arguments, cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=600,
            input=input_text,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError("External command could not complete; inputs and credentials are not logged.") from None
    if result.returncode not in allowed:
        if arguments[:3] == ["gh", "pr", "create"]:
            raise RuntimeError(
                "검토용 draft PR 생성 실패. 해당 저장소의 Settings → Actions → General → "
                "Workflow permissions → Allow GitHub Actions to create and approve pull requests와 "
                "워크플로의 pull-requests: write 권한을 확인하세요. "
                "템플릿 설정은 복사본에 상속되지 않습니다. 생성된 결과 브랜치는 보존되며 "
                "설정 수정 후 실패한 실행을 재실행할 수 있습니다. "
                f"(exit {result.returncode}; 입력·인증정보·응답 원문은 출력하지 않습니다.)"
            )
        if arguments[:3] == ["gh", "workflow", "run"]:
            raise RuntimeError(
                "Copilot 추천 워크플로 요청 실패. 기본 브랜치의 research-plan.lock.yml과 "
                "workflow_dispatch 입력, 요청 워크플로의 actions: write 권한을 확인하세요. "
                "이미 생성된 검토 PR과 사용자 선택은 보존됩니다. "
                f"(exit {result.returncode}; 입력·인증정보·응답 원문은 출력하지 않습니다.)"
            )
        raise RuntimeError(
            f"{arguments[0]} command failed (exit {result.returncode}); inputs and credentials are not logged."
        )
    return result


def gh_json(*arguments):
    result = command(["gh", "api", *arguments])
    try:
        return json.loads(result.stdout)
    except ValueError:
        raise RuntimeError("GitHub returned invalid JSON; response content is not logged.") from None


def intake(event, event_name, repository, actor, manual_number=None):
    repository_name(repository)
    if (event.get("repository", {}).get("full_name") != repository
            or not isinstance(actor, str) or not re.fullmatch(r"[A-Za-z0-9-]+(?:\[bot\])?", actor)):
        raise ValueError("Event repository or actor is inconsistent.")
    if event_name == "issues":
        if event.get("action") != "opened":
            return None, {"status": "not_applicable", "reason": "Only newly opened Issues are automated."}
        original = event.get("issue") or {}
        if original.get("author_association") not in TRUSTED_ASSOCIATIONS:
            return None, {
                "status": "deferred",
                "reason": "Automatic runs require a repository owner or collaborator; a maintainer must dispatch this Issue manually.",
            }
        if original.get("user", {}).get("login") != actor:
            raise ValueError("Issue author and event actor do not match.")
        number = issue_number(original.get("number"))
    elif event_name == "workflow_dispatch":
        number = issue_number(manual_number)
        permission = gh_json(f"repos/{repository}/collaborators/{actor}/permission")
        if permission.get("permission") not in ("admin", "maintain", "write"):
            return None, {"status": "deferred", "reason": "Manual runs require maintainer/write permission."}
    else:
        raise ValueError("Unsupported workflow event.")
    issue = gh_json(f"repos/{repository}/issues/{number}")
    if issue.get("number") != number:
        raise ValueError("GitHub Issue number does not match the request.")
    if event_name == "issues" and issue.get("author_association") not in TRUSTED_ASSOCIATIONS:
        return None, {"status": "deferred", "reason": "Issue author is no longer a trusted collaborator."}
    if issue.get("state") != "open":
        return None, {"status": "deferred", "reason": "Closed Issues are not automatically republished."}
    request = parse_issue(issue, repository)
    if request is None:
        return None, {"status": "not_applicable", "reason": "Not the registered five-field research form."}
    return request, None


def data_reference(config):
    if config.get("schema_version") != 1:
        raise ValueError("Unsupported asset reference configuration.")
    return validate_image_reference(config.get("data_image"))


def image_assets(reference, destination):
    reference = validate_image_reference(reference)
    if destination.exists():
        raise ValueError("Image asset destination already exists.")
    container = "research-assets-" + uuid.uuid4().hex
    created = False
    try:
        docker("pull", reference)
        docker("create", "--name", container, "--network", "none", reference)
        created = True
        docker("cp", container + ":/opt/workshop/assets", str(destination))
    finally:
        if created:
            docker("rm", container)


def validate_aggregate(eda, source, expected_ids=None):
    if (eda.get("kind") != "aggregate-eda" or eda.get("report_generated") is not True
            or any(eda.get(name) is not False for name in (
                "model_execution_performed", "training_performed", "preprocessing_performed"))
            or not re.fullmatch(r"[0-9a-f]{64}", eda.get("data_id") or "")):
        raise ValueError("Invalid aggregate report contract.")
    blocks = eda.get("results", [])
    identifiers = [item.get("block") for item in blocks]
    if (not {"schema", "patient_overlap"}.issubset(identifiers)
            or len(identifiers) != len(set(identifiers))
            or set(identifiers) - set(CHECKS)
            or (expected_ids is not None and identifiers != list(expected_ids))
            or any(item.get("status") not in ("passed", "failed", "unverifiable", "completed")
                   for item in blocks)):
        raise ValueError("Required and expected selected EDA blocks must match the registered catalog.")
    by_name = {item["block"]: item for item in blocks}
    schema = by_name["schema"]["details"]
    demographics = by_name.get("demographics")
    cohort = by_name.get("cohort", {}).get("details")
    generic = "source_profile" in source
    overlaps = by_name["patient_overlap"]["details"].get("overlaps")
    expected_pairs = {left + "_" + right: min(source["splits"][left]["rows"], source["splits"][right]["rows"])
                      for left, right in (("train", "validation"), ("train", "test"), ("validation", "test"))}
    if (not isinstance(overlaps, dict) or set(overlaps) != set(expected_pairs)
            or any(type(count) is not int or not 0 <= count <= expected_pairs[pair]
                   for pair, count in overlaps.items())):
        raise ValueError("환자 중복 집계의 분할·건수 계약이 손상되었습니다.")
    if (schema.get("numeric_contract") != "passed"
            or schema.get("actual_count") != len(source["features"])
            or schema.get("rows") != sum(entry["rows"] for entry in source["splits"].values())
            or schema.get("split_rows") != {key: entry["rows"] for key, entry in source["splits"].items()}
            or ("expected_patient_overlap" in source and overlaps != source["expected_patient_overlap"])):
        raise ValueError("Input structure or recorded prepared-data alignment is damaged.")
    if cohort is not None:
        if cohort.get("duplicate_admission_keys") != 0:
            raise ValueError("Input structure or recorded prepared-data alignment is damaged.")
        for split in SPLITS:
            if cohort["counts"][split]["admissions"] != source["splits"][split]["rows"]:
                raise ValueError("Aggregate split count is damaged.")
    if demographics is not None and not generic:
        if demographics["status"] != "completed":
            raise ValueError("Input structure or recorded prepared-data alignment is damaged.")
        if demographics["details"]["original_race_missing"].get("test") != source["expected_test_race_missing"]:
            raise ValueError("Original missing-race aggregate differs from the registered source.")
    if (not generic and "label_observability" in by_name and by_name["label_observability"]["details"].get(
            "target_definition", {}).get("status") != "passed"):
        raise ValueError("Input structure or recorded prepared-data alignment is damaged.")
    def aggregate_only(value):
        if isinstance(value, dict):
            if {"row_key", "patient_key", "row_keys", "patient_keys", "metadata",
                "feature_values", "predictions"}.intersection(value):
                raise ValueError("Only aggregate report content may be published.")
            for item in value.values():
                aggregate_only(item)
        elif isinstance(value, list):
            for item in value:
                aggregate_only(item)
    aggregate_only(eda)
    return by_name


def cell(value):
    return html.escape(str(value)).replace("|", "\\|").replace("\r", "").replace("\n", "<br>")


def plan_report_lines(plans):
    lines = []
    for stage in ("eda", "evaluation"):
        current = plans[stage]
        lines += ["", f"### {stage}: 기본 정책 + 실제 AI 추천 + 사용자 선택",
                  f"AI: `{current['ai_status']}`; planning_complete: `{current['planning_complete']}`.",
                  "", "| 선택 ID | 출처 | 상태 | 이유 / 차단 근거 |", "|---|---|---|---|"]
        for item in current["items"]:
            lines.append(f"| `{item['id']}` | {cell(', '.join(item['sources']))} | "
                         f"`{item['state']}` | {cell(item['reason'])} |")
        lines.append("사용자 제외: " + (", ".join(current["user_excluded_ids"]) or "없음"))
    readiness = planning.approval_readiness(plans)
    lines += [
        "", "## 사용자 선택과 승인",
        f"교육용 계획 승인 준비: **{'완료' if readiness['ready'] else '미완료'}**.",
        *(f"- {reason}" for reason in readiness["reasons"]),
        "", "PR의 Conversation에 자연어 피드백을 작성할 수 있습니다. "
        "현재 write/maintain/admin 권한이 있는 사람의 요청만 AI 조율과 Python 검증을 거쳐 같은 PR에 반영합니다.",
        "기존 `/plan`도 지원합니다. 명령을 사용할 때는 명령만 별도 댓글로 작성합니다. 아래는 사용법 예시입니다.",
        "", "```text", "/plan add eda prediction_timing", "/plan add evaluation f1",
        "/plan primary average_precision", "/plan threshold 0.3", "```",
        "", "제외는 `/plan remove eda|evaluation ID`, 매개변수는 "
        "`/plan parameter top_fraction 0.1`, AI 재추천은 `/plan recommend`를 사용합니다.",
        "카탈로그에 등록된 ID만 처리하며 필수 schema/patient_overlap은 제외할 수 없습니다.",
        "자연어는 실행 코드가 아닌 수정 요청입니다. 처리된 댓글 편집은 재적용하지 않으므로 새 댓글로 요청하세요.",
        "반영할 피드백이 남아 있거나 준비 이후 본문·권한·연구 요청이 바뀌면 게시/승인 기록을 중단합니다.",
        "AI 추천 provided, 주지표 선택, blocked 평가 항목 없음 상태를 확인한 뒤 "
        "Ready for review → Merge합니다. 실패·대기 상태로 병합하면 승인 기록을 만들지 않습니다.",
        "사람이 Merge한 시점의 선택·계획·자산·해시를 `research/approved/`에 새 불변 기록으로 보존합니다.",
        "이 승인은 교육용 계획 확정이며 모델 실행·최종 평가·임상 사용 승인이 아닙니다.",
        "", "DCA는 임상 행동·threshold 범위가 정해지기 전 보류합니다. 비교모델 없는 NRI는 적용하지 않습니다.",
        "계획은 독립·누수 없는 임상 평가셋 인증이 아닙니다.",
        "검토용 초안이며 자동 merge·후속 모델 실행을 하지 않습니다.", "",
    ]
    return lines


def markdown_report(request, eda, provenance, plans):
    if eda.get("dataset_context", {}).get("legacy") is False:
        from app.input_review import generic_report
        return generic_report(request, eda, provenance, plans)
    by_name = {item["block"]: item for item in eda["results"]}
    cohort = by_name.get("cohort", {}).get("details")
    plan = plans["evaluation"]
    commit_label = "로컬 기준 commit (미커밋 변경 포함)" if provenance.get("dry_run") else "실행 코드 commit"
    run_label = "모의 실행링크 (실제 실행 아님)" if provenance.get("dry_run") else "실행 근거"
    lines = [
        "# 연구검토: 데이터 점검 결과와 평가 계획 초안",
        "", f"원본 Issue: {request['issue_url']}",
        "", "**집계 계산·보고서 생성 성공과 독립 연구 적합성 통과는 다릅니다.**",
        f"- 보고서 생성: `{eda['report_generated']}`",
        f"- 데이터 점검 판정: `{eda['assessment_status']}` (환자 중복과 근거 부족을 보존)",
        "- 모델 실행·지표 계산·임상 사용 승인·임계값 최적화: 수행하지 않음",
        f"- AI 추천: `{plan['ai_status']}` / 계획 조합 완료: `{plan['planning_complete']}`",
        "- pending/stale은 AI 대기 초안이며 빈 AI 성공 placeholder를 만들지 않습니다.",
        "- 연구 요청은 `request.json`에 보존. 자유서술 선정조건은 실행하지 않음.",
        "", "## 재현 근거",
        "", "| 항목 | 값 |", "|---|---|",
        f"| data 이미지 | `{provenance['data_image']}` |",
        f"| data_id | `{eda['data_id']}` |",
        f"| prepared revision | `{eda.get('prepared_revision')}` |",
        f"| {commit_label} | `{provenance['code_commit']}` |",
        f"| {run_label} | {provenance['workflow_run_url']} |",
        "", "입력 경로: 검증된 준비파일 또는 명시된 기존 집계 재사용입니다.",
        f"EDA 실행 방식: `{eda.get('execution_mode', 'selected_execution')}`. "
        + eda.get("execution_note", "선택한 데이터 점검 블록만 실행했습니다."),
        f"계획용 metadata mock 여부: `{provenance.get('planning_metadata_mock', False)}`.",
        "실행 중인 DB에 질의한 결과가 아닙니다.",
        "", "## 블록 상태",
        "", "| 블록 | 상태 |", "|---|---|",
    ]
    for item in eda["results"]:
        lines.append(f"| `{item['block']}` | `{item['status']}` |")
    lines.append("\n미선택 데이터 점검: " + (", ".join(name for name in CHECKS if name not in by_name) or "없음"))
    lines += [
        "", "`passed`: 해당 범위 충족 / `failed`: 측정 위반 / `unverifiable`: 필요한 근거 부족 / `completed`: 기술 집계 완료.",
        "", "## 분할·사건 수",
        "", "| 분할 | 입원 | 고유 환자 | target 0 | target 1 | 사건비율(입원 분모) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for split in SPLITS:
        entry = cohort["counts"][split] if cohort else {
            "admissions": by_name["schema"]["details"]["split_rows"][split],
            "patients": "미측정", "target_0": "미측정", "target_1": "미측정", "event_rate": None}
        rate = f"{entry['event_rate']:.4%}" if entry["event_rate"] is not None else "미측정"
        lines.append(
            f"| {split} | {entry['admissions']} | {entry['patients']} | {entry['target_0']} | "
            f"{entry['target_1']} | {rate} |"
        )
    lines += [
        "", f"전체 고유 환자: **{cohort['unique_patients']}**." if cohort else "cohort 미선택: 환자/사건 집계를 새로 계산하지 않았습니다.",
        "반복 입원을 임의 제거하거나 초회 입원만 선택하지 않았습니다.",
        "환자는 여러 입원·양쪽 target에 기여할 수 있어 부분집단 환자 수를 합산하지 않습니다.",
        "", "### 환자 중복 / 원본 인종 결측",
        "", "| 분할 쌍 | 고유 환자 중복 |", "|---|---:|",
    ]
    for pair, count in by_name["patient_overlap"]["details"]["overlaps"].items():
        lines.append(f"| {cell(pair)} | {count} |")
    demo = by_name.get("demographics", {}).get("details")
    if demo:
        lines += ["", "| 분할 | 원본 race 결측 | 공개 Caucasian 대치 |", "|---|---:|---:|"]
    for split in SPLITS:
        if not demo:
            continue
        lines.append(f"| {split} | {demo['original_race_missing'][split]} | {demo['caucasian_imputed'][split]} |")
    if demo:
        lines += ["", "### 인구집단별 기술 집계", "", "공개 `race`와 관측 `race_original`은 별도 표입니다. 대치는 실제 관측 인종이 아닙니다."]
    for split in SPLITS:
        if not demo:
            continue
        for column, groups in demo["distributions"][split].items():
            lines += [
                "", f"#### {split} / {column}", "",
                "| 집단 | 입원 | 고유 환자 | target 0 | target 1 | 사건비율(입원 분모) |",
                "|---|---:|---:|---:|---:|---:|",
            ]
            for group, entry in sorted(groups.items()):
                lines.append(
                    f"| {cell(group)} | {entry['admissions']} | {entry['patients']} | "
                    f"{entry['target_0']} | {entry['target_1']} | {entry['event_rate']:.4%} |"
                )
    schema = by_name["schema"]["details"]
    constants = [name for name, entry in schema["quality"].items() if entry["constant"]]
    lines += [
        "", "## 품질·선정·관측·이력의 해석 경계",
        f"- 수치 계약: `{schema['numeric_contract']}`, {schema['actual_count']}개 특성 / {schema['rows']}행.",
        f"- 관측된 상수 특성: {len(constants)}개. 삭제하거나 임상 범위 오류로 판정하지 않았습니다.",
        "- 표준화된 특성의 임상 의미·단위는 확인 불가입니다. 변수별 타입/finite/결측/min/max는 `eda.json`에 보존합니다.",
        "- 출처 선정 설명: 입원·당뇨 진단·1–14일 재원·검사·투약 조건. 실제 행별 검사와 별개입니다.",
        f"- 원본 선정조건 행별 검사: `{cohort['row_level_selection']['status'] if cohort else '미선택'}`. 이미지에는 raw 원본/선정 전 모집단·제외 수가 없습니다.",
        "- 연구 단위·추가 선정조건은 검토 전 미확정이며 환자/특성을 임의 삭제하지 않습니다.",
        "- 승인된 예측시점·실제 timestamp·특성 가용시점 사전이 없어 입원완료/퇴원정보의 입원시점 사용을 인증하지 않습니다.",
        "- `<30`=1, `>30`/`NO`=0 인코딩과 완전한 30일 추적/외부병원 포착은 별개입니다. 관찰 종료자료가 없어 음성을 완전추적 무사건으로 보지 않습니다.",
        "- 고정 hash 일치는 누수 없음의 증거가 아닙니다. 역사적 113-feature 변환의 fit 범위 미확인·split 전 target encoding 우려·validation 개발 사용을 보존합니다.",
        "- 임의 최소표본수/통계유의성 기준을 만들지 않았습니다. 각 블록의 상세 근거·집계·한계는 `eda.json`에 있습니다.",
        "", "## 평가 계획 추천 초안",
        "", f"사용자 선택 primary_metric: `{plan['primary_metric']}`, threshold: `{plan['threshold']}`. "
        "자동 승인·계산 결과가 아니며 임상 사용은 미승인입니다.",
    ]
    lines.extend(plan_report_lines(plans))
    return "\n".join(lines)


def report_directory(root, number):
    number = issue_number(number)
    directory = root / "research" / "reports" / f"issue-{number}"
    for path in (directory, *directory.parents):
        if path == root.parent:
            break
        if path.is_symlink():
            raise ValueError("Report paths must not traverse symlinks.")
    if not directory.resolve().is_relative_to(root.resolve()):
        raise ValueError("Report path must stay inside the allowed output root.")
    return directory


def selected_report(eda, identifiers, context=None):
    indexed = {item["block"]: item for item in eda["results"]}
    missing = [name for name in identifiers if name not in indexed]
    live = context is not None and "metadata" in context
    if missing:
        if not live:
            raise ValueError("Aggregate projection cannot supply an unmeasured selected EDA block.")
        indexed.update({item["block"]: item for item in report(context, identifiers=missing)["results"]})
    result = copy.deepcopy(eda)
    result.pop("provenance", None)
    result["results"] = [copy.deepcopy(indexed[name]) for name in identifiers]
    statuses = [item["status"] for item in result["results"]]
    result["assessment_status"] = "failed" if "failed" in statuses else (
        "unverifiable" if "unverifiable" in statuses else "completed")
    result["execution_mode"] = "selected_execution" if live else "projection_reuse"
    result["execution_note"] = (
        "필수 점검의 집계를 재사용하고 추가로 선택된 데이터 점검만 실행했습니다."
        if live else "기존 집계에서 선택된 결과를 투영해 재사용했습니다. 새 EDA 실행이나 지표 계산이 아닙니다.")
    return result


def payload_plans(directory, eda, context, catalogs, policy):
    selection_path = directory / "selection.json"
    if selection_path.is_file():
        selection = read_json(selection_path)
    else:
        selection = copy.deepcopy((context or {}).get("_initial_selection"))
        if selection is None:
            selection = planning.default_selection()
        for stage in ("eda", "evaluation"):
            selection[stage]["objectives"] = list(policy[stage]["objectives"])
        legacy_path = directory / "evaluation-plan.json"
        if legacy_path.is_file():
            legacy = read_json(legacy_path)
            for name in ("primary_metric", "threshold", "parameters", "clinical_action", "comparison_model"):
                if legacy.get(name) is not None:
                    selection["evaluation"][name] = copy.deepcopy(legacy[name])
            primary = selection["evaluation"]["primary_metric"]
            if primary is not None:
                selection["evaluation"]["add"].append(primary)
            parameters = selection["evaluation"]["parameters"]
            if selection["evaluation"]["threshold"] is not None and "threshold" in parameters:
                if parameters["threshold"] != selection["evaluation"]["threshold"]:
                    raise ValueError("Legacy threshold migration contains conflicting manual values.")
                parameters.pop("threshold")
    ai_path = directory / "ai-recommendations.json"
    ai = read_json(ai_path) if ai_path.is_file() else None
    if context is not None and "data_manifest" in context:
        manifest = context["data_manifest"]
        metadata_mock = False
    else:
        source = read_json(CONFIG)
        manifest = {
            "data_id": eda["data_id"], "prepared_source": source,
            "feature_columns": source["features"],
            "split_rows": {name: source["splits"][name]["rows"] for name in SPLITS},
            "planning_metadata_mock": True,
        }
        metadata_mock = True
    plans = planning.build_plans(eda, manifest, selection, ai, catalogs=catalogs, policy=policy)
    plans["recommendation_request"]["instructions"] += (
        "\n모든 추천 이유(reason)는 한국어로 작성하세요. 코드 식별자와 근거 ID는 변경하지 마세요.")
    for stage, collection in (("eda", "checks"), ("evaluation", "metrics")):
        aliases = {}
        for entry in catalogs[stage][collection]:
            for token in (entry["id"], *entry["aliases"]):
                aliases[re.sub(r"[\s-]+", "_", token.strip().lower())] = entry["id"]
        reasons = {}
        if ai is not None and plans[stage]["ai_status"] == "provided":
            for entry in ai[stage]:
                identifier = aliases[re.sub(r"[\s-]+", "_", entry["id"].strip().lower())]
                reasons.setdefault(identifier, []).append(entry["reason"])
        for item in plans[stage]["items"]:
            blocked = []
            for reason in item["blocked_reasons"]:
                for prefix, korean in (
                    ("Missing planned input: ", "계획된 입력이 없습니다: "),
                    ("Missing approved parameter: ", "승인된 매개변수가 없습니다: "),
                    ("Missing defined context: ", "정의된 검토 조건이 없습니다: "),
                ):
                    if reason.startswith(prefix):
                        reason = korean + reason[len(prefix):]
                        break
                blocked.append(reason)
            item["blocked_reasons"] = blocked
            fragments = []
            if "default" in item["sources"]:
                fragments.append("기본 정책에 포함된 블록입니다.")
            fragments.extend(reasons.get(item["id"], []))
            if "user" in item["sources"]:
                fragments.append("사용자가 추가한 블록입니다.")
            fragments.extend(blocked)
            fragments.append("자료 부족도 확인 불가로 보고하는 진단입니다." if stage == "eda"
                             else "향후 계산 계획이며 아직 지표를 계산하거나 임상 사용을 승인하지 않았습니다.")
            item["reason"] = " ".join(fragments)
        if stage == "evaluation":
            plans[stage]["interpretation"] = (
                "계산 계획은 임상 사용 승인이나 독립 검증 성능 주장이 아닙니다. "
                "등록된 데이터셋의 관측·누수·전처리 이력 제한을 유지합니다."
                if "source_profile" in manifest["prepared_source"] else
                "계산 계획은 임상 사용 승인이나 독립 검증 성능 주장이 아닙니다. 환자 중복과 validation 개발 사용의 한계를 유지합니다.")
    chosen = selected_report(eda, plans["eda"]["selected_ids"], context)
    validate_aggregate(chosen, manifest["prepared_source"], plans["eda"]["selected_ids"])
    return selection, plans, chosen, metadata_mock


def write_payload(root, request, eda, provenance, *, context=None, catalogs=None, policy=None):
    directory = report_directory(root, request["issue_number"])
    directory.mkdir(parents=True, exist_ok=True)
    for name in (*OUTPUT_NAMES, "ai-recommendations.json", "input-schema.json", "input-mapping.json",
                 "execution-plan.json"):
        if (directory / name).is_symlink():
            raise ValueError("Generated report files must not be symlinks.")
    catalogs = catalogs if catalogs is not None else {
        stage: planning.load_catalog(stage) for stage in ("eda", "evaluation")}
    policy = policy if policy is not None else planning.load_policy()
    selection, plans, enriched, metadata_mock = payload_plans(directory, eda, context, catalogs, policy)
    enriched["provenance"] = copy.deepcopy(provenance)
    enriched["provenance"]["planning_metadata_mock"] = metadata_mock
    plan = plans["evaluation"]
    previous = directory / "eda.json"
    if previous.is_file():
        try:
            old = read_json(previous)
            prior = old.get("provenance", {})
            comparable_old = copy.deepcopy(old)
            comparable_new = copy.deepcopy(enriched)
            comparable_old.get("provenance", {}).pop("workflow_run_url", None)
            comparable_new["provenance"].pop("workflow_run_url", None)
            if (comparable_old == comparable_new
                    and read_json(directory / "request.json") == request
                    and read_json(directory / "evaluation-plan.json") == plan
                    and re.fullmatch(
                        re.escape(f"https://github.com/{request['repository']}/actions/runs/") + r"[1-9][0-9]*",
                        prior.get("workflow_run_url", ""),
                    )):
                # Retain the original execution evidence on semantically unchanged reruns.
                enriched["provenance"]["workflow_run_url"] = prior["workflow_run_url"]
        except (OSError, ValueError, KeyError):
            pass
    write_json(directory / "request.json", request)
    write_json(directory / "eda.json", enriched)
    write_json(directory / "evaluation-plan.json", plan)
    write_json(directory / "eda-plan.json", plans["eda"])
    write_json(directory / "recommendation-request.json", plans["recommendation_request"])
    if not (directory / "selection.json").exists():
        write_json(directory / "selection.json", selection)
    markdown = markdown_report(request, enriched, enriched["provenance"], plans)
    names = [*OUTPUT_NAMES]
    if context is not None and context.get("input_contract") is not None:
        from app.input_review import mapping_lines
        write_json(directory / "input-mapping.json", context["input_contract"])
        write_json(directory / "input-schema.json", context["_input_schema_request"])
        markdown += "\n" + "\n".join(mapping_lines(context["input_contract"]))
        names.extend(("input-mapping.json", "input-schema.json"))
    if context is not None and context.get("_pr1_coordination") is not None:
        coordination = context["_pr1_coordination"]
        write_json(directory / "coordination-plan.json", coordination)
        markdown += ("\n## AI 연구 조율\n\n" + cell(coordination["record"]["reason"])
                     + "\n\n공개 연구 목적·스키마와 권한 있는 사람의 피드백을 기준으로 구성한 수정안입니다."
                     "\n최종 승인은 사람의 Merge이며 모델 실행 승인이 아닙니다.\n")
        names.append("coordination-plan.json")
    if context is not None and context.get("_execution_plan") is not None:
        execution = context["_execution_plan"]
        write_json(directory / "execution-plan.json", execution)
        markdown += ("\n## EDA 실행 계약\n\n"
                     f"계획 hash: `{execution['plan_hash']}`. "
                     "시작 데이터·컬럼 역할·등록된 블록의 입력 참조를 고정한 단방향 계획입니다. "
                     "실행 중 데이터 교체·자동 재계획은 하지 않습니다.\n")
        names.append("execution-plan.json")
    (directory / "report.md").write_text(markdown, encoding="utf-8")
    if (directory / "ai-recommendations.json").is_file():
        names.append("ai-recommendations.json")
    return [str((directory / name).relative_to(root)).replace("\\", "/") for name in names]


def existing_pull(repository, branch, default_branch):
    pages = gh_json(
        "--method", "GET", f"repos/{repository}/pulls",
        "-f", "state=all", "-f", "head=" + repository.split("/")[0] + ":" + branch,
        "-f", "per_page=100", "--paginate", "--slurp",
    )
    pulls = [item for page in pages for item in page]
    if any(item.get("state") == "closed" for item in pulls):
        return None, {"status": "deferred", "reason": "This Issue's review PR was closed or merged; automatic recreation/reopening is prohibited."}
    if len(pulls) > 1:
        raise ValueError("Multiple review PRs need maintainer reconciliation.")
    if not pulls:
        return None, None
    pull = pulls[0]
    if (pull.get("head", {}).get("repo", {}).get("full_name") != repository
            or pull["head"].get("ref") != branch or pull.get("base", {}).get("ref") != default_branch):
        raise ValueError("Existing review PR repository or branch is inconsistent.")
    issue_number(pull.get("number"))
    return pull, None


def merge_auto_body(current, generated, legacy=None):
    current = current or ""
    if current.count(AUTO_START) != current.count(AUTO_END) or current.count(AUTO_START) > 1:
        raise ValueError("Review PR has duplicate or broken automatic-area markers.")
    block = AUTO_START + "\n" + generated.rstrip() + "\n" + AUTO_END
    if AUTO_START in current:
        start, end = current.index(AUTO_START), current.index(AUTO_END)
        if end < start:
            raise ValueError("Review PR has duplicate or broken automatic-area markers.")
        return current[:start] + block + current[end + len(AUTO_END):]
    if not current.strip() or (legacy is not None and current.strip() == legacy.strip()):
        return block
    return block + "\n\n" + current


def merge_review_body(existing_body, generated_report):
    return merge_auto_body(existing_body, generated_report)


def publish_payload(root, request, eda, provenance, default_branch, *,
                    context=None, catalogs=None, policy=None, input_request=None,
                    input_phase="input_mapping"):
    pin_review_code()
    repository = request["repository"]
    branch = f"research/review-issue-{request['issue_number']}"
    command(["git", "check-ref-format", "--branch", default_branch], cwd=root)
    if command(["git", "status", "--porcelain"], cwd=root).stdout.strip():
        raise ValueError("Publication requires a clean checkout.")
    pull, deferred = existing_pull(repository, branch, default_branch)
    if deferred:
        return deferred
    remote = command(["git", "ls-remote", "--heads", "origin", "refs/heads/" + branch], cwd=root).stdout.strip()
    if pull and not remote:
        raise ValueError("Existing review PR has no matching remote branch.")
    if remote:
        command(["git", "fetch", "origin", f"refs/heads/{branch}:refs/remotes/origin/{branch}"], cwd=root)
        command(["git", "checkout", "-b", branch, "refs/remotes/origin/" + branch], cwd=root)
        marker = report_directory(root, request["issue_number"]) / "request.json"
        if not marker.is_file() or marker.is_symlink():
            raise ValueError("Existing reserved branch is not a generated research-review branch.")
        previous = read_json(marker)
        if previous.get("repository") != repository or previous.get("issue_number") != request["issue_number"]:
            raise ValueError("Existing branch ownership marker is inconsistent.")
    else:
        command(["git", "checkout", "-b", branch], cwd=root)
    directory = report_directory(root, request["issue_number"])
    previous_report = directory / "report.md"
    legacy = previous_report.read_text(encoding="utf-8") if previous_report.is_file() else None
    if pull:
        merge_auto_body(pull.get("body"), "", legacy)
    if input_request is not None:
        from app.input_review import write_pending
        paths = write_pending(root, request, input_request, provenance,
                              context["_initial_selection"], phase=input_phase)
        published = {"assessment_status": "not_run"}
        evaluation = {"ai_status": "pending_" + input_phase, "planning_complete": False}
    else:
        paths = write_payload(root, request, eda, provenance, context=context, catalogs=catalogs, policy=policy)
        published = read_json(directory / "eda.json")
        evaluation = read_json(directory / "evaluation-plan.json")
    generated = (directory / "report.md").read_text(encoding="utf-8")
    body = merge_auto_body(pull.get("body") if pull else "", generated, legacy)
    command(["git", "add", "--", *paths], cwd=root)
    staged = command(["git", "diff", "--cached", "--name-only"], cwd=root).stdout.splitlines()
    if not set(staged).issubset(paths):
        raise ValueError("Only generated report files may be committed.")
    changed = command(["git", "diff", "--cached", "--quiet", "--", *paths], cwd=root, allowed=(0, 1)).returncode == 1
    if changed:
        command([
            "git", "-c", "user.name=github-actions[bot]",
            "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
            "commit", "-m", f"Prepare research review for Issue #{request['issue_number']}",
        ], cwd=root)
        command([
            "git", "-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential",
            "push", "origin", "HEAD:refs/heads/" + branch,
        ], cwd=root)
    if pull:
        number = issue_number(pull["number"])
        body_updated = body != (pull.get("body") or "")
        if body_updated:
            command(["gh", "api", "--method", "PATCH", f"repos/{repository}/pulls/{number}",
                     "--input", "-"], cwd=root, input_text=json.dumps({"body": body}, ensure_ascii=False))
        return {
            "status": "updated" if changed or body_updated else "unchanged", "branch": branch,
            "pull_url": f"https://github.com/{repository}/pull/{number}",
            "assessment_status": published["assessment_status"], "ai_status": evaluation["ai_status"],
            "planning_complete": evaluation["planning_complete"], "body_updated": body_updated,
            "policy": "Only the automatic PR body area changes; human text, title, comments and other files are preserved.",
        }
    if not changed and not remote:
        raise ValueError("A new review branch has no report changes to publish.")
    body_path = Path((context or {}).get("_review_work", directory)) / ("pr-body-" + uuid.uuid4().hex + ".txt")
    with body_path.open("x", encoding="utf-8") as handle:
        handle.write(body)
    try:
        result = command([
            "gh", "pr", "create", "--repo", repository, "--head", branch, "--base", default_branch,
            "--draft", "--title", f"연구검토 #{request['issue_number']}: 데이터 점검 결과와 평가 계획 초안",
            "--body-file", str(body_path),
        ], cwd=root)
    finally:
        body_path.unlink(missing_ok=True)
    url = result.stdout.strip()
    if not re.fullmatch(re.escape(f"https://github.com/{repository}/pull/") + r"[1-9][0-9]*", url):
        raise RuntimeError("GitHub PR creation returned an unexpected response.")
    return {"status": "created", "branch": branch, "pull_url": url,
            "assessment_status": published["assessment_status"], "draft": True,
            "ai_status": evaluation["ai_status"], "planning_complete": evaluation["planning_complete"]}


def dispatch_ai(outcome, repository, default_branch):
    if outcome.get("status") not in ("created", "updated", "unchanged"):
        return {"status": "not_applicable"}
    if outcome.get("ai_status") not in ("pending", "stale", "pending_input_mapping", "pending_coordination"):
        return {"status": "not_needed", "ai_status": outcome.get("ai_status")}
    prefix = f"https://github.com/{repository}/pull/"
    url = outcome.get("pull_url", "")
    if not url.startswith(prefix):
        raise ValueError("AI dispatch requires the current repository's review PR.")
    number = issue_number(url[len(prefix):])
    # The completed workflow event starts Copilot without changing actor to a bot.
    return {"status": "requested", "workflow": "research-plan.lock.yml",
            "pull_request": number, "trigger": "workflow_run", "default_branch": default_branch}


def run_workflow(event, event_name, repository, actor, default_branch, work, manual_number=None):
    pin_review_code()
    request, decision = intake(event, event_name, repository, actor, manual_number)
    if decision:
        return decision
    branch = f"research/review-issue-{request['issue_number']}"
    previous_pull, deferred = existing_pull(repository, branch, default_branch)
    if deferred:
        return deferred
    if (work.exists() or work.resolve().is_relative_to(ROOT.resolve())
            or work.resolve() == work.resolve().parent):
        raise ValueError("Use a new, owned runtime directory outside the checkout.")
    reference = data_reference(read_json(ROOT / "config" / "assets.json"))
    catalogs = {stage: planning.load_catalog(stage) for stage in ("eda", "evaluation")}
    policy = planning.load_policy()
    initial_selection = planning.default_selection()
    commit = command(["git", "rev-parse", "HEAD"], cwd=ROOT).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Invalid trusted code commit.")
    run_id = run_identifier(os.environ.get("GITHUB_RUN_ID"))
    provenance = {
        "data_image": reference, "code_commit": commit,
        "workflow_run_url": f"https://github.com/{repository}/actions/runs/{run_id}",
        "data_access": "Validated prepared files extracted from the data image; no database queries.",
    }
    work.mkdir(parents=True)
    try:
        image_assets(reference, work / "assets")
        from app.input_contracts import profile_for_data
        profile = profile_for_data(read_json(work / "assets" / "data_manifest.json")["data_id"])
        binding = None
        if profile["bindings"] is None and previous_pull is not None:
            from app.input_review import read_binding
            binding = read_binding(repository, previous_pull)
        try:
            context = load_context(work / "assets", input_profile=profile, input_mapping=binding)
        except InputMappingRequired as pending:
            context = {"_initial_selection": initial_selection, "_review_work": work}
            outcome = publish_payload(
                ROOT, request, None, provenance, default_branch, context=context,
                catalogs=catalogs, policy=policy, input_request=pending.schema_request)
            return {**outcome, "workflow_run_url": provenance["workflow_run_url"],
                    "ai_dispatch": dispatch_ai(outcome, repository, default_branch)}
        schema_request = context.get("_input_schema_request")
        if not isinstance(schema_request, dict):
            raise ValueError("AI 조율 전에 검증된 입력 스키마가 필요합니다.")
        context["_initial_selection"] = initial_selection
        context["_review_work"] = work
        outcome = publish_payload(
            ROOT, request, None, provenance, default_branch, context=context,
            catalogs=catalogs, policy=policy, input_request=schema_request, input_phase="coordination")
        return {**outcome, "workflow_run_url": provenance["workflow_run_url"],
                "ai_dispatch": dispatch_ai(outcome, repository, default_branch)}
    finally:
        shutil.rmtree(work)



INPUT_FILES = (
    "request.json", "eda.json", "report.md", "evaluation-plan.json",
    "eda-plan.json", "recommendation-request.json", "selection.json",
    "ai-recommendations.json",
    "input-schema.json", "input-mapping.json",
    "feedback-requests.json", "feedback-applied.json", "coordination-plan.json", "execution-plan.json",
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
    if session.get("has_execution_plan"):
        names = (*names, "execution-plan.json")
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
    from app.planning_eda import validate_feedback_state
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
        from app.input_contracts import profile_for_data
        manifest = read_json(work / "assets" / "data_manifest.json")
        profile = profile_for_data(manifest["data_id"])
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
        coordination_schema = {**schema_request,
                               "feature_columns": manifest["feature_columns"],
                               "model_ref": read_json(ROOT / "config" / "assets.json").get("model_image"),
                               "current_bindings": context["input_contract"]["bindings"]
                               if context is not None and context.get("input_contract") else None}
        feedback, applied_ids = _pending_feedback(flow, repository, number, destination)
        from app.planning_eda import build_context
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
    return {"status": "input_mapping_validated", "next_tool": "plan-eda",
            "model_execution_performed": False}

def plan_eda(pull_request, work):
    flow, repository = _runtime()
    number, work = flow.issue_number(pull_request), Path(work)
    session = read_json(work / "session.json")
    if (session.get("repository") != repository or session.get("pull_request") != number
            or session.get("phase") not in ("coordination", "eda_planned")):
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
    from app.planning_eda import apply_proposal
    proposal = _read_json(work / "coordination-proposal.json")
    if session["phase"] == "eda_planned":
        prepared = read_json(work / "EDA_PLAN.json")
        if (canonical_hash(proposal) != session["planner_proposal_hash"]
                or canonical_hash(prepared) != session["prepared_plan_hash"]):
            raise ValueError("이미 준비한 계획과 다른 조율안은 같은 세션에 적용할 수 없습니다.")
        return {"status": "eda_plan_validated", "next_tool": "execute-eda",
                "plan_hash": session["execution_plan_hash"],
                "selected_eda_ids": [call["tool_id"] for call in prepared["execution_plan"]["calls"]],
                "eda_execution_performed": False}
    result = apply_proposal(
        selection, proposal,
        read_json(work / "RESEARCH_CONTEXT.json"),
        catalogs=session["catalogs"], policy=session["policy"])
    updated, record = result["selection"], result["record"]
    mapping_path = folder / "input-mapping.json"
    context = load_context(
        work / "assets", input_profile=session["input_profile"],
        input_mapping=read_json(mapping_path) if mapping_path.is_file() else None)
    execution = planning_eda.build_execution_plan(
        context, result["eda_ids"], catalog=session["catalogs"]["eda"], policy=session["policy"],
        data_image=session.get("data_image"), model_image=session["coordination_schema"].get("model_ref"),
        code_commit=session["code_commit"])
    prepared = {"selection": updated, "record": record, "execution_plan": execution}
    write_json(work / "EDA_PLAN.json", prepared)
    session.update(phase="eda_planned", prepared_plan_hash=canonical_hash(prepared),
                   execution_plan_hash=execution["plan_hash"], has_execution_plan=True,
                   planner_proposal_hash=canonical_hash(proposal))
    session["allowed_outputs"] = _outputs(session)
    write_json(work / "session.json", session)
    return {"status": "eda_plan_validated", "next_tool": "execute-eda",
            "plan_hash": execution["plan_hash"], "selected_eda_ids": result["eda_ids"],
            "eda_execution_performed": False}


def execute_eda(pull_request, work, expected_plan_hash=None):
    flow, repository = _runtime()
    number, work = flow.issue_number(pull_request), Path(work)
    session = read_json(work / "session.json")
    if (session.get("repository") != repository or session.get("pull_request") != number
            or session.get("phase") not in ("eda_planned", "recommendation")):
        raise ValueError("검증된 EDA 계획이 준비된 세션만 실행할 수 있습니다.")
    if expected_plan_hash is not None and expected_plan_hash != session["execution_plan_hash"]:
        raise ValueError("실행 요청의 EDA 계획 hash가 현재 계획과 다릅니다.")
    pull, _, _, _ = _pull(flow, repository, number)
    if pull["head"]["sha"] != session["head_sha"]:
        raise ValueError("EDA 실행 전에 PR이 변경되었습니다.")
    folder = flow.report_directory(ROOT, session["issue_number"])
    if canonical_hash(read_json(folder / "selection.json")) != session["selection_hash"]:
        raise ValueError("EDA 실행 전에 사람 선택이 변경되었습니다.")
    _verify_research_inputs(flow, repository, number, folder, work, session)
    prepared = read_json(work / "EDA_PLAN.json")
    if (canonical_hash(prepared) != session["prepared_plan_hash"]
            or prepared["execution_plan"]["plan_hash"] != session["execution_plan_hash"]):
        raise ValueError("검증 후 EDA 계획이나 선택이 변경되었습니다.")
    if session["phase"] == "recommendation":
        request = read_json(work / "AI_REQUEST.json")
        if (canonical_hash(request["eda_results"]) != session["baseline_hash"]
                or request["eda_results"]["execution_plan_hash"] != session["execution_plan_hash"]):
            raise ValueError("완료된 EDA 결과가 변경되었습니다.")
        return {"status": "data_checks_completed", "next_tool": "read-recommendation-context",
                "context_hash": session["context_hash"], "selected_eda_ids": session["initial_eda_ids"],
                "feedback_applied": len(session["coordination"]["record"]["feedback_ids"]),
                "model_execution_performed": False}
    updated, record, execution = (prepared[key] for key in ("selection", "record", "execution_plan"))
    mapping_path = folder / "input-mapping.json"
    context = load_context(
        work / "assets", input_profile=session["input_profile"],
        input_mapping=read_json(mapping_path) if mapping_path.is_file() else None)
    baseline = executor_eda.execute_plan(
        execution, context, catalog=session["catalogs"]["eda"], policy=session["policy"])
    identifiers = [call["tool_id"] for call in execution["calls"]]
    flow.validate_aggregate(baseline, context["data_manifest"]["prepared_source"], identifiers)
    wrapper = {"record": record, "code_commit": session["code_commit"],
               "schema": session["coordination_schema"],
               "eda_evidence": {item["block"]: {"status": item["status"], "details": item["details"]}
                                for item in baseline["results"]},
               "execution_plan": execution}
    wrapper["coordination_hash"] = canonical_hash(wrapper)
    context["_pr1_coordination"] = wrapper
    baseline.update(coordination_hash=wrapper["coordination_hash"],
                    research_request_hash=record["request_hash"],
                    coordination_evidence=wrapper["eda_evidence"])
    write_json(folder / "selection.json", updated)
    write_json(folder / "coordination-plan.json", wrapper)
    write_json(folder / "execution-plan.json", execution)
    feedback_ids = record["feedback_ids"]
    write_json(folder / "feedback-applied.json", {
        "schema_version": 1, "repository": repository, "pull_request": number,
        "comment_ids": sorted(set(session["applied_feedback_ids"]) | set(feedback_ids)),
        "coordination_hash": wrapper["coordination_hash"],
    })
    session.update(
        phase="recommendation", selection_hash=canonical_hash(updated),
        selection_changed=canonical_hash(updated) != session["initial_selection_hash"],
        initial_eda_ids=identifiers, coordination=wrapper,
        baseline_hash=canonical_hash(baseline))
    request = _prepare_block_request(flow, folder, work, session, context, updated, baseline)
    session["context_hash"] = request["context_hash"]
    session["allowed_outputs"] = _outputs(session)
    write_json(work / "session.json", session)
    return {"status": "data_checks_completed", "next_tool": "read-recommendation-context",
            "context_hash": request["context_hash"], "selected_eda_ids": identifiers,
            "feedback_applied": len(feedback_ids), "model_execution_performed": False}


def run_data_checks(pull_request, work):
    """Compatibility entrypoint; the workflow exposes planning and execution separately."""
    planned = plan_eda(pull_request, work)
    return execute_eda(pull_request, work, planned["plan_hash"])

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
        baseline = read_json(work / "AI_REQUEST.json")["eda_results"]
        if (canonical_hash(baseline) != session["baseline_hash"]
                or baseline["data_id"] != context["data_manifest"]["data_id"]):
            raise ValueError("검증한 EDA 집계가 변경되었습니다. 다시 시작하세요.")
        flow.validate_aggregate(
            baseline, context["data_manifest"]["prepared_source"], session["initial_eda_ids"])
    else:
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
    if session.get("has_execution_plan"):
        execution = planning_eda.build_execution_plan(
            context, plans["eda"]["selected_ids"], catalog=session["catalogs"]["eda"],
            policy=session["policy"], data_image=session["data_image"],
            model_image=session["coordination_schema"].get("model_ref"),
            code_commit=session["code_commit"])
        eda = executor_eda.execute_plan(
            execution, context, catalog=session["catalogs"]["eda"], policy=session["policy"],
            reuse=baseline, reuse_plan=session["coordination"]["execution_plan"])
        context["_execution_plan"] = execution
    else:
        eda = flow.selected_report(baseline, plans["eda"]["selected_ids"], context)
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


def main():
    pin_review_code()
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="mode", required=True)
    commands.add_parser("workflow")
    commands.add_parser("resolve-ai-target")
    for name in ("prepare-ai", "apply-ai", "apply-input-mapping", "run-data-checks", "plan-eda", "execute-eda"):
        cloud = commands.add_parser(name)
        cloud.add_argument("--pull-request", required=True, type=issue_number)
        cloud.add_argument("--work", required=True, type=Path)
        if name == "execute-eda":
            cloud.add_argument("--plan-hash", required=True)
    dry = commands.add_parser("dry-run")
    dry.add_argument("--issue-json", type=Path, required=True)
    dry.add_argument("--aggregate-json", type=Path, required=True)
    dry.add_argument("--repository", required=True)
    dry.add_argument("--output", type=Path, required=True)
    dry.add_argument("--code-commit", required=True)
    dry.add_argument("--run-url", required=True)
    args = parser.parse_args()
    try:
        if args.mode == "resolve-ai-target":
            if os.environ.get("GITHUB_ACTIONS") != "true":
                raise ValueError("Remote mode is restricted to the GitHub Actions runner.")
            from app.recommendation_events import resolve_target, write_outputs
            outcome = resolve_target(
                read_json(Path(os.environ["GITHUB_EVENT_PATH"])),
                os.environ["GITHUB_EVENT_NAME"], os.environ["GITHUB_REPOSITORY"],
                os.environ.get("REQUESTED_PR"),
            )
            write_outputs(outcome)
        elif args.mode in ("prepare-ai", "apply-ai", "apply-input-mapping", "run-data-checks", "plan-eda", "execute-eda"):
            if os.environ.get("GITHUB_ACTIONS") != "true":
                raise ValueError("Remote mode is restricted to the GitHub Actions runner.")
            if args.mode == "prepare-ai":
                handler = prepare_ai
            elif args.mode == "apply-ai":
                handler = apply_ai
            elif args.mode == "apply-input-mapping":
                handler = apply_input_mapping
            elif args.mode == "plan-eda":
                handler = plan_eda
            elif args.mode == "execute-eda":
                handler = execute_eda
            else:
                handler = run_data_checks
            outcome = (handler(args.pull_request, args.work, args.plan_hash)
                       if args.mode == "execute-eda" else handler(args.pull_request, args.work))
        elif args.mode == "workflow":
            if os.environ.get("GITHUB_ACTIONS") != "true":
                raise ValueError("Remote mode is restricted to the GitHub Actions runner.")
            event = read_json(Path(os.environ["GITHUB_EVENT_PATH"]))
            default_branch = event["repository"]["default_branch"]
            outcome = run_workflow(
                event, os.environ["GITHUB_EVENT_NAME"], os.environ["GITHUB_REPOSITORY"],
                os.environ["GITHUB_ACTOR"], default_branch,
                Path(os.environ["RESEARCH_WORK_DIRECTORY"]), os.environ.get("RESEARCH_ISSUE_NUMBER"),
            )
            from app.recommendation_events import write_target
            write_target(outcome, os.environ["GITHUB_REPOSITORY"])
            summary = os.environ.get("GITHUB_STEP_SUMMARY")
            if summary:
                with Path(summary).open("a", encoding="utf-8") as handle:
                    handle.write("## 연구검토 준비\n\n```json\n" + json.dumps(outcome, ensure_ascii=False, indent=2) + "\n```\n")
        else:
            repository = repository_name(args.repository)
            if (not re.fullmatch(r"[0-9a-f]{40}", args.code_commit)
                    or not re.fullmatch(re.escape(f"https://github.com/{repository}/actions/runs/") + r"[1-9][0-9]*", args.run_url)
                    or args.output.resolve().is_relative_to(ROOT.resolve())):
                raise ValueError("Dry-run provenance/output is invalid.")
            request = parse_issue(read_json(args.issue_json), repository)
            if request is None:
                raise ValueError("Dry-run input is not the registered research form.")
            eda = read_json(args.aggregate_json)
            validate_aggregate(eda, read_json(CONFIG))
            provenance = {"data_image": data_reference(read_json(ROOT / "config" / "assets.json")),
                          "code_commit": args.code_commit, "workflow_run_url": args.run_url,
                          "data_access": "Local dry-run reuses previously verified real-data EDA; no remote execution.",
                          "dry_run": True}
            paths = write_payload(args.output, request, eda, provenance)
            outcome = {"status": "dry_run", "paths": paths, "assessment_status": eda["assessment_status"],
                       "remote_execution_performed": False}
            write_json(args.output / "dry-run-validation.json", outcome)
        print(json.dumps(outcome, ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        reason = str(error) if type(error) in (ValueError, RuntimeError) else "Input/filesystem/API validation failed."
        parser.error("Research review preparation failed: " + reason
                     + " No Issue text, credentials or individual records are logged.")


if __name__ == "__main__":
    # Helper imports must reuse this trusted entrypoint after report checkout.
    sys.modules["app.research"] = sys.modules[__name__]
    main()
