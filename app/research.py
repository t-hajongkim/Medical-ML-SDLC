"""Research-form intake, aggregate review payload and guarded GitHub publication."""

import argparse
import copy
import html
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid

from app.asset_contract import read_json, validate_image_reference, write_json
from app.blocks.catalog import CHECKS
from app.dataset import ROOT
from app.eda import InputMappingRequired, load_context, report
from app.prepared import CONFIG, SPLITS, docker
from app import planning


OUTPUT_NAMES = ("request.json", "eda.json", "report.md", "evaluation-plan.json",
                "eda-plan.json", "recommendation-request.json", "selection.json")
AUTO_START = "<!-- research-review:auto:start -->"
AUTO_END = "<!-- research-review:auto:end -->"
TRUSTED_ASSOCIATIONS = {"OWNER", "COLLABORATOR"}


def pin_review_code():
    """Cache trusted helpers/checks before selecting a report-only PR tree."""
    # CHECKS already holds eagerly imported functions from the trusted catalog.
    for name in ("app.input_review", "app.recommendation_events", "app.pr1_intent"):
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
                "Copilot 추천 워크플로 요청 실패. 기본 브랜치의 recommend-blocks.lock.yml과 "
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
    for name in (*OUTPUT_NAMES, "ai-recommendations.json", "input-schema.json", "input-mapping.json"):
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
    return {"status": "requested", "workflow": "recommend-blocks.lock.yml",
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
        from app.input_contracts import load_profile
        profile = load_profile()
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


def main():
    pin_review_code()
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="mode", required=True)
    commands.add_parser("workflow")
    commands.add_parser("resolve-ai-target")
    for name in ("prepare-ai", "apply-ai", "apply-input-mapping", "run-data-checks"):
        cloud = commands.add_parser(name)
        cloud.add_argument("--pull-request", required=True, type=issue_number)
        cloud.add_argument("--work", required=True, type=Path)
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
        elif args.mode in ("prepare-ai", "apply-ai", "apply-input-mapping", "run-data-checks"):
            if os.environ.get("GITHUB_ACTIONS") != "true":
                raise ValueError("Remote mode is restricted to the GitHub Actions runner.")
            from app import cloud_recommendations
            if args.mode == "prepare-ai":
                handler = cloud_recommendations.prepare_ai
            elif args.mode == "apply-ai":
                handler = cloud_recommendations.apply_ai
            elif args.mode == "apply-input-mapping":
                handler = cloud_recommendations.apply_input_mapping
            else:
                handler = cloud_recommendations.run_data_checks
            outcome = handler(args.pull_request, args.work)
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
