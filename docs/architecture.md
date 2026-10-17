# GHCR 자산·조합형 EDA·평가 계획 승인 구조

## 고정 추론 → 기존 15개 지표 연결 (2026-10-17)

사용자 승인 계약: [고정 추론·평가 PRD](prd/2026-10-17-fixed-inference-evaluation.md).
아래 경로는 로컬 구현과 실제 고정 validation 추론을 확인했다.
코드 게시와 원격 실행 검증은 구분한다. 원격 성공은 해당 run·artifact·결과 PR로 확인한다.

```text
+---------------------------+      +-------------------------------+
| Target → 기본/추가 EDA     | ---> | 저장된 평가계획 PR → 사람 Merge |
+---------------------------+      +-------------------------------+
                                                |
                                                v
+-----------------------------------------------------------------+
| evaluate-model.yml: merged research PR / 수동 exact merged PR    |
| 신뢰 default 코드 checkout → 실제 merge SHA의 보고서 JSON만 읽기 |
+-----------------------------------------------------------------+
                                |
                                v
+-----------------------------------------------------------------+
| model_evaluation.py: PR·계획·EDA·입력·digest·현재 소스 계약 검증   |
| 고정 Public DATA/MODEL 파일 추출 (Docker create/cp/rm, DB 미기동) |
+-----------------------------------------------------------------+
                                |
                                v
+-----------------------------------------------------------------+
| inference.py: 기존 prepared data/model 검증 → joblib.load        |
| row_key/split/position 연결 → 113-feature LR → class 1 확률       |
| validation 기본 / test 명시, labels·확률·벡터는 비공개 메모리     |
+-----------------------------------------------------------------+
                                |
                                v
+-----------------------------------------------------------------+
| executor_eval.execute_plan(저장 계획, 비공개 context)              |
| 기존 15개 지표 중 선택 항목만 → 집계 JSON/Markdown artifact·Summary |
+-----------------------------------------------------------------+
```

- 새 상위 호출자만 모델/IO/사람 Merge 출처를 담당한다. 기존 네 Planner/Executor의 계산·계획 경계와 15개 지표 수학 라이브러리는 유지한다. 계획을 재추천하거나 threshold를 채우지 않는다.
- 모델은 기존 `config/assets.json`의 두 digest·113 입력·classes `[0,1]`·positive `1`로 제한한다. 파일/manifest 검증은 역직렬화보다 먼저 수행한다. 모델 URL/경로 자유 입력·fit·scaler·재분할·raw 다운로드는 없다.
- 단일 hosted job, contents/PR/packages read-only, Python 3.11/sklearn 1.2.2 전용 pinned 의존성. 보고서 코드 checkout/실행·분석 댓글 trigger·별도 승인 JSON·자동 PR2/Pages는 없다.
- source/계약 stale·정렬·무결성 오류는 명시적 실패다. 지표별 선행조건 부족은 기존 `not_applicable`을 보존한다. 행/식별자/벡터는 로그·artifact에 남기지 않고 비공개 중간 파일과 컨테이너는 항상 정리한다.
- README와 HTML은 박스형 흐름 그림과 GitHub 합성 CI/실제 자산 실행을 구분한다. 원격 성공 증거는 해당 run의 집계 artifact/Summary로만 확인한다.
- Merge는 교육용 계획 승인이다. validation 재사용·환자 중복·전처리 이력·조건부 모델 등록 한계를 유지하며 임상·누수 없는 독립 평가를 주장하지 않는다.

## PR2 결과 검토 (2026-10-17)

계약: [PR2 결과 검토 PRD](prd/2026-10-17-pr2-result-review.md).
현재 고정 추론·평가와 별도 PR2 게시기를 로컬에 구현했다. 신규 17개/전체 555개 테스트와
실제 validation 15,265행 smoke는 기존 로컬 확인 기록이지 원격 실행 증거가 아니다.
기존 원본359688/Test3 안정79b176e의 PR1 #3과 새 코드 기준의 전체 흐름 검증은 구분한다.
threshold null·F1/Recall/Precision blocked를 임의로 해소하거나 PR1을 Merge하지 않는다.

```text
현재: PR1 사람 Merge → evaluate-model.yml의 read-only evaluate → 집계 두 파일 artifact/Summary
evaluate-model.yml 성공 완료 → 별도 publish-evaluation.yml의 workflow_run
        → trusted default checkout → source workflow ID/name/path/event/repo/success/code 검증
        → source run/attempt의 유일한 비만료 artifact ID → 정확한 두 파일 → app/result_review.py
        → merged PR1·merge/plan/EDA/digest/source/runtime·집계 검증 → JSON 정본으로 보고서 재생성
        → research/result-issue-{issue}-{split}의 두 결과 파일 → 사람 검토용 draft PR2
```

