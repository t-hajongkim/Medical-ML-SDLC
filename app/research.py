"""Research-form intake, aggregate review payload and guarded GitHub publication."""

import argparse
import copy
import html
import hashlib
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
AUTO_START = "**자동 생성 연구 보고서 · 시작**"
AUTO_END = "**자동 생성 연구 보고서 · 끝**"
LEGACY_AUTO_START = "<!-- research-review:auto:start -->"
LEGACY_AUTO_END = "<!-- research-review:auto:end -->"
TRUSTED_ASSOCIATIONS = {"OWNER", "COLLABORATOR"}




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
        docker("create", "--name", container, "--network", "none", reference, "/bin/true")
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
        if ("expected_test_race_missing" in source
                and demographics["details"]["original_race_missing"].get("test") != source["expected_test_race_missing"]):
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
        "추가·제외·주지표·임계값과 매개변수 변경을 자유롭게 요청하세요. AI가 요청을 해석하고 Python이 검증합니다.",
        "카탈로그에 등록된 ID만 처리하며 필수 schema/patient_overlap은 제외할 수 없습니다.",
        "자연어는 실행 코드가 아닌 수정 요청입니다. 처리된 댓글 편집은 재적용하지 않으므로 새 댓글로 요청하세요.",
        "준비 이후 본문·권한·연구 요청이 바뀌면 기존 조율안을 게시하지 않습니다.",
        "AI 추천 provided, 주지표 선택, blocked 평가 항목 없음과 반영할 요청을 확인한 뒤 "
        "Ready for review → Merge합니다.",
        "Merge된 계획과 commit이 승인 기준입니다. 별도 승인 JSON을 생성하지 않습니다.",
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
    chosen = selected_report(eda, plans["eda"]["selected_ids"], context)
    validate_aggregate(chosen, manifest["prepared_source"], plans["eda"]["selected_ids"])
    return selection, plans, chosen, metadata_mock


def write_payload(root, request, eda, provenance, *, context=None, catalogs=None, policy=None,
                  prepared=None):
    directory = report_directory(root, request["issue_number"])
    directory.mkdir(parents=True, exist_ok=True)
    for name in (*OUTPUT_NAMES, "ai-recommendations.json", "input-schema.json", "input-mapping.json",
                 "execution-plan.json"):
        if (directory / name).is_symlink():
            raise ValueError("Generated report files must not be symlinks.")
    catalogs = catalogs if catalogs is not None else {
        stage: planning.load_catalog(stage) for stage in ("eda", "evaluation")}
    policy = policy if policy is not None else planning.load_policy()
    if prepared is None:
        selection, plans, enriched, metadata_mock = payload_plans(directory, eda, context, catalogs, policy)
    else:
        selection, plans, enriched, metadata_mock = prepared
        if canonical_hash(enriched) != canonical_hash(eda):
            raise ValueError("Prepared publication does not match the final EDA.")
        if canonical_hash(read_json(directory / "selection.json")) != canonical_hash(selection):
            raise ValueError("Prepared publication does not match the current selection.")
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
    start_marker, end_marker = AUTO_START, AUTO_END
    if LEGACY_AUTO_START in current or LEGACY_AUTO_END in current:
        if AUTO_START in current or AUTO_END in current:
            raise ValueError("Review PR mixes incompatible automatic-area markers.")
        start_marker, end_marker = LEGACY_AUTO_START, LEGACY_AUTO_END
    if current.count(start_marker) != current.count(end_marker) or current.count(start_marker) > 1:
        raise ValueError("Review PR has duplicate or broken automatic-area markers.")
    block = AUTO_START + "\n" + generated.rstrip() + "\n" + AUTO_END
    if start_marker in current:
        start, end = current.index(start_marker), current.index(end_marker)
        if end < start:
            raise ValueError("Review PR has duplicate or broken automatic-area markers.")
        return current[:start] + block + current[end + len(end_marker):]
    if not current.strip() or (legacy is not None and current.strip() == legacy.strip()):
        return block
    return block + "\n\n" + current


def merge_review_body(existing_body, generated_report):
    return merge_auto_body(existing_body, generated_report)




def dispatch_ai(outcome, repository, default_branch):
    if outcome.get("status") not in ("prepared", "created", "updated", "unchanged"):
        return {"status": "not_applicable"}
    if outcome.get("ai_status") not in ("pending", "stale", "pending_input_mapping", "pending_coordination"):
        return {"status": "not_needed", "ai_status": outcome.get("ai_status")}
    if outcome.get("issue_number") is not None and not outcome.get("pull_url"):
        kind, number = "issue", issue_number(outcome["issue_number"])
    else:
        prefix = f"https://github.com/{repository}/pull/"
        url = outcome.get("pull_url", "")
        if not url.startswith(prefix):
            raise ValueError("AI feedback requires the current repository's review PR.")
        kind, number = "pull_request", issue_number(url[len(prefix):])
    return {"status": "requested", "workflow": "research-plan.lock.yml",
            "target_kind": kind, "target_number": number,
            kind: number, "trigger": "workflow_run", "default_branch": default_branch}


