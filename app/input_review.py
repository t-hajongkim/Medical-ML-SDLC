"""Input role display and dataset-neutral aggregate reports."""


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