- 기존 계산 workflow의 단일 read-only job·테스트 계약은 변경하지 않는다.
  별도 publisher 한 job만 actions read/contents+PR write를 가지며 모델·AI·ML 의존성 없이 실행한다.
- 결과는 `research/results/issue-{issue}/{split}/evaluation.json`·`report.md`만 저장한다.
  validation/test는 별도 PR이며 제목은 `평가결과 #{issue}: 고정 모델 · {split}`이다.
- 같은 open PR/branch 재사용·blob 비교 no-op·검증된 기존 run 링크 보존·non-force 갱신을 적용한다.
  closed/merged PR2는 deferred, 소유권 불명/추가 편집/경쟁 변경은 중단하며 사람 본문은 `merge_auto_body`로 보존한다.
- 게시 workflow를 저장소별 직렬화하며 쓰기 직전 head·PR 상태/본문을 재조회한다. 사람 제목·Ready 상태를 초기화하지 않는다.
  기존 Markdown도 JSON의 renderer와 대조하고 이전 source run을 재검증해 오래된 run의 덮어쓰기를 거부한다.
- threshold 부족은 not_applicable과 이유로 남기고 EDA failed·환자 중복·validation 재사용 한계를 유지한다.
  자동 Merge·Pages·임상 승인·환자/예측/특성 벡터 게시는 없다.
- 실제 PR2 연결은 새 코드 게시의 별도 명시적 승인·반영과 사람의 PR1 Merge 뒤에만 가능하다.
  PR1 피드백 지연·요약 정확성 공백은 후속으로 남으며 해당 질문이 PR1 변경을 승인한 것은 아니다.

## 2026-10-17: native 정규화와 게시 직전 단일 검사

`agent` 종료 시점의 Python `post_check`는 제거하고 공식 `safe-outputs.steps`로 같은 검사를 옮겼다.
게시 job은 기존 본문·receipt artifact를 읽어 원본 hash와 대상·선언을 확인한 뒤에만 native 쓰기를 진행한다.
본문은 고정된 gh-aw Actions의 `sanitizeContent`를 직접 재사용해 비교한다.
HTML 엔티티 정규화 때문에 정상 본문을 거부하지 않으며, 별도 Python 정규화 구현이나 새 승인 상태를 만들지 않는다.
검사가 실패하면 뒤의 `Process Safe Outputs`는 기본 success 조건으로 건너뛴다.
정상 자료가 있는 경우에 `agent`의 부수 실패만으로 게시를 일괄 차단하는 조건은 추가하지 않았다.
원래 상세 PR 본문과 8개 native job 경계는 유지하며 긴 본문 전달 방식 변경은 별도 범위다.

## 2026-10-17: 같은 runner 안의 반복 준비 축소

- 최초 Issue는 실제 입력 검증과 기본 EDA를 먼저 실행한다. 이 순서는 유지한다.
- 기존 PR은 성공한 native 게시 증거와 Git blob이 일치하면 행 없는 계획 문맥으로 조율을 시작한다.
  지표 선택·기존 점검 제외만 바꾸면 GHCR 추출·CSV 파싱·전체 행 캐시 복원이 없다.
  새 EDA 블록이 필요하면 같은 job에서 자산을 한 번 준비하고 실제 파일·입력을 검증한 뒤 새 블록만 계산한다.
- 최종 게시 준비는 `PLANNING_CONTEXT.json`을 사용한다. 계산용 `VALIDATED_CONTEXT.json`과 실제 자산은 게시 단계에서 다시 읽지 않는다.
- `research_tools.py`를 AI 시작 전 비공개 runner 경로에 복사한다. 각 MCP 도구는 한 Python 프로세스에서 함수를 직접 호출한다.
  보고서 tree 선택 후 재조회는 이 사본의 표준 라이브러리 코드만 실행하고 저장소 코드를 import하지 않는다.
  게시 자료 검사는 실제 쓰기 job에서 신뢰된 워크플로와 공식 Actions 모듈로 실행한다.
- Docker는 데이터 파일 추출에만 `create/cp/rm`으로 사용한다. 연구 요청 처리에서 DB `start/run`이나 모델 실행은 없다.
- 구형 `publish_payload`·`write_pending`·`read_binding`·`run-data-checks`와 선행 import 장치를 제거한다.
  현재 입력 역할 표·중립 보고서, 이미지 제작 코드와 후속 지표 실행기는 유지한다.