def run_workflow(event, event_name, repository, actor, default_branch, work, manual_number=None):
    request, decision = intake(event, event_name, repository, actor, manual_number)
    if decision:
        return decision
    branch = f"research/review-issue-{request['issue_number']}"
    pull, deferred = existing_pull(repository, branch, default_branch)
    if deferred:
        return deferred
    data_reference(read_json(ROOT / "config" / "assets.json"))
    if pull is None:
        outcome = {"status": "prepared", "issue_number": request["issue_number"],
                   "ai_status": "pending", "pull_request_created": False,
                   "eda_execution_performed": False}
    else:
        outcome = {"status": "updated", "ai_status": "stale",
                   "pull_url": f"https://github.com/{repository}/pull/{pull['number']}"}
    return {**outcome, "ai_dispatch": dispatch_ai(outcome, repository, default_branch)}



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
    names = [*OUTPUT_FILES, *MAPPING_OUTPUTS]
    if session.get("has_coordination"):
        names.append("coordination-plan.json")
    if session.get("pull_request") is not None and session.get("has_coordination"):
        names.append("feedback-applied.json")
    if session.get("selection_changed") or session.get("target_kind") == "issue":
        names.append("selection.json")
    if session.get("target_kind") == "issue":
        names.append("request.json")
    if session.get("has_execution_plan"):
        names.append("execution-plan.json")
    return [session["report_dir"] + "/" + name for name in dict.fromkeys(names)]

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
    pull = _session_target(flow, repository, number, session)
    if canonical_hash(read_json(folder / "request.json")) != session["request_hash"]:
        raise ValueError("연구 요청이 준비 이후 변경되었습니다.")
    pending, _ = ([], []) if session["pull_request"] is None else _pending_feedback(
        flow, repository, number, folder, applied_ids=session["applied_feedback_ids"])
    if pending != read_json(work / "RESEARCH_CONTEXT.json")["feedback"]:
        raise ValueError("준비 이후 피드백이 변경되었습니다.")
    return pull

def _session_target(flow, repository, number, session):
    expected = session.get("target_number", session.get("pull_request"))
    if session.get("repository") != repository or expected != number:
        raise ValueError("연구 세션과 요청 대상이 다릅니다.")
    if session.get("target_kind", "pull_request") == "issue":
        original = flow.gh_json(f"repos/{repository}/issues/{session['issue_number']}")
        if original.get("state") != "open":
            raise ValueError("닫힌 Issue에는 새 연구 PR을 만들지 않습니다.")
        request = flow.parse_issue(original, repository)
        if request is None or canonical_hash(request) != session["request_hash"]:
            raise ValueError("연구 요청이 변경되었습니다. 새 실행으로 다시 시작하세요.")
        return None
    pull, branch, issue, default = _pull(flow, repository, number)
    if (pull["head"]["sha"] != session["head_sha"] or branch != session["head_branch"]
            or issue != session["issue_number"] or default != session["default_branch"]):
        raise ValueError("연구 PR이 변경되었습니다. 최신 상태로 다시 시작하세요.")
    return pull


def _cache_input_context(work, session, context):
    from app.input_contracts import read_manifest
    value = {key: item for key, item in context.items()
             if key != "feature_rows" and not key.startswith("_pr1_")}
    write_json(work / "VALIDATED_CONTEXT.json", value)
    session["validated_context_hash"] = canonical_hash(value)
    session["validated_manifest_hash"] = canonical_hash(read_manifest(work / "assets"))
    _cache_planning_context(work, session, context)


def _cache_planning_context(work, session, context):
    value = {key: context[key] for key in (
        "data_manifest", "feature_columns", "expected_feature_columns", "target_mapping",
        "input_contract", "dataset_context", "_input_schema_request") if key in context}
    write_json(work / "PLANNING_CONTEXT.json", value)
    session["planning_context_hash"] = canonical_hash(value)
    session["signature_context"] = {
        key: value[key] for key in ("data_manifest", "feature_columns",
                                   "expected_feature_columns", "target_mapping", "input_contract")
        if key in value}


def _planning_context_for_session(work, session):
    value = read_json(work / "PLANNING_CONTEXT.json")
    if canonical_hash(value) != session["planning_context_hash"]:
        raise ValueError("검증된 계획 문맥이 변경되었습니다.")
    return value


def _execution_context_for_session(work, session, folder):
    if not (work / "assets").exists():
        image_assets(session["data_image"], work / "assets")
        binding = read_json(folder / "input-mapping.json")
        context = load_context(work / "assets", input_profile=session["input_profile"],
                               input_mapping=binding)
        if planning_eda.input_signature(context) != planning_eda.input_signature(session["signature_context"]):
            raise ValueError("추가 계산의 입력이 인증된 기존 입력과 다릅니다.")
        _cache_input_context(work, session, context)
        return context
    return _context_for_session(work, session)


def _context_for_session(work, session):
    from app.asset_contract import verify_files
    from app.input_contracts import read_manifest, _feature_rows
    manifest = read_manifest(work / "assets")
    if canonical_hash(manifest) != session["validated_manifest_hash"]:
        raise ValueError("검증한 입력 manifest가 변경되었습니다.")
    verify_files(work / "assets", manifest)
    value = read_json(work / "VALIDATED_CONTEXT.json")
    if canonical_hash(value) != session["validated_context_hash"]:
        raise ValueError("runner 내부의 검증된 입력 문맥이 변경되었습니다.")
    value["feature_rows"] = lambda: _feature_rows(work / "assets", manifest)
    return value