- 이전 게시 증거는 7일 보관한다. 누락·만료·입력/코드 변경이면 새 자산 준비로 돌아가며 조작된 증거는 거부한다.

## 2026-10-16: 계획과 실행의 명시적 파일 경계

```text
연구 요청 + 시작 GHCR 데이터의 schema/data_id
  → research.py (공통 CLI·PR/세션·출처 검증)
  → planning_eda (가벼운 목적 정리·매핑/Tool 실행계획, 계산 없음)
  → executor_eda (동일 plan_hash의 등록 블록 계산)
  → planning_eval (실제 결과 기반 지표계획, 계산 없음)
  → safe_outputs/본문 검증 → PR1 사람 선택·승인
```

시작 입력은 `input_contracts.profile_for_data(manifest)`로 선택한 GHCR manifest에서 계약을 구성한다.
저장소의 활성 profile/data_id 등록 목록을 조회하지 않는다. 알려지지 않은 형식·의미 사전 없는 새 컬럼 구조는 중단한다.
계획에는 실제 feature 순서/매핑 해시·data_id·데이터/모델 digest·코드 commit·Tool 계약/정책 hash·입력 참조만 저장한다.
raw metadata·숫자 벡터는 Python context 안에 있고 AI 계획 JSON에 복제하지 않는다.

`plan-eda`와 `execute-eda`는 서로 다른 MCP 도구지만 같은 agent job에 있다.
계획·입력 변경과 필수 진단 누락은 계산 전에 실패한다. 출력은 집계 계약으로 검사하며,
환자 중복 failed/증거 부족 unverifiable를 계산 오류와 구분한다.
후단 추가 진단은 최초 검증 결과와 해당 실행계획을 확인해 새 블록만 계산한다.
최초 실행계획 hash는 평가계획 문맥에 고정하고 최종 실행계획은 같은 PR의 생성 파일에 보존한다.
입력 서명·코드·도구/정책·매개변수가 다른 결과는 재사용하지 않는다.

`executor_eval`은 기존 지표 15개를 호출하는 순수 수치 라이브러리이며 PR1 MCP 경로에서는 호출하지 않는다.
로컬의 별도 Merge 후 평가 workflow가 `model_evaluation.py`를 통해 이 라이브러리를 호출한다.
계획의 planning_complete는 사람 승인 증거가 아니다. 실제 Merge를 확인한 뒤에만 계산·PR2를 진행하며 최종 Pages는 후속이다.
실행 중 데이터 교체·자동 재계획·새 전처리 fit은 추가하지 않는다.
`research-plan.md`는 AI/도구/권한 원본, `.lock.yml`은 공식 compiler의 생성 결과다.
`review-eval-plan.yml`/`review_eval_plan.py`는 권한 확인한 자연어 피드백을 AI에 전달한다.
Git 파일·JSON 읽기는 입력 단계와 함께 `github_io.py`를 사용한다. Merge 이벤트 처리는 없다.
기존 블록·등록 목록과 안전한 게시·재시도·입력/승인 검증은 재사용한다.

## 현재 연결 범위: Target → EDA → Evaluation Plan

```text
연구 Issue / prepare-eda-pr.yml
  → default branch의 신뢰 코드와 폼 YAML 계약
  → 권한·폼 확인 후 Issue 또는 기존 PR·run ID/회차·source SHA 전달
  → 선행 workflow 성공 완료 이벤트(workflow_run)
  → resolve_review: 동일 저장소·기본 브랜치·등록된 선행 경로·전달 출처 검증
  → research-plan.lock.yml의 Copilot 엔진
      고정 DATA 추출·입력 검증, 확정 bindings 재사용 / 미확정만 AI 매핑
      Python이 기본 EDA를 먼저 실행 → 공개 목적·기본 집계·미사용 블록을 AI에 제공
      AI 추가 점검 계획 → Python 검증·기존 결과 재사용·추가 블록만 실행
      최종 EDA 기반 AI 지표 추천 → Python 최종 계획 조합 (늦은 EDA 실행 없음)
      최초는 native create-pull-request, 피드백은 기존 PR push/본문 갱신
      AI 댓글: 변경·제한·사람의 선택/승인 대기 안내
  → review-eval-plan.yml의 자연어 피드백
      사람의 현재 write/maintain/admin 권한·원본 API·본문 hash 확인
      자연어는 본문 없는 대기열 → workflow_run → AI 조율·Python 검증
  → 사람이 Ready for review → Merge
  → Merge된 계획과 commit을 승인 기준으로 사용 (별도 승인 JSON 없음)
```

EDA는 데이터 점검, Evaluation은 예측 결과 지표다. 서로 다른 카탈로그와 실행 블록을 사용한다.
15개 지표 계산 모듈은 준비했지만 이 연결에서는 모델 추론·지표 계산을 호출하지 않는다.
기본/필수 선택은 `planning-policy.json`, 연구별 선택은 `selection.json`, 실제 AI 입력은
`ai-recommendations.json`으로 분리한다. 초기 AI는 주지표·threshold를 임의 확정하지 않는다.
사람이 자연어로 명시한 변경만 AI가 형식화하고 Python이 검증해 selection에 반영한다. Merge 승인은 별개다.
알려진 입력은 기본 EDA부터 수행한다. AI는 그 결과로 미사용 점검을 선정한다. 미확정 매핑만 기본 실행 전에 해결한다.
입력 매핑·목적 조율·후단 추천의 context hash는 구별한다.
앞단은 공개 목적·스키마·현재 선택, 후단은 여기에 전체 실제 EDA 집계를 받는다.
매핑이 없으면 추측한 환자 키로 EDA를 실행하거나 중복 0명으로 통과시키지 않는다.
`planning_complete`는 추천 조합 완료이지 사람의 승인 완료가 아니다.
보고서의 별도 승인 준비 조건은 AI provided·명시적 주지표·선택 평가항목 blocked 없음이다.

AI pending/stale은 미완료로 남기며 가짜 빈 추천을 성공으로 만들지 않는다.
Copilot native 인증은 github.token과 copilot-requests: write이며 PAT/AI Secret을 요구하지 않는다.
준비·검토 단계는 토큰으로 새 워크플로를 dispatch하지 않으므로 actions: write를 제거했다.
사수님 analysis-verification.md와 같은 workflow_run 연결을 사용하며 기본 사람 역할 검사를 유지한다.
on.bots 예외나 roles: all은 추가하지 않는다. 대상 해석 job의 actions: read는 전달 artifact 조회용이다.
대상은 해당 실행이 기록한 PR 한 개이며 최신 PR 추측·다른 저장소·다른 브랜치·변조/만료 전달 파일은 거부한다.
서로 다른 실행은 run ID/회차로 구분하며 Merge는 이 피드백 workflow를 실행하지 않는다.
정적 branch 제한 대신 source run의 실제 default branch와 경로를 코드에서 검증하므로 compiler 경고 1개는 남는다.
request.json·feedback-requests.json은 AI 게시에서 제외한다.
selection.json은 검증된 목적 합치기 또는 권한 확인한 피드백 변경이 있을 때만 허용 목록에 포함한다.
PR의 사람 본문·제목·기존 댓글은 보존하며 새 AI 안내 댓글을 최대 1개 추가할 수 있다.
coordination-plan.json은 원문 대신 목적/선택/스키마/피드백 hash와 최초 EDA 근거를 저장한다.
후단 추가 점검으로 최초 근거 hash가 순환 변경되지 않도록 분리한다.

입력 확장 범위는 등록된 준비 이진분류 자산이다. 기존 v2 Diabetes 검증은 유지한다.
등록형 v1은 source file hash·data_id·feature schema·3분할을 검사하고 원본 컬럼을 표준 역할로 연결한다.
단순한 이름 추정으로 환자 의미를 승인하지 않으며 등록 의미 근거가 없는 후보를 허용하지 않는다.
generic 경로의 코호트/정답/인구집단/전처리 해석은 데이터셋 문맥을 사용하고 Diabetes 전용 가정은 적용하지 않는다.
환자 중복 계산 자체는 같은 patient_key 집합의 교집합이다. 지원된 합성 스키마 두 개로 동일 결과를 확인한다.

자연어는 AI 조율의 데이터이며 코드로 실행하지 않는다. 변경안의 등록 ID·입력·매개변수는 Python이 검증한다.
연속 댓글은 미처리 ID 순서로 처리하고, 이미 처리한 댓글 편집은 새 요청으로 보지 않는다.
미반영 자연어 편집은 hash를 갱신한다. AI 준비·게시 전 원본 작성자/권한/본문/PR을 다시 확인한다.
미반영 요청은 같은 PR의 댓글·피드백 기록에서 확인하고 사람이 검토한다.
PR 코드·사용자 셸·SQL은 실행하지 않는다. 원래 checkout의 HEAD/index/origin도 변경하지 않는다.
보고서 브랜치 checkout 전에 도우미 모듈을 신뢰 코드에서 고정하고, CLI의 __main__/app.research 참조를 같은 모듈로 유지한다.
CHECKS는 기존 catalog의 미리 import된 함수 참조를 사용한다. 보고서 브랜치에 Python 파일이 바뀌어 있어도 새로 불러오지 않는다.