def _authenticate_prior_eda(flow, folder, session, prior, prior_plan):
    """Anchor reusable PR blobs to the exact successful native publication."""
    import io
    import zipfile

    repository = repository_name(session["repository"])
    issue = issue_number(session["issue_number"])
    if (type(session.get("schema_version")) is not int or session["schema_version"] != 2
            or session.get("target_kind") != "pull_request"
            or type(session.get("pull_request")) is not int
            or session["pull_request"] < 1
            or type(session.get("target_number")) is not int
            or session["target_number"] != session["pull_request"]
            or session["report_dir"] != f"research/reports/issue-{issue}"
            or session["head_branch"] != f"research/review-issue-{issue}"):
        raise ValueError("Prior EDA reuse requires the exact existing research PR.")
    provenance = prior.get("provenance", {})
    if not isinstance(provenance, dict):
        raise ValueError("Prior EDA publication provenance is malformed.")
    url = provenance.get("workflow_run_url")
    if url is None:
        session["eda_cache_status"] = "cache_miss_missing_publication_proof"
        return False
    match = re.fullmatch(
        re.escape(f"https://github.com/{repository}/actions/runs/") + r"([1-9][0-9]{0,19})",
        url if isinstance(url, str) else "")
    if not match:
        raise ValueError("Prior EDA publication run URL is not from this repository.")
    run = run_identifier(match.group(1))
    source = flow.gh_json(f"repos/{repository}/actions/runs/{run}")
    if (not isinstance(source, dict) or str(source.get("id")) != run
            or source.get("path") != ".github/workflows/research-plan.lock.yml"
            or source.get("event") not in ("workflow_run", "workflow_dispatch")
            or source.get("status") != "completed" or source.get("conclusion") != "success"
            or source.get("head_branch") != session["default_branch"]
            or source.get("head_sha") != prior_plan["code_commit"]
            or source.get("head_sha") != session["code_commit"]
            or any(not isinstance(source.get(key), dict)
                   or source[key].get("full_name") != repository
                   or type(source[key].get("id")) is not int or source[key]["id"] < 1
                   for key in ("repository", "head_repository"))
            or source["repository"]["id"] != source["head_repository"]["id"]):
        raise ValueError("Prior EDA is not backed by a successful trusted default-branch native workflow.")
    attempt = run_identifier(str(source.get("run_attempt", "")))
    actors = [source.get("actor")]
    if "triggering_actor" in source:
        actors.append(source["triggering_actor"])
    checked = set()
    for actor in actors:
        login = actor.get("login") if isinstance(actor, dict) else None
        if (not isinstance(actor, dict) or actor.get("type") != "User"
                or not isinstance(login, str) or not re.fullmatch(r"[A-Za-z0-9-]+", login)):
            raise ValueError("Prior EDA publication requires an authorized human source actor.")
        if login not in checked:
            permission = flow.gh_json(f"repos/{repository}/collaborators/{login}/permission")
            if permission.get("permission") not in ("write", "maintain", "admin"):
                raise ValueError("Prior EDA publication actor no longer has write permission.")
            checked.add(login)
    pages = flow.gh_json(
        f"repos/{repository}/actions/runs/{run}/artifacts?per_page=100",
        "--paginate", "--slurp")
    if (not isinstance(pages, list) or any(not isinstance(page, dict)
            or not isinstance(page.get("artifacts"), list)
            or any(not isinstance(item, dict) for item in page["artifacts"]) for page in pages)):
        raise ValueError("Prior EDA publication artifact listing is malformed.")
    name = f"research-review-body-{run}-{attempt}"
    matches = [item for page in pages for item in page["artifacts"]
               if isinstance(item, dict) and item.get("name") == name]
    if len(matches) > 1:
        raise ValueError("Prior EDA publication artifact is ambiguous.")
    if not matches or matches[0].get("expired") is True:
        session["eda_cache_status"] = ("cache_miss_expired_publication_proof" if matches
                                       else "cache_miss_missing_publication_proof")
        return False
    artifact = matches[0]
    origin = artifact.get("workflow_run")
    if (artifact.get("expired") is not False or not isinstance(origin, dict)
            or str(origin.get("id")) != run or origin.get("head_sha") != source["head_sha"]
            or origin.get("head_branch") != session["default_branch"]
            or origin.get("repository_id") != source["repository"]["id"]
            or origin.get("head_repository_id") != source["head_repository"]["id"]):
        raise ValueError("Prior EDA artifact does not match its exact workflow run.")
    identifier = issue_number(artifact.get("id"))
    try:
        downloaded = subprocess.run(
            ["gh", "api", f"repos/{repository}/actions/artifacts/{identifier}/zip"],
            capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError("Could not download prior EDA publication proof.") from None
    if downloaded.returncode or len(downloaded.stdout) > 1_000_000:
        raise RuntimeError("Prior EDA publication proof download failed or exceeded its size limit.")
    try:
        with zipfile.ZipFile(io.BytesIO(downloaded.stdout)) as archive:
            entries = archive.infolist()
            limits = {"publication.json": 65536, "pr-body.md": 524288}
            if (len(entries) != 2 or {entry.filename for entry in entries} != set(limits)
                    or any(entry.is_dir() or entry.flag_bits & 1
                           or entry.file_size > limits[entry.filename]
                           or ((entry.external_attr >> 16) & 0o170000) not in (0, 0o100000)
                           for entry in entries)):
                raise ValueError("Prior EDA proof ZIP must contain only bounded publication.json and pr-body.md.")
            content = archive.read("publication.json")
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError):
        raise ValueError("Prior EDA publication proof is not a readable bounded ZIP.") from None
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Prior EDA publication receipt has duplicate keys.")
            result[key] = value
        return result
    receipt = json.loads(content, object_pairs_hook=unique)
    required = {"schema_version", "operation", "repository", "target_kind", "target_number",
                "issue_number", "pull_request", "head_branch", "previous_head_sha",
                "publication_commit", "expected_tree_sha", "recommendations_sha256",
                "previous_body_sha256", "body_sha256", "body_refresh_required"}
    if (not isinstance(receipt, dict) or type(receipt.get("schema_version")) is not int
            or not required.issubset(receipt)
            or receipt["schema_version"] != 2 or receipt.get("repository") != repository
            or type(receipt.get("issue_number")) is not int or receipt["issue_number"] != issue
            or receipt.get("head_branch") != session["head_branch"]
            or type(receipt.get("target_number")) is not int
            or not ((receipt.get("operation") == "create" and receipt.get("target_kind") == "issue"
                     and receipt["target_number"] == issue and receipt.get("pull_request") is None)
                    or (receipt.get("operation") == "update" and receipt.get("target_kind") == "pull_request"
                        and type(receipt.get("pull_request")) is int
                        and receipt["pull_request"] == session["pull_request"]
                        and receipt["target_number"] == session["pull_request"]))
            or type(receipt.get("body_refresh_required")) is not bool
            or (receipt["operation"] == "create" and receipt["body_refresh_required"])
            or (receipt["operation"] == "create" and receipt["previous_head_sha"] != prior_plan["code_commit"])
            or any(not isinstance(receipt.get(key), str)
                   or not re.fullmatch(r"[0-9a-f]{40}", receipt[key])
                   for key in ("previous_head_sha", "expected_tree_sha"))
            or (receipt.get("publication_commit") is None and receipt["operation"] == "create")
            or (receipt.get("publication_commit") is not None
                and (not isinstance(receipt["publication_commit"], str)
                     or not re.fullmatch(r"[0-9a-f]{40}", receipt["publication_commit"])))
            or any(not isinstance(receipt.get(key), str)
                   or not re.fullmatch(r"[0-9a-f]{64}", receipt[key])
                   for key in ("recommendations_sha256", "previous_body_sha256", "body_sha256"))):
        raise ValueError("Prior EDA publication receipt does not match this research target.")
    head = _sha(session["head_sha"], "current PR head")
    commit = flow.gh_json(f"repos/{repository}/git/commits/{head}")
    if not isinstance(commit, dict) or commit.get("sha") != head:
        raise ValueError("Current PR commit identity does not match the EDA proof request.")
    current_tree = _sha(commit.get("tree", {}).get("sha"), "current PR tree")
    trees = {}
    for sha in {receipt["expected_tree_sha"], current_tree}:
        value = flow.gh_json(f"repos/{repository}/git/trees/{sha}?recursive=1")
        if (not isinstance(value, dict) or value.get("sha") != sha
                or value.get("truncated") is not False or not isinstance(value.get("tree"), list)
                or len(value["tree"]) > 100000):
            raise ValueError("Prior EDA proof requires complete bounded immutable Git trees.")
        entries = {}
        for entry in value["tree"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or entry["path"] in entries:
                raise ValueError("Prior EDA proof Git tree has malformed or duplicate paths.")
            entries[entry["path"]] = entry
        trees[sha] = entries
    for name in ("eda.json", "execution-plan.json", "request.json", "input-mapping.json", "input-schema.json"):
        path = session["report_dir"] + "/" + name
        published = trees[receipt["expected_tree_sha"]].get(path)
        current = trees[current_tree].get(path)
        local = folder / name
        if published is None and current is None and not local.exists() and name.startswith("input-"):
            continue
        if (published is None or current is None or published.get("type") != "blob"
                or published.get("mode") != "100644" or current.get("type") != "blob"
                or current.get("mode") != "100644" or published.get("sha") != current.get("sha")
                or not isinstance(current.get("sha"), str)
                or not re.fullmatch(r"[0-9a-f]{40}", current["sha"])
                or local.is_symlink() or not local.is_file() or local.stat().st_size > 10_000_000):
            raise ValueError("Prior EDA cache blobs differ from the authenticated publication.")
        raw = local.read_bytes()
        oid = hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()
        if oid != current["sha"]:
            raise ValueError("Local prior EDA cache differs from the authenticated Git blob.")
    return True


def _begin_basic_eda(flow, folder, work, session, context, selection, *, prior_cache=None):
    ids = planning_eda._eda_ids(selection, [], session["catalogs"], session["policy"])
    prior = None
    prior_plan = None
    session.setdefault("eda_cache_status", "cache_miss_no_previous_result")
    if prior_cache:
        prior, prior_plan = prior_cache
        ids = [call["tool_id"] for call in prior_plan["calls"]]
        session["eda_cache_status"] = "authenticated_previous_pr_result"
    elif prior_cache is None and (folder / "eda.json").is_file() and (folder / "execution-plan.json").is_file():
        prior = read_json(folder / "eda.json")
        prior_plan = read_json(folder / "execution-plan.json")
        planning_eda.validate_execution_plan(prior_plan, catalog=session["catalogs"]["eda"],
                                            policy=session["policy"])
        if (prior_plan["input_signature"] != planning_eda.input_signature(context)
                or any(prior_plan[key] != expected for key, expected in (
                    ("data_image", session["data_image"]), ("model_image", session["model_image"]),
                    ("code_commit", session["code_commit"])))):
            prior = prior_plan = None
            session["eda_cache_status"] = "cache_miss_changed_input_or_code"
        elif _authenticate_prior_eda(flow, folder, session, prior, prior_plan):
            ids = [call["tool_id"] for call in prior_plan["calls"]]
            session["eda_cache_status"] = "authenticated_previous_pr_result"
        else:
            prior = prior_plan = None
    execution = planning_eda.build_execution_plan(
        context, ids, catalog=session["catalogs"]["eda"], policy=session["policy"],
        data_image=session["data_image"], model_image=session["model_image"],
        code_commit=session["code_commit"])
    basic = executor_eda.execute_plan(execution, context, catalog=session["catalogs"]["eda"],
                                     policy=session["policy"], reuse=prior, reuse_plan=prior_plan)
    flow.validate_aggregate(basic, context["data_manifest"]["prepared_source"], ids)
    write_json(work / "BASIC_EDA.json", basic)
    write_json(work / "BASIC_PLAN.json", execution)
    session.update(phase="coordination", basic_eda_hash=canonical_hash(basic),
                   basic_plan_hash=canonical_hash(execution))
    schema = {**context["_input_schema_request"], "feature_columns": context["feature_columns"],
              "model_ref": session["model_image"],
              "current_bindings": context["input_contract"]["bindings"]}
    session["coordination_schema"] = schema
    feedback, applied = ([], []) if session["pull_request"] is None else _pending_feedback(
        flow, session["repository"], session["pull_request"], folder)
    session["applied_feedback_ids"] = applied
    research_context = planning_eda.build_context(
        read_json(folder / "request.json"), schema, selection, feedback,
        catalogs=session["catalogs"], policy=session["policy"], basic_eda=basic)
    session["context_hash"] = research_context["context_hash"]
    write_json(work / "RESEARCH_CONTEXT.json", research_context)
    write_json(work / "INPUT_SCHEMA_REQUEST.json",
               {"status": "coordination", "request": context["_input_schema_request"]})
    if "metadata" in context:
        _cache_input_context(work, session, context)
    else:
        _cache_planning_context(work, session, context)


def _prior_planning_context(flow, folder, session):
    session["eda_cache_status"] = "cache_miss_no_previous_result"
    names = ("eda.json", "execution-plan.json", "input-mapping.json", "input-schema.json")
    if session["pull_request"] is None or not all((folder / name).is_file() for name in names):
        return None
    prior, plan = read_json(folder / "eda.json"), read_json(folder / "execution-plan.json")
    if any(plan.get(key) != session[key] for key in ("data_image", "model_image", "code_commit")):
        session["eda_cache_status"] = "cache_miss_changed_input_or_code"
        return None
    planning_eda.validate_execution_plan(plan, catalog=session["catalogs"]["eda"], policy=session["policy"])
    binding = read_json(folder / "input-mapping.json")
    if not isinstance(binding.get("source_manifest"), dict):
        session["eda_cache_status"] = "cache_miss_missing_planning_metadata"
        return None
    if not _authenticate_prior_eda(flow, folder, session, prior, plan):
        return None
    from app.input_contracts import profile_for_data, planning_context
    manifest = binding["source_manifest"]
    profile = profile_for_data(manifest)
    schema = read_json(folder / "input-schema.json")
    if (binding.get("profile_snapshot") != profile
            or binding.get("profile_hash") != canonical_hash(profile)
            or binding.get("schema_request") != schema):
        raise ValueError("인증된 입력 매핑과 계획 메타데이터가 다릅니다.")
    context = planning_context(manifest, profile, binding, schema)
    if planning_eda.input_signature(context) != plan["input_signature"]:
        raise ValueError("인증된 EDA 계획과 입력 매핑이 다릅니다.")
    session["input_profile"] = profile
    return context, prior, plan


def prepare_ai(pull_request, work, *, target_kind="pull_request"):
    flow, repository = _runtime()
    number, work = flow.issue_number(pull_request), Path(work)
    if target_kind not in ("issue", "pull_request"):
        raise ValueError("지원하지 않는 연구 대상입니다.")
    if work.exists() or work.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("Use a new recommendation directory outside the checkout.")
    if flow.command(["git", "status", "--porcelain"], cwd=ROOT).stdout.strip():
        raise ValueError("Research preparation requires a clean trusted checkout.")
    code = _sha(flow.command(["git", "rev-parse", "HEAD"], cwd=ROOT).stdout.strip(), "code")
    config = read_json(ROOT / "config" / "assets.json")
    default = flow.gh_json(f"repos/{repository}")["default_branch"]
    content = {}
    if target_kind == "issue":
        request, deferred = flow.intake(
            {"repository": {"full_name": repository}}, "workflow_dispatch", repository,
            os.environ.get("RESEARCH_REQUEST_ACTOR", os.environ.get("GITHUB_ACTOR")),
            manual_number=number)
        if deferred:
            raise ValueError("연구 Issue의 상태 또는 권한을 확인해야 합니다.")
        issue, head = number, code
        branch = f"research/review-issue-{issue}"
        previous, deferred = flow.existing_pull(repository, branch, default)
        if previous is not None or deferred:
            raise ValueError("이 Issue의 기존 PR에서 피드백을 요청하세요.")
        content["request.json"] = json.dumps(request, ensure_ascii=False)
        content["selection.json"] = json.dumps(planning.default_selection())
        actual_pr = None
    else:
        pull, branch, issue, default = _pull(flow, repository, number)
        head = _sha(pull["head"]["sha"], "PR head")
        flow.command(["git", "fetch", "origin", f"refs/heads/{branch}"], cwd=ROOT)
        if flow.command(["git", "rev-parse", "FETCH_HEAD"], cwd=ROOT).stdout.strip() != head:
            raise ValueError("The PR changed while preparing feedback.")
        for name in INPUT_FILES:
            value = _read_branch_file(flow, head, f"research/reports/issue-{issue}/{name}",
                                      optional=name not in ("request.json", "selection.json"))
            if value is not None:
                content[name] = value
        request = json.loads(content["request.json"])
        actual_pr = number
    if request.get("repository") != repository or request.get("issue_number") != issue:
        raise ValueError("연구 요청의 소유 정보가 다릅니다.")
    work.mkdir(parents=True)
    try:
        folder = flow.report_directory(ROOT, issue)
        folder.mkdir(parents=True, exist_ok=True)
        for name in INPUT_FILES:
            path = folder / name
            if path.is_symlink():
                raise ValueError("Research files must not be symlinks.")
            if name in content:
                path.write_text(content[name], encoding="utf-8")
            elif path.exists():
                path.unlink()
        selection = read_json(folder / "selection.json")
        session = {
            "schema_version": 2, "repository": repository, "target_kind": target_kind,
            "target_number": number, "pull_request": actual_pr, "issue_number": issue,
            "head_sha": head, "head_branch": branch, "default_branch": default,
            "report_dir": f"research/reports/issue-{issue}", "code_commit": code,
            "data_image": flow.data_reference(config),
            "model_image": validate_image_reference(config.get("model_image")),
            "catalogs": {stage: planning.load_catalog(stage) for stage in ("eda", "evaluation")},
            "policy": planning.load_policy(), "has_coordination": True,
            "selection_hash": canonical_hash(selection), "initial_selection_hash": canonical_hash(selection),
            "selection_changed": False, "request_hash": canonical_hash(request),
            "applied_feedback_ids": [],
            "inherited_ai_eda_ids": [
                item["id"] for item in json.loads(content.get("ai-recommendations.json", "{}")).get("eda", [])
            ],
        }
        cached = _prior_planning_context(flow, folder, session)
        if cached is not None:
            context, prior, plan = cached
            session["has_input_mapping"] = True
            _begin_basic_eda(flow, folder, work, session, context, selection,
                             prior_cache=(prior, plan))
        else:
            flow.image_assets(flow.data_reference(config), work / "assets")
            from app.input_contracts import profile_for_data, read_manifest
            profile = profile_for_data(read_manifest(work / "assets"))
            session["input_profile"] = profile
            binding = read_json(folder / "input-mapping.json") if "input-mapping.json" in content else None
            try:
                context = load_context(work / "assets", input_profile=profile, input_mapping=binding)
            except InputMappingRequired as pending:
                session.update(phase="input_mapping", has_input_mapping=False)
                write_json(work / "INPUT_SCHEMA_REQUEST.json",
                           {"status": "input_mapping", "request": pending.schema_request})
                write_json(work / "RESEARCH_CONTEXT.json", {
                    "phase": "input_mapping", "request": planning_eda._request(request),
                    "schema_request": pending.schema_request})
            else:
                session["has_input_mapping"] = context.get("input_contract") is not None
                _begin_basic_eda(flow, folder, work, session, context, selection, prior_cache=())
        session["allowed_outputs"] = _outputs(session)
        write_json(work / "session.json", session)
        return {"status": "ready_for_input_mapping" if session["phase"] == "input_mapping"
                else "basic_eda_completed", "phase": session["phase"],
                "target_kind": target_kind, "target_number": number, "pull_request": actual_pr,
                "context_hash": session.get("context_hash"), "basic_eda_completed": session["phase"] != "input_mapping",
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
    transient["fixed_eda_ids"] = [item["block"] for item in baseline["results"]]
    transient["fixed_eda_recommendation_ids"] = session["fixed_eda_recommendation_ids"]
    write_json(work / "AI_REQUEST.json", transient)
    if context.get("input_contract") is not None:
        write_json(folder / "input-mapping.json", context["input_contract"])
        write_json(folder / "input-schema.json", context["_input_schema_request"])
    return request

def apply_input_mapping(pull_request, work):
    flow, repository = _runtime()
    number, work = flow.issue_number(pull_request), Path(work)
    session = read_json(work / "session.json")
    _session_target(flow, repository, number, session)
    if session["phase"] != "input_mapping":
        raise ValueError("입력 매핑 단계에서만 처리합니다.")
    folder = flow.report_directory(ROOT, session["issue_number"])
    from app.input_contracts import _read_json, apply_mapping
    bundle = apply_mapping(work / "assets", _read_json(work / "input-mapping-proposal.json"),
                           profile=session["input_profile"])
    context = bundle["context"]
    context["_input_schema_request"] = bundle["schema_request"]
    write_json(folder / "input-mapping.json", bundle["binding"])
    write_json(folder / "input-schema.json", bundle["schema_request"])
    session["has_input_mapping"] = True
    _begin_basic_eda(flow, folder, work, session, context, read_json(folder / "selection.json"))
    session["allowed_outputs"] = _outputs(session)
    write_json(work / "session.json", session)
    return {"status": "input_mapping_validated", "basic_eda_completed": True,
            "next_tool": "read-research-context", "model_execution_performed": False}

def plan_eda(pull_request, work):
    flow, repository = _runtime()
    number, work = flow.issue_number(pull_request), Path(work)
    session = read_json(work / "session.json")
    if session.get("phase") not in ("coordination", "eda_planned"):
        raise ValueError("기본 EDA 이후에만 추가 점검을 계획합니다.")
    folder = flow.report_directory(ROOT, session["issue_number"])
    _verify_research_inputs(flow, repository, number, folder, work, session)
    selection = read_json(folder / "selection.json")
    if canonical_hash(selection) != session["selection_hash"]:
        raise ValueError("준비 이후 사람의 선택이 변경되었습니다.")
    from app.input_contracts import _read_json
    proposal = _read_json(work / "coordination-proposal.json")
    if session["phase"] == "eda_planned":
        prepared = read_json(work / "EDA_PLAN.json")
        if (canonical_hash(proposal) != session["planner_proposal_hash"]
                or canonical_hash(prepared) != session["prepared_plan_hash"]):
            raise ValueError("이미 준비한 계획과 다른 요청입니다.")
        return {"status": "eda_plan_validated", "plan_hash": prepared["execution_plan"]["plan_hash"],
                "selected_eda_ids": [call["tool_id"] for call in prepared["execution_plan"]["calls"]],
                "next_tool": "execute-eda", "eda_execution_performed": False}
    result = planning_eda.apply_proposal(
        selection, proposal, read_json(work / "RESEARCH_CONTEXT.json"),
        catalogs=session["catalogs"], policy=session["policy"])
    previous = read_json(work / "BASIC_PLAN.json")
    exclusions = set(result["selection"]["eda"]["exclude"])
    identifiers = list(dict.fromkeys([
        *result["eda_ids"],
        *(call["tool_id"] for call in previous["calls"] if call["tool_id"] not in exclusions),
    ]))
    execution = planning_eda.build_execution_plan(
        session["signature_context"], identifiers, catalog=session["catalogs"]["eda"],
        policy=session["policy"], data_image=session["data_image"],
        model_image=session["model_image"], code_commit=session["code_commit"])
    prepared = {"selection": result["selection"], "record": result["record"], "execution_plan": execution}
    write_json(work / "EDA_PLAN.json", prepared)
    session.update(phase="eda_planned", prepared_plan_hash=canonical_hash(prepared),
                   planner_proposal_hash=canonical_hash(proposal),
                   execution_plan_hash=execution["plan_hash"], has_execution_plan=True)
    session["allowed_outputs"] = _outputs(session)
    write_json(work / "session.json", session)
    return {"status": "eda_plan_validated", "next_tool": "execute-eda",
            "plan_hash": execution["plan_hash"], "selected_eda_ids": identifiers,
            "eda_execution_performed": False}


def execute_eda(pull_request, work, expected_plan_hash=None):
    flow, repository = _runtime()
    number, work = flow.issue_number(pull_request), Path(work)
    session = read_json(work / "session.json")
    if session.get("phase") not in ("eda_planned", "recommendation"):
        raise ValueError("추가 EDA 계획을 먼저 검증해야 합니다.")
    if expected_plan_hash != session["execution_plan_hash"]:
        raise ValueError("요청한 계획 hash가 다릅니다.")
    folder = flow.report_directory(ROOT, session["issue_number"])
    _verify_research_inputs(flow, repository, number, folder, work, session)
    if session["phase"] == "recommendation":
        cached = read_json(work / "AI_REQUEST.json")
        if canonical_hash(cached["eda_results"]) != session["baseline_hash"]:
            raise ValueError("완료된 EDA 결과가 변경되었습니다.")
        return {"status": "data_checks_completed", "next_tool": "read-recommendation-context",
                "context_hash": session["context_hash"], "selected_eda_ids": session["initial_eda_ids"],
                "executed_ids": cached["eda_results"]["executed_ids"],
                "reused_ids": cached["eda_results"]["reused_ids"],
                "model_execution_performed": False}
    prepared = read_json(work / "EDA_PLAN.json")
    if canonical_hash(prepared) != session["prepared_plan_hash"]:
        raise ValueError("계획이 검증 후 변경되었습니다.")
    basic, basic_plan = read_json(work / "BASIC_EDA.json"), read_json(work / "BASIC_PLAN.json")
    if (canonical_hash(basic) != session["basic_eda_hash"]
            or canonical_hash(basic_plan) != session["basic_plan_hash"]):
        raise ValueError("기본 EDA 결과가 변경되었습니다.")
    updated, record, execution = (prepared[key] for key in ("selection", "record", "execution_plan"))
    previous_calls = {call["tool_id"]: call for call in basic_plan["calls"]}
    needs_inputs = any(previous_calls.get(call["tool_id"]) != call for call in execution["calls"])
    context = (_execution_context_for_session(work, session, folder) if needs_inputs
               else _planning_context_for_session(work, session))
    baseline = executor_eda.execute_plan(
        execution, context, catalog=session["catalogs"]["eda"], policy=session["policy"],
        reuse=basic, reuse_plan=basic_plan)
    ids = [call["tool_id"] for call in execution["calls"]]
    flow.validate_aggregate(baseline, context["data_manifest"]["prepared_source"], ids)
    wrapper = {"record": record, "code_commit": session["code_commit"],
               "schema": session["coordination_schema"], "execution_plan": execution,
               "eda_evidence": {item["block"]: {"status": item["status"], "details": item["details"]}
                                for item in baseline["results"]}}
    wrapper["coordination_hash"] = canonical_hash(wrapper)
    context["_pr1_coordination"] = wrapper
    baseline.update(coordination_hash=wrapper["coordination_hash"],
                    research_request_hash=record["request_hash"],
                    coordination_evidence=wrapper["eda_evidence"])
    write_json(folder / "selection.json", updated)
    write_json(folder / "coordination-plan.json", wrapper)
    write_json(folder / "execution-plan.json", execution)
    if session["pull_request"] is not None:
        write_json(folder / "feedback-applied.json", {
            "schema_version": 1, "repository": repository, "pull_request": number,
            "comment_ids": sorted(set(session["applied_feedback_ids"]) | set(record["feedback_ids"])),
            "coordination_hash": wrapper["coordination_hash"]})
    session.update(phase="recommendation", selection_hash=canonical_hash(updated),
                   selection_changed=canonical_hash(updated) != session["initial_selection_hash"],
                   initial_eda_ids=ids, coordination=wrapper, baseline_hash=canonical_hash(baseline),
                   fixed_eda_recommendation_ids=list(dict.fromkeys(
                       identifier for identifier in [
                           *session.get("inherited_ai_eda_ids", []), *record["eda_add"]]
                       if identifier in ids)))
    request = _prepare_block_request(flow, folder, work, session, context, updated, baseline)
    session["context_hash"] = request["context_hash"]
    session["allowed_outputs"] = _outputs(session)
    write_json(work / "session.json", session)
    return {"status": "data_checks_completed", "next_tool": "read-recommendation-context",
            "context_hash": session["context_hash"], "selected_eda_ids": ids,
            "executed_ids": baseline["executed_ids"], "reused_ids": baseline["reused_ids"],
            "model_execution_performed": False}



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
    number, work = flow.issue_number(pull_request), Path(work)
    session = read_json(work / "session.json")
    if session.get("phase") != "recommendation":
        raise ValueError("최종 EDA 이후에만 평가 지표를 추천합니다.")
    folder = flow.report_directory(ROOT, session["issue_number"])
    pull = _verify_research_inputs(flow, repository, number, folder, work, session)
    selection = read_json(folder / "selection.json")
    if canonical_hash(selection) != session["selection_hash"]:
        raise ValueError("사람의 선택이 변경되었습니다.")
    context = _planning_context_for_session(work, session)
    context["_pr1_coordination"] = session["coordination"]
    execution = read_json(folder / "execution-plan.json")
    context["_execution_plan"] = execution
    baseline = read_json(work / "AI_REQUEST.json")["eda_results"]
    if canonical_hash(baseline) != session["baseline_hash"]:
        raise ValueError("최종 EDA 집계 결과가 변경되었습니다.")
    ai = read_json(folder / "ai-recommendations.json")
    plans = planning.build_plans(baseline, context["data_manifest"], selection, ai,
                                 catalogs=session["catalogs"], policy=session["policy"])
    if any(plans[stage]["ai_status"] != "provided" for stage in ("eda", "evaluation")):
        raise ValueError("현재 최종 EDA에 맞는 실제 AI 추천이 필요합니다.")
    if plans["eda"]["selected_ids"] != session["initial_eda_ids"]:
        raise ValueError("지표 추천 단계에서는 추가 EDA를 실행하지 않습니다. EDA 계획 단계에서 요청하세요.")
    if plans["eda"]["ai_recommended_ids"] != session["fixed_eda_recommendation_ids"]:
        raise ValueError("기본/사람 선택과 AI 추가 점검의 출처를 변경할 수 없습니다.")
    flow.validate_aggregate(baseline, context["data_manifest"]["prepared_source"], session["initial_eda_ids"])
    request = read_json(folder / "request.json")
    provenance = {
        "data_image": session["data_image"], "model_image": session["model_image"],
        "code_commit": session["code_commit"],
        "workflow_run_url": f"https://github.com/{repository}/actions/runs/"
                            + flow.run_identifier(os.environ.get("GITHUB_RUN_ID")),
        "data_access": "Validated prepared files; cached blocks explicitly reused; no database or model execution."}
    flow.write_payload(ROOT, request, baseline, provenance, context=context,
                       catalogs=session["catalogs"], policy=session["policy"],
                       prepared=(selection, plans, baseline, False))
    body = flow.merge_review_body((pull or {}).get("body") or "", (folder / "report.md").read_text(encoding="utf-8"))
    (work / "pr-body.md").write_text(body, encoding="utf-8")
    session["allowed_outputs"] = _outputs(session)
    write_json(work / "session.json", session)
    commit = _git_tree_commit(flow, ROOT, work, session)
    if pull is None and commit is None:
        raise ValueError("분석 결과 변경 없이 빈 최초 PR을 만들 수 없습니다.")
    refresh = pull is not None and (commit is not None or body != (pull.get("body") or ""))
    return {"status": "validated_copilot_recommendations" if commit else
                     ("body_refresh" if refresh else "unchanged"),
            "operation": "create" if pull is None else "update",
            "target_kind": session["target_kind"], "target_number": number,
            "pull_request": session["pull_request"],
            "issue_number": session["issue_number"], "head_branch": session["head_branch"],
            "publication_commit": commit, "publication_ready": pull is None or commit is not None or refresh,
            "create_required": pull is None, "push_required": pull is not None and commit is not None,
            "body_refresh_required": refresh,
            "allowed_outputs": session["allowed_outputs"], "remote_publication_performed": False,
            "pull_request_title": f"연구검토 #{session['issue_number']}: 데이터 점검과 평가 계획",
            "pull_request_body": body}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="mode", required=True)
    commands.add_parser("workflow")
    commands.add_parser("resolve-ai-target")
    for name in ("prepare-ai", "apply-ai", "apply-input-mapping", "plan-eda", "execute-eda"):
        cloud = commands.add_parser(name)
        cloud.add_argument("--target-number", "--pull-request", dest="target_number",
                           required=True, type=issue_number)
        cloud.add_argument("--work", required=True, type=Path)
        if name == "prepare-ai":
            cloud.add_argument("--target-kind", choices=("issue", "pull_request"), default="pull_request")
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
        elif args.mode in ("prepare-ai", "apply-ai", "apply-input-mapping", "plan-eda", "execute-eda"):
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
            if args.mode == "prepare-ai":
                outcome = handler(args.target_number, args.work, target_kind=args.target_kind)
            elif args.mode == "execute-eda":
                outcome = handler(args.target_number, args.work, args.plan_hash)
            else:
                outcome = handler(args.target_number, args.work)
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