승인 기준은 사람이 Merge한 계획과 commit이다. 별도 승인 JSON·동결 검증·추가 main 쓰기를 제거했다.
계획 준비 상태는 안내이며 Merge 자체를 별도 코드로 차단하지 않는다.
모델 실행을 연결할 때 필요한 입력·허용 블록·필수 매개변수 검사는 실제 실행 경계에서 유지한다.
승인은 교육용 계획 확정이며 임상 사용 승인이 아니다. 알려진 환자 중복·불확실성은 보고서에 그대로 남는다.

운영 범위는 템플릿 코드 반영과 PR 생성 허용 설정까지다.
독립 복사본 설정·갱신과 실제 Issue/Copilot/PR 승인 흐름 확인은 사용자가 수행한다.
템플릿에서 이 연결의 실제 원격 이벤트 성공을 검증했다고 주장하지 않는다.

## 2단계: 준비파일을 읽는 EDA 경로

```text
검증된 data/assets (+ 선택적 고정 UCI raw)
  → app.executor_eda.load_context
      validate_data_manifest / validate_dataset 재사용
      metadata 1회 적재, feature row 스트리밍
  → catalog.run_checks
      patient_overlap / demographics / schema (기존 보강)
      cohort / prediction_timing / label_observability / preprocessing_lineage (신규)
  → 집계 전용 JSON: report_generated와 assessment_status를 분리
```

공통 계약은 `run(context: dict) -> {block, status, details}`, catalog는 목록을 반환한다.
metadata는 현재 DB의 9개 필드, feature row는 row_key/split/row_position/feature_values다.
context에는 ordered feature_columns/expected_feature_columns, target_mapping, data_manifest와 선택적 raw 집계를 전달한다.
`_shared.py`는 split alias와 엄격한 bool/target/필수키 검사만 공유하며 클래스/설정 프레임워크를 추가하지 않는다.
`valid`는 명시적 alias로만 지원하고 분할 출력은 validation으로 고정한다.

실제 101,766행: overlap `failed`, demographics `completed`, 나머지 5개 `unverifiable`.
schema의 측정 가능한 numeric 계약은 passed지만 의미/단위 근거는 부족하다.
cohort는 출처의 입원/당뇨/1–14일/검사/투약 선정 설명과 raw 행별 count 검사를 분리한다.
예측시점 승인·가용시점 사전, 추적 종료/외부병원 재입원 포착, 역사적 fit 범위가 없어 임의로 인증하지 않는다.
환자/입원 키와 벡터는 JSON·로그에 출력하지 않는다. 모델 artifact를 로드하거나 실행하지 않는다.

구형 pair 초기화·학습 경로와 호출자 없는 pair 계약은 제거했다. 현재 입력·수치·출력 집계 계약은 유지한다.

Issue→EDA→연구검토 draft PR과 3단계 연결은 위 현재 범위를 따른다.
모델 추론·실제 결과 지표 실행·재전처리·재분할·자산 재게시는 이번 범위 밖이다.
게시 workflow·고정 source/digest·Dockerfile은 유지하고 폼의 자동 연결 안내만 갱신했다.

## 2단계 자동 연결

```text
prepare-eda-pr.yml: issues.opened / workflow_dispatch(issue_number)
  → default branch checkout (사용자 ref 입력 없음)
  → 폼 YAML 의존성 설치
  → app.research workflow:
      작성자 신뢰/수동 권한 + GitHub API JSON의 정확한 5필드 폼 검사
      Issue 대상만 전달, 데이터 추출·준비 PR 생성 없음
  → native agent의 prepare-ai:
      고정 data_image 추출·manifest/입력 검증·필요한 매핑
      기본 EDA → AI 추가 선정 → 추가 실행 → 최종 지표 추천
      결과 준비 후 research/review-issue-{번호} branch의 최초 PR 생성
```

모델 이미지·raw 원본은 받지 않고 DB 서비스를 실행하지 않는다. 임의 ref/image/path/SQL/Python을 입력받지 않는다.
준비 권한은 contents/PR/issues read다. 댓글 큐만 contents write를 사용하며 실제 PR 쓰기는 native safe outputs에 분리한다.
Public data pull에는 packages write·새 자격증명이 필요 없다.
OWNER/COLLABORATOR Issue만 자동 진행하고 다른 작성자는 수동 dispatch를 요구한다.
수동 실행자는 API write/maintain/admin 권한을 확인한다.
저장소·default branch는 event의 동적 값이고 본문은 event 파일/API JSON에서만 파싱한다.

최초 PR 생성 전에 실제 EDA와 추천을 완료한다. 댓글은 같은 PR을 대상으로 필요한 계산과 계획만 갱신한다.
완성 PR은 원본 Issue·data digest/data_id/revision·코드 commit·실행 링크, 상태/집계 표/한계와 평가 계획을 담는다.
추천은 실제 지표 계산이 아니다. 주지표/threshold는 사람이 정하며
DCA는 임상 행동·threshold 범위, NRI는 비교모델·대응 입력이 없으면 blocked로 남긴다.
같은 Issue는 직렬화해 열린 PR을 갱신하고 사용자 파일/PR 본문/댓글을 보존한다.
closed/merged PR은 자동 보류, 내용 동일하면 commit 없음, force push/amend/자동 merge 없음.
branch가 남고 PR 생성만 실패한 재실행은 Issue marker를 확인해 같은 branch에서 복구한다.

이 연결의 실제 이벤트·Public 이미지 추출·Copilot·GITHUB_TOKEN PR 생성 시연은 템플릿에서 실행하지 않는다.
토큰 생성 PR의 다른 CI trigger 제한과 Actions PR 생성 허용 설정은 README의 활성화 전제를 따른다.

## 현재 수직 흐름

```text
workflow_dispatch (운영자)
  → checkout + Python 3.12 (게시 과정은 표준 라이브러리만)
  → 합성 자산 계약 테스트
  → github.repository 기반 -db / -model 이름과 태그 검증
  → 고정 revision의 준비 train/validation/test + demographics 다운로드
  → 고정 SHA UCI 원본으로 encounter 키/target/인구집단 연결 검증
  → bytes/SHA/rows/113 변수 순서/numeric finite/known overlap 검사
  → 별도 컨텍스트:
       data/: 준비 CSV + 복원 metadata + DB manifest/init/healthcheck
       model/: 고정 checkpoint + 입력/버전/한계 manifest + 라이선스/출처
  → PostgreSQL 데이터 이미지 빌드
  → GitHub runner에서 실제 DB 기동·전체 metadata/feature/asset_info 대조
  → scratch 모델 artifact 이미지 빌드
  → 실행하지 않고 이미지 filesystem만 추출해 모든 파일 해시/manifest 대조
  → GITHUB_TOKEN 로그인, 두 공통 패키지 push
  → 두 @sha256 참조와 집계·알려진 한계를 Summary에 기록
  → 운영자가 Public 전환·실제 참조 등록 (자동 commit/push 없음)
```

GHCR은 실행 DB가 아니라 재현할 이미지와 파일의 저장소다.
실제 DB는 검증 중 runner에서만 `--network none`으로 실행하며 host 포트를 매핑하지 않는다.
모델은 `docker create`/`cp`로 파일만 검사하고 `docker run`/predict를 하지 않는다.
게시 실패 시 두 레지스트리 push를 원자적으로 롤백하는 기능은 없으므로 두 digest를 함께 검토한다.

## 파일 경계

| 경로 | 현재 책임 |
|---|---|
| `.github/workflows/publish-db-image.yml` | **Establish Database Image**, 운영자 수동 게시. contents read / packages write |
| `app/prepared.py` | 고정 자산 수입·검증·context·DB/모델 artifact runner 검사 |
| `config/prepared-source.json` | 이미지 제작·재게시 전용 source revision/URL/SHA/bytes/rows/schema/target/모델 명세. 일반 연구 실행은 이미지 manifest를 사용 |
| `app/asset_contract.py`, `app/dataset.py`, `app/ghcr.py` | 기존 hash·UCI 명세·GHCR 이름/digest 도우미 재사용 |
| `data/` | 출처/NOTICE와 원본 명세만. Git에 CSV·바이너리 없음 |
| `db/` | PostgreSQL Dockerfile/init.sql/healthcheck.sh |
| `app/Dockerfile` | `FROM scratch` 모델 artifact. 학습/추론 런타임 없음 |
| `tests/test_prepared_source.py` | 새 자산 계약·모의 다운로드/runner·무학습/무추론 검증 |
| `.github/workflows/ci.yml` | 전체 합성 회귀 검증. 지표 비교 테스트에는 고정 ML 의존성 사용 |
| `.github/ISSUE_TEMPLATE/research-request.yml` | 기존 5필드·동의 유지, 자동 연결과 복사본 PR 생성 설정 안내 |
| `.github/workflows/prepare-eda-pr.yml`, `app/research.py` | 경량 Issue intake→정확한 대상 전달, native 기본 EDA→추가 점검→추천 이후 최초 PR |
| `app/planning_eval.py`, `config/*-catalog.json`, `config/planning-policy.json` | 독립 카탈로그·정책·AI·사용자 선택 조합, 별도 승인 준비 판정 |
| `app/blocks/metrics/` | 독립 결과 지표 15개와 입력 검증. 3단계에서는 계산하지 않음 |
| `research-plan.md`, `research-plan.lock.yml`, `app/research.py` | 실제 gh-aw Copilot 엔진·공개 집계·추천 검증·기존 PR 안전 갱신 |
| `review-eval-plan.yml`, `app/review_eval_plan.py` | 자연어 피드백 접수·AI 전달. 별도 명령 파서·Merge 승인 JSON 없음 |
| `app/github_io.py` | 입력 매핑과 피드백 단계의 Git tree·blob 읽기 및 엄격한 JSON 파싱 공통화 |
| `app/planning_eda.py`, `tests/test_pr1_intent.py`, `tests/test_eda_execution_plan.py` | 가벼운 목적·피드백 문맥, 등록 조율안·시작 입력 실행계획 계약·감사 hash |
| `app/executor_eda.py` | 입력/출력 계약을 검증해 등록 EDA 블록 실행·집계·정확한 캐시 재사용 |
| `app/executor_eval.py`, `tests/test_executor_eval.py` | 기존 15개 지표의 순수 실행·명시적 매개변수·출력 검증, Actions 미연결 |
| `app/recommendation_events.py` | schema2의 Issue/기존 PR 대상·출처·작성자를 검증해 native job으로 전달 |
| `app/input_contracts.py` | GHCR manifest의 입력 의미·무결성·확정/AI 매핑·표준 context·저장 매핑 검증. 저장소 profile registry 제거 |
| `app/input_review.py` | 입력 역할 표시·데이터셋 중립 집계 보고서 |
| `app/research_tools.py` | 비공개 runner 도구 어댑터·단일 프로세스 호출·표준 라이브러리 게시 재조회 |
| `tests/test_input_contracts.py`, `tests/test_input_pipeline.py` | 다른 컬럼 스키마·매핑 전 EDA 금지·두 단계 AI·승인 매핑 변조 거부 |
| `tests/test_recommendation_events.py` | 합성 이벤트·실제 ZIP 파싱·출처/대상/만료/변조/경로 침범 거부. 실제 원격 실행 아님 |
| `tests/test_research_workflow.py` | 입력/권한/이미지·GH 모의/멱등 갱신/closed PR/집계·추천 계약 회귀 |
| `config/assets.json` | 실제 게시된 data/model 두 digest 고정 참조. 두 패키지 Public·익명 조회 확인 |

`app/prepared.py`는 ML 모듈을 import하지 않는다. 모델 pickle을 역직렬화하지 않으며 bytes·hash·명세만 검사한다.
이전 원본 전처리/새 학습·pair 초기화/추론 코드와 해당 테스트·중단 전용 초기화 workflow는 제거했다.
현재 준비 데이터/모델 artifact 계약과 등록 Tool 실행만 유지하며, 구형 학습·추론 경로로 되돌아가지 않는다.
새 model artifact는 기존 inference image와 다르므로 초기화가 완료됐다고 해석하지 않는다.

## 입력과 DB 계약

입력 사실은 선택한 digest의 이미지 안에 있다. 기존 `prepared-data-only` v2의 명세·컬럼 역할 계약은 그대로 지원하며 새 필드를 강제로 추가하지 않는다.
다른 컬럼 구조의 `registered-prepared-input` v1은 manifest의 `input_metadata`에 의미 사전과 값 매핑을 명시한다.
의미 메타데이터도 data_id에 포함한다. 실행 중 원본/의미가 바뀌면 기존 계약과 추천을 거부하며 자동 재계획하지 않는다.
입력 검증과 실행 시 고정 GHCR digest의 실제 자산·manifest·매핑을 검증한다.
Merge 후 승인 스냅샷을 위한 별도 재다운로드와 감사 검증은 수행하지 않는다.
이 경계는 원본 재전처리·모델 로딩/학습·지표 계산을 추가하지 않는다.

- 데이터: HF `auphong2707/hospital-readmission-risk-data`, revision `4a61a3591f079628c72f5edbc7494ca7dda8741d`, `splits/`만 선택.
  train 71,236 / validation 15,265 / test 15,265. 이미 표준화된 113 X + target.
- target: 원본 `<30`만 1, `>30`/`NO`는 0. 원본 SHA `f792c388d9b470aac4fbf21c39b176534d55a116e7626881c30bbe7ab0341422`.
- `prepared.metadata`: encounter row_key, 원본 patient_key, split, target, race/gender/age_group,
  race_original, race_was_missing. 키는 feature vector에서 제외한다.
- `prepared.features`: metadata와 row_key/split FK, 분할 내 row_position, 순서가 고정된 JSONB 숫자 배열.
- `prepared.asset_info`: data_id, 총/분할 행수, feature_count, 분할 쌍별 환자 중복과 원본 인종 결측 집계.
- init/healthcheck는 모델 pair를 요구하지 않는다. 데이터 입력 계약만 검증한다.
  중복 0 대신 train–validation 5,055 / train–test 5,086 / validation–test 1,760 집계 일치를 확인한다.
- 원본 race 결측은 train 1,596 / validation 336 / test 341개.
  공개 meta의 Caucasian 대치값과 원본 결측 플래그를 동시에 보존한다.

데이터 manifest는 schema 2 / `prepared-data-only`, 모델 manifest는 schema 2 / `model-artifact`다.
기존 schema 1 pair_id/전처리기/환자 독립 분할 계약을 우회하는 것이 아니라 **별도의 현재 자산 계약**이다.
모델 manifest는 data_id·원본SHA·113 변수 순서·target·classes·checkpointSHA·라이브러리 버전과 한계를 기록한다.
학습된 전처리기나 120-feature scaler가 새 계약의 필수 입력인 것처럼 추가하지 않는다.

## 생성 컨텍스트

| 저장소 파일 | runner 생성 컨텍스트 |
|---|---|
| `db/Dockerfile`, `.dockerignore` | `data/Dockerfile`, `data/.dockerignore` |
| `db/init.sql`, `db/healthcheck.sh` | `data/db/`의 같은 파일 |
| 검증된 데이터 CSV·sidecar와 생성 명세 | `data/assets/` |
| `app/Dockerfile` | `model/Dockerfile` |
| 검증된 checkpoint·model manifest·MIT source README·NOTICE | `model/assets/` |

원본 CSV는 runner source 폴더에서 검사한 뒤 제거한다. 데이터/모델 CSV·바이너리는 Git·Artifacts·Releases에 넣지 않는다.
로컬 검증에서는 기존 획득 자산을 읽기 전용으로 사용할 수 있으며 빌드 context는 Git 바깥에 둔다.
실제 Docker는 사용자 승인에 따라 GitHub-hosted runner에서만 실행했다. 로컬 Docker는 실행하지 않았다.

2026-10-15 [run 38722808263](https://github.com/t-hajongkim/Medical-ML-SDLC/actions/runs/38722808263)이 성공했다.
실제 DB의 metadata/feature 각 101,766행과 asset_info 전체가 일치하고 모델 filesystem 무결성 검사도 통과했다.
두 패키지 visibility를 workflow token의 read API로 Public 확인하고 외부에서 익명 OCI manifest 조회도 확인했다.
실제 이미지 source/revision은 `t-hajongkim/Medical-ML-SDLC` / `5c0fc144687766c0306e8b7150f3228579ac7a0f`이다.
후속 digest 참조 기록 커밋을 빌드 소스인 것처럼 바꾸지 않는다. 고정 참조는 `config/assets.json`을 읽는다.

## 해석 및 후속 경계

저장 checkpoint는 sklearn 1.2.2, 기존 수동 검증 환경은 1.6.1이며 버전 경고 아래 test 지표를 재현했다.
현재는 준비 CSV 실습용 조건부 등록만 한다. 원본 입력 파이프라인·실행 추론 이미지·임상 평가를 완성한 것이 아니다.
공개 validation의 개발 재사용, 환자 중복, target encoding/역사적 113 변환 불확실성 때문에 독립·누수 없는 holdout 주장을 하지 않는다.

참가자 초기화/개인 패키지, 후속 모델 실행·최종 모델 평가·Pages는 후속 범위다.
템플릿만 코드와 PR 생성 설정을 반영하며 복사본의 실제 흐름 확인 뒤 사용자 코드 리뷰를 진행한다.
폼 자유 입력은 연구 요청 데이터이지 임의 Python/SQL 실행 코드가 아니다.
