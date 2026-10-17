# Medical ML SDLC Workshop

구현 범위는 **Target → 기본 EDA → AI의 미사용 EDA 선정 → 추가 실행 → 최종 결과·지표 추천 뒤 PR1 생성 → 자연어 피드백 반영 → 사람이 계획 Merge**입니다.
전처리·train/validation/test 분할·대응 모델 준비는 완료된 입력 계약으로 가정하며 폼은 유지합니다.
준비된 Diabetes 데이터와 검증된 모델 파일은 공통 GHCR에 게시했습니다.
EDA 점검 7개와 결과 지표 15개는 별도 카탈로그로 관리하며, 기본·AI·사용자 선택을 조합합니다.
**계획 확정 자체는 모델 실행·최종 성능 평가·임상 사용 승인이 아닙니다.**
**Merge 후 고정 모델 추론 → 선택 지표 계산 → Actions 집계 결과 → draft PR2** 연결을 구현했습니다.
계산과 게시 workflow를 분리하며 원격 성공은 실제 run과 결과 PR로 확인합니다. Pages는 후속 범위입니다.
게시 YML은 새 학습·전처리 fit·모델 로딩·predict를 실행하지 않습니다.
GHCR은 이미지 레지스트리이지 실행 중인 DB가 아닙니다.

## 코드 읽기: 계획과 실행의 네 경계

| 모듈 | 책임 | 하지 않는 일 |
|---|---|---|
| `app/planning_eda.py` | 가벼운 목적/피드백 정리, 등록 입력 서명·블록 ID·입력 참조의 단방향 실행계획 생성/검증 | EDA 계산·모델 실행 |
| `app/executor_eda.py` | 계획의 입력/출력 계약 확인, 등록 EDA 계산·집계·검증된 결과 재사용 | AI 추론·임의 코드·자동 재계획 |
| `app/planning_eval.py` | 목적·실제 EDA·AI 추천·정책·사용자 선택으로 평가계획/승인 준비 판정 | 지표 값 계산·사람 승인 대체 |
| `app/executor_eval.py` | 명시적으로 요청한 기존 지표 15개의 순수 계산·출력 검증 | 모델 로딩/predict·GitHub/네트워크 IO·승인 부여 |
| `app/inference.py` | 고정 자산/모델 검증·행 연결·113-feature 모델 확률 추론 | fit·추가 scaler·재분할·임의 모델 로딩 |
| `app/model_evaluation.py` | 실제 Merge된 계획 확인·추론/지표 연결·집계 결과 게시 | AI 재계획·개별 확률 게시·PR2/Pages·임상 승인 |
| `app/result_review.py` | 성공한 평가 출처·저장 계획·집계를 검증하고 두 결과 파일의 draft PR2 생성/갱신 | 모델/지표 재실행·threshold 생성·자동 Merge·임상 승인 |

실제 AI 추론은 기존 gh-aw Copilot이 수행합니다. Python Planner는 AI 제안과 계약을 검증합니다.
MCP의 `plan-eda`는 **계획만** 저장하며, `execute-eda(plan_hash)`가 같은 계획을 확인한 뒤 계산합니다.
정해진 기본 EDA는 같은 Planner/Executor를 사용해 AI 추천 전에 계산합니다. 미확정 입력 매핑만 먼저 해결합니다.
그 뒤 `read-recommendation-context`와 `apply-recommendations`로 평가계획과 게시 결과를 준비합니다.
`research.py`는 공통 CLI·실행 세션·GitHub/게시 연결을 맡고, `review_eval_plan.py`는 자연어 댓글을 AI에 전달합니다.
`github_io.py`는 입력 매핑과 피드백 단계의 Git 파일 읽기·JSON 검증을 공통으로 제공합니다.
이전 `cloud_recommendations.py`의 중간 연결은 별도 파일로 남기지 않습니다.

실행 시작 시 선택한 GHCR manifest에서 입력 계약을 구성합니다. 저장소의 데이터셋별 profile/data_id 등록 목록은 두지 않습니다.
알 수 없는 형식이나 의미 사전이 없는 새 컬럼 구조는 중단하며,
AI가 의미 사전 없이 환자 키·정답 의미를 추측하거나 기존 학습 특성을 바꾸지 않습니다.
**실행 중 데이터 교체·감시·자동 재계획 루프는 없습니다.** 사람의 새 댓글은 별도의 명시적 수정 요청입니다.
`execution-plan.json`은 데이터/모델 digest·코드·입력 서명·Tool 참조를 고정하는 생성 결과이며 공통 config를 덮어쓰지 않습니다.
PR1 경로는 합성 회귀·공식 compiler로 확인합니다. 새 로컬 모델 연결은 고정 자산의 실제 추론도 별도로 확인하며,
원격 workflow 성공은 해당 run의 artifact/Summary와 결과 PR이 있을 때만 주장합니다. Pages는 추가하지 않습니다.

## Merge 뒤 고정 모델 추론·평가

```text
[PR1: EDA·평가계획] → [사람 Merge]
                             |
                             v
[evaluate-model.yml] → [model_evaluation.py: Merge SHA의 저장 계획 확인]
                             |
                             v
[inference.py: 고정 DATA + MODEL 검증 → validation 확률 추론]
                             |
                             v
[executor_eval.execute_plan: 선택된 기존 지표 계산]
                             |
                             v
[Actions: evaluation.json + report.md + Summary]
```

- 자동 진입은 같은 저장소의 `research/review-issue-*` PR이 **Merge되어 닫힌 경우**입니다.
  Actions 수동 실행도 정확한 **Merge된 PR 번호**가 필요합니다. Issue·댓글은 모델을 실행하지 않습니다.
- 기본 분할은 `validation`입니다. `test`는 수동 실행에서 명시적으로 선택합니다. train·전체 분할·자동 재분할은 허용하지 않습니다.
- 모델 저장 버전에 맞게 **Python 3.11 / scikit-learn 1.2.2 / NumPy 1.24.4**를 `requirements-inference.txt`로 고정합니다.
  기존 합성 CI의 `requirements-ml.txt` 환경은 바꾸지 않습니다.
- 모델은 현재 등록된 113-feature LogisticRegression과 대응 고정 자산으로 제한합니다. 다른 모델/데이터를 자동 지원하지 않습니다.
  manifest·파일 hash·특성 순서·클래스·모델 저장 버전을 확인한 뒤에만 checkpoint를 로드합니다.
- `row_key`·split·row_position으로 정답을 연결합니다. 모델의 feature names가 있으면 이름과 순서도 대조합니다.
  이미 표준화된 입력에 추가 scaler·fit·학습을 적용하지 않습니다.
- 저장 계획의 threshold·선택 지표를 유지합니다. 빠진 threshold·참조 확률·임상 행동은 만들지 않고 기존 `not_applicable`을 보존합니다.
- Actions는 단일 hosted job과 읽기 권한만 사용합니다. 데이터·모델은 파일로 추출하고 DB를 시작하지 않습니다.
  labels·확률·특성·키는 메모리/임시 경로에서만 사용하며 공개 파일은 집계 JSON과 Markdown 두 개뿐입니다.
- 계획 당시 코드·이미지·매핑과 현재 계약이 달라지면 stale 오류로 멈춥니다. 옛 보고서 브랜치의 Python을 checkout해 실행하지 않습니다.
- 로컬 실제 검증: 고정 validation **15,265행**, ROC AUC **0.8151440032**, AP **0.3107697977**, Brier **0.1766472449**, calibration 10 bins.
  보존된 보고서 계획을 사용한 로컬 실행이며 원격 Merge workflow 실행 증거가 아닙니다.
  환자 중복·validation 개발 사용·전처리 이력 한계를 유지하며 독립 임상 성능으로 해석하지 않습니다.

### PR2 결과 검토

- `evaluate-model.yml`의 단일 read-only 계산 job은 그대로입니다. 별도 `publish-evaluation.yml`만 성공한
  `Evaluate merged research plan` 실행을 받아 trusted default 코드로 동작합니다.
- 다운로드 전에 현재 API의 workflow ID/이름/경로·repo·run/attempt·성공·관련 코드와 유일한 비만료 artifact ID를 확인합니다.
  다른 실행/이전 attempt artifact로 fallback하지 않습니다. publisher는 모델·ML 의존성을 설치하거나 추론하지 않습니다.
- 실제 merged PR1과 저장 계획·EDA·DATA/MODEL digest/ID·source/runtime·지표 선행조건을 다시 검사한 뒤,
  `research/result-issue-{issue}-{split}`의 `research/results/issue-{issue}/{split}/evaluation.json`과 `report.md`만 게시합니다.
- 최초 PR은 draft입니다. open PR의 사람 제목·본문 자동 영역 밖 메모·review 상태를 보존하고, 같은 의미의 결과는
  이전 검증된 run 링크를 유지해 불필요한 commit/PR 수정이 없습니다. Markdown 수동 수정·추가 파일·불명확한 branch는 중단합니다.
- 게시를 저장소별 직렬화하고 branch/PR을 쓰기 직전에 재조회합니다. 이전 run이 새 결과를 덮어쓰지 않으며 non-force만 사용합니다.
  PR 생성 실패 뒤 branch는 유지해 검증 후 복구하고, closed/merged PR2는 `deferred`로 남깁니다.
- threshold 미지정은 `not_applicable`/null과 사유로 남습니다. 교육용 결과 검토는 임상 사용 승인이 아닙니다.
  기존 Test3 PR1 #3은 보존합니다. 코드 변경 뒤에는 최신 버전으로 새 계획을 생성한 후 검토·Merge합니다.
  코드 게시와 사람의 PR1 Merge가 별도로 이루어진 뒤에만 원격 동작을 확인할 수 있습니다.

### GitHub에 테스트 파일이 필요한 이유

`tests/`는 실제 분석 단계가 아니라 **코드 변경의 계약·회귀 확인**입니다.
`ci.yml`이 push·pull_request·수동 실행에서 테스트를 실행합니다.
Issue·자연어 댓글 처리에는 전체 테스트가 없으며, Merge 후 `evaluate-model.yml`은 테스트 대신 **실제 고정 자산의 추론·평가**를 실행합니다.
테스트를 저장소에서 지우면 현재 CI와 검증 근거가 함께 사라지므로 유지합니다. 페이지의 업무 흐름에서는 접힌 검증 영역으로 구분합니다.

## 참가자 주의사항

> [!IMPORTANT]
> 점검 결과를 검토용 PR로 자동 생성하려면 **각자 복사한 저장소에서 PR 생성 허용 설정을 확인**해야 합니다.
> 원본 저장소의 설정이 실습 복사본에도 적용됐다고 가정하지 마세요.

1. **Settings → Actions → General → Workflow permissions**로 이동합니다.
2. **Allow GitHub Actions to create and approve pull requests**를 체크합니다.
3. **Save**를 누릅니다.

- `GH_TOKEN: ${{ github.token }}`은 Actions가 자동 발급한 토큰을 사용합니다. **PAT를 만들거나 Secrets에 토큰을 별도 등록할 필요가 없습니다.**
- Copilot 추천은 실행 계정의 이용 권한·조직 모델 정책을 따릅니다. 코드 컴파일 성공이 실제 엔진 접근을 보장하지 않으며, 별도 Secret으로 권한을 우회하지 않습니다.
- 설정명에 `approve`가 있어도 현재 워크플로는 **검토용 draft PR만 생성**합니다. 자동 승인·병합은 하지 않습니다.
- 조직 정책으로 설정을 변경할 수 없다면 저장소·조직 관리자에게 확인하세요.
- 이 설정만으로 자동화 코드가 설치되지는 않습니다. 실습 저장소에도 승인된 EDA 연결 코드를 반영해야 합니다.

### EDA 뒤 PR 생성이 실패할 때

결과 브랜치는 있는데 PR이 없고 native PR 생성이 403으로 실패했다면 **복사한 저장소 자체의 PR 생성 허용 설정**을 확인합니다.
템플릿에서 켠 옵션은 복사본에 상속되지 않습니다.
`agent`가 성공하고 `safe_outputs`의 권한 오류만 남았다면 설정을 저장한 뒤 **Re-run failed jobs**로 게시 job을 재시도할 수 있습니다.
`agent`도 실패한 이전 실행은 전체 초기 분석을 무작정 재시도하지 마세요. 기존 결과 브랜치와 게시 artifact의 일치부터 확인해야 합니다.
결과 브랜치는 복구 근거이므로 삭제하지 않습니다. 기존 브랜치 덮어쓰기나 새 Issue 생성으로 우회하지 않습니다.
이 설정을 확인해도 실패한다면 워크플로의 `pull-requests: write`와 조직 정책·브랜치 규칙을 확인합니다.

## 먼저 볼 워크플로

### 이번 PR1 구현을 리뷰하는 순서

| 순서 | 파일·함수 | 확인할 내용 |
|---|---|---|
| 1 | `research.py`: `run_workflow`, `prepare_ai`, `_begin_basic_eda` | 초기 Issue는 전달만 하고, 기본 EDA가 AI 추가 선정 전에 완료되는지 |
| 2 | `planning_eda.py`: `build_context`, `apply_proposal`, `build_execution_plan` | 실제 기본 집계·미사용 후보·허가된 댓글을 검증해 실행계획을 만드는지 |
| 3 | `research.py`: `plan_eda`, `execute_eda` / `executor_eda.py`: `execute_plan` | 기존 결과를 재사용하고 요청된 새 블록만 계산하는지 |
| 4 | `planning_eval.py`: `build_plans` / `research.py`: `apply_ai`, `write_payload` | 지표 단계에서 EDA를 추가하지 않고 최종 계획을 저장 과정에서 재조립하지 않는지 |
| 5 | `recommendation_events.py`, `research-plan.md`, 준비/댓글 YML | Issue 최초 생성과 기존 PR 수정의 정확한 대상·권한·native 게시 분기 |
| 6 | `research.py`: `_authenticate_prior_eda`, `_prior_planning_context`, `_execution_context_for_session` | 이전 집계의 게시 출처를 검증하고 새 계산이 있을 때만 자산을 준비하는지 |
| 7 | `research_tools.py` / `research-plan.md` | 한 도구 호출에서 Python을 중첩 실행하지 않고 게시 후에는 신뢰된 비공개 어댑터만 사용하는지 |

순서 테스트는 `test_pr1_flow.py`, 재사용 출처 테스트는 `test_eda_cache_provenance.py`,
native 도구·게시 검사는 `test_cloud_recommendations.py`와 `test_recommendation_events.py`에 있습니다.
기본/지표 값은 `planning-policy.json`에서 확인합니다. 모델 실행·최종 평가·Pages는 이 변경의 범위가 아닙니다.

| 파일 / Actions 표시 이름 | 역할 | 현재 상태 |
|---|---|---|
| [publish-db-image.yml](.github/workflows/publish-db-image.yml) / **Establish Database Image** | 고정 공개 자산 검증 → DB 이미지 빌드·스모크 → 모델 파일 이미지 빌드·무결성 검사 → 두 공통 GHCR 게시 | [2026-10-15 실제 게시 성공](https://github.com/t-hajongkim/Medical-ML-SDLC/actions/runs/38722808263). 두 패키지 Public·익명 조회 확인 |
| [ci.yml](.github/workflows/ci.yml) / **Check workshop setup** | 현재 자산 계약과 이전 코드의 합성 fixture 회귀 검사 | 외부 자산 다운로드·Docker·GHCR 게시 없음 |
| [prepare-eda-pr.yml](.github/workflows/prepare-eda-pr.yml) / **Prepare research review** | Issue 권한·폼 확인 → 정확한 Issue 또는 기존 PR 대상 전달. 데이터 추출·준비 PR 생성 없음 | Issue opened 또는 유지관리자의 수동 실행 |
| [research-plan.md](.github/workflows/research-plan.md) / **연구 점검·평가계획** | 입력 준비·기본 EDA → AI 추가 선정·실행 → 최종 지표 추천 → 최초 PR 생성 또는 같은 PR 갱신 | `workflow_run` 또는 수동 PR 번호. 읽기 전용 AI와 native 게시 작업 분리 |
| [review-eval-plan.yml](.github/workflows/review-eval-plan.yml) / **Review evaluation plan feedback** | PR의 자연어 피드백을 권한 확인 후 AI에 전달 | 허용 블록·매개변수는 Python이 검증. Merge hook·별도 승인 JSON·명령 전용 파서 없음 |

운영자는 자산 게시 YML을 검토합니다. 참가자 개인 패키지·초기화·Pages는 이번 범위가 아닙니다.
고정 모델 평가와 PR2는 코드 구현·합성 검증·실제 원격 실행 성공을 구분합니다.

자동 추천은 사수님 참고 저장소의 후속 검증과 같은 **`workflow_run` 완료 이벤트**로 연결합니다.
토큰으로 새 `workflow_dispatch`를 호출하지 않아 호출자를 봇으로 바꾸지 않습니다.
사람의 기본 admin/maintainer/write 역할 검사와 같은 저장소의 생성 PR·허용 파일 검증을 유지합니다.
`on.bots` 예외·`roles: all`·추가 PAT·조직 권한 확대는 추가하지 않습니다.
추천 실행이 success여도 agent가 skipped라면 실제 AI 추천이 아닙니다.
계획의 `ai_status: provided`와 `ai-recommendations.json` 반영 여부를 확인합니다.
전달 artifact의 schema 2는 대상 종류(Issue/PR)·번호·실행 ID/회차·commit·요청 여부만 담습니다. 원자료·환자 행은 담지 않습니다.
최신 PR을 추측하지 않고 해당 실행이 지정한 PR만 처리합니다. 요청하지 않은 재실행·Merge 후에는 AI를 시작하지 않습니다.
수동 게시에는 `contents: read`, `packages: write`만 사용하고 이미지 이름은 `github.repository`에서 생성합니다.
새 저장소에서도 경로·소유자 이름을 하드코딩할 필요가 없습니다.

### 게시 YML의 파트별 입출력

`version` 입력은 이미지 태그입니다. 데이터 내용과 모델 파일은 고정 자산 명세로 결정하며, 태그를 바꿔도 재학습하거나 재분할하지 않습니다.

| 파트 | 입력 | 처리와 출력 | 실패 시 |
|---|---|---|---|
| 기본 계약 | 합성 fixture·저장소 이름·이미지 태그 | 현재 계약 검사 후 데이터/모델 이미지 이름 생성 | 계약·이름·태그 오류로 중단 |
| 자산 준비 | 고정 URL·revision·bytes·SHA·변수 순서 | 준비 분할·메타데이터·원본 키·checkpoint를 확인하고 두 빌드 컨텍스트 생성 | 파일 무결성·행수·스키마·키 연결 불일치로 중단 |
| 이미지 검사 | 데이터/모델 컨텍스트 | DB 전체 적재 결과와 모델 파일·명세를 검사. 모델은 실행하지 않음 | 두 검사 중 하나라도 실패하면 게시 전 중단 |
| GHCR 게시 | 검증된 두 이미지·GitHub token | 별도 공통 데이터/모델 패키지에 push하고 각각 digest 확인 | 인증·push·digest 해석 오류로 실패. 한쪽만 게시됐을 가능성 확인 필요 |
| 결과 안내 | 실제 두 digest·준비 결과 | Summary에 고정 참조·행수·알려진 한계 표시 | 앞 단계가 실패하면 게시 완료 Summary를 생성하지 않음 |

```text
합성 계약 검사 → 고정 자산 다운로드·검증
→ DB 이미지 빌드·전체 행 검사
→ 모델 파일 이미지 빌드·무결성 검사
→ 두 이미지 게시 → 실제 digest와 한계 안내
```

새 학습·checkpoint 로딩·추론·추가 scaler 적용은 이 흐름에 없습니다.
이미지 참조의 설정 파일 반영과 최초 Public 전환은 자동으로 처리하지 않습니다.

## 게시할 자산

### 데이터 이미지: 공통 `-db`

- 공개 HF [`auphong2707/hospital-readmission-risk-data`](https://huggingface.co/datasets/auphong2707/hospital-readmission-risk-data)의
  revision `4a61a3591f079628c72f5edbc7494ca7dda8741d`, **`splits/` 아래 파일만** 사용합니다.
- train **71,236**, validation **15,265**, test **15,265**행. 각 파일은 이미 표준화된 **113개 숫자 입력 + target**입니다.
- 각 분할의 demographics sidecar를 고정 UCI 원본의 `encounter_id`에 연결해 target·행 순서·성별·나이·인종을 검사하고
  `patient_nbr` 연결을 복원합니다. 키는 모델 입력에 넣지 않으며 개별 행·식별자는 로그·Issue에 출력하지 않습니다.
- 원본은 SHA256 `f792c388d9b470aac4fbf21c39b176534d55a116e7626881c30bbe7ab0341422`로 검증합니다.
  원본 CSV는 키 복원에만 사용한 뒤 빌드 컨텍스트에서 제외합니다. 삭제된 원본을 Git에 복원하지 않습니다.
- 원본 feature/target CSV와 sidecar의 바이트를 그대로 보존합니다. 다시 전처리하거나 분할하지 않습니다.
- PostgreSQL은 `prepared.metadata`, `prepared.features`, `prepared.asset_info`를 제공합니다.
  `features`는 순서가 보존된 JSONB 숫자 배열과 분할 내 `row_position`을 보관합니다.
  `metadata`의 `race_original`·`race_was_missing`으로 원본 결측과 공개 sidecar의 대치값을 구분합니다.

### 모델 이미지: 별도 공통 `-model`

- [`auphong2707/hospital-readmission-phase2-lr`](https://huggingface.co/auphong2707/hospital-readmission-phase2-lr)의
  revision `e1f7fcc0f4f8e273fc56eb35018ec70bbccbee61`, `logistic_regression.pkl`만 등록합니다.
- 크기 **4,018 bytes**, SHA256 `f923be3373fbd958df6f1d9a0694fc4a36c6727f6c48090637be84ed56e9e823`.
- 이미 준비된 113-feature CSV 입력의 LogisticRegression, classes `[0, 1]`, positive `readmitted <30`입니다.
  target은 `<30`이면 1, `>30`·`NO`이면 0입니다.
- 별도 로컬 수동 추론에서 전체 test 지표를 재현했지만, 저장 환경 sklearn **1.2.2**와 검증 환경 **1.6.1** 사이 경고가 있었습니다.
  **준비된 CSV 실습용 조건부 등록**이지 무조건 호환·독립 임상성능 인증이 아닙니다.
- `FROM scratch`의 **파일 보관 이미지**입니다. checkpoint·명세·MIT source README·출처만 담으며 Python 런타임이나
  실행 가능한 추론 서비스가 없습니다. 게시 과정의 모델 검사는 파일 존재·해시·데이터 입력 명세 일치 검사입니다.
- 기존 **120-feature scaler는 113 입력과 불일치하므로 다운로드·적용·포장하지 않습니다.**
  원본 입력의 113-feature 전처리 파이프라인 재현은 미해결입니다.

데이터와 모델은 별도 이미지로 저장되므로 참가자 DATA 복사에 모델이 딸려가지 않습니다.
두 digest는 게시 후 Summary에 표시하며 검증된 실제 참조를 `config/assets.json`에 고정 등록했습니다.
2026-10-15 [게시 run 38722808263](https://github.com/t-hajongkim/Medical-ML-SDLC/actions/runs/38722808263)에서
metadata·feature 각 101,766행 및 asset_info 전체 대조, 모델 이미지 파일 해시 검사가 통과했습니다.
두 패키지는 Public이며 익명 OCI manifest 조회와 모델 파일 해시도 확인했습니다.
이미지의 실제 빌드 소스는 `5c0fc144687766c0306e8b7150f3228579ac7a0f`입니다. 이후 참조 기록 커밋과 구분합니다.
게시 YML이 설정 파일을 자동 커밋하지는 않습니다. 최초 Public 전환이 필요한 경우 운영자가 처리합니다.
두 번의 push는 원자적이지 않으므로 향후 게시에서도 두 게시가 모두 성공했는지 확인해야 합니다.

| 등록된 공통 이미지 | 고정 참조 |
|---|---|
| 데이터 | `ghcr.io/t-hajongkim/medical-ml-sdlc-db@sha256:a81f2187eaac6b465b3443df423ee369ab7e7cd216e71ab9c0a4aeb8a88ab947` |
| 모델 파일 | `ghcr.io/t-hajongkim/medical-ml-sdlc-model@sha256:1617d5200111f0529d29d4a59517177dc6eb6cc5bf4a5ba88408c1cd83f8ee38` |

**자산 등록 완료와 참가자 초기화·자동 점검 연결 완료는 다릅니다.**

## 보존해야 할 한계

| 항목 | 확인된 사실 / 해석 경계 |
|---|---|
| 환자 중복 | train–test **5,086**, train–validation **5,055**, validation–test **1,760** |
| validation | 공개 validation은 모델 개발에 사용됐으므로 독립 검증셋이 아닙니다 |
| 인종 결측 | 원본 결측 train **1,596**, validation **336**, test **341**개. 공개 meta는 Caucasian 대치값이므로 원본 결측 플래그를 함께 보존합니다 |
| 전처리 이력 | source의 split 전 target encoding과 과거 113-feature 변환 이력의 불확실성이 있습니다 |
| 성능 해석 | 누수 없는 독립 holdout·원본 입력 추론·임상적 유효성 검증을 주장하지 않습니다 |

환자 중복을 숨기기 위해 재분할하지 않습니다. DB init은 중복 0을 요구하는 대신 **기록된 실제 중복 집계와 일치**하는지 검사합니다.

## 필요한 파일과 역할

| 파일 | 역할 |
|---|---|
| `.github/workflows/publish-db-image.yml` | 운영자 수동 자산 게시 YML |
| `app/prepared.py` | 표준 라이브러리만으로 고정 다운로드·해시/행/스키마/키 연결·컨텍스트 생성·runner 검증 |
| `config/prepared-source.json` | GHCR 이미지 제작·재게시용 원본 URL·revision·bytes·SHA·변수 순서·target·모델 명세. 일반 연구 실행의 입력 기준이 아님 |
| `data/source_manifest.json`, `data/NOTICE.txt` | UCI 원본 고정 명세·출처·라이선스·변경 및 한계 기록 |
| `db/Dockerfile`, `db/init.sql`, `db/healthcheck.sh` | 데이터 전용 PostgreSQL 이미지·적재 계약·준비 상태 검사 |
| `app/Dockerfile` | 재사용·정리한 모델 **artifact** 이미지 정의. 추론 실행 이미지가 아님 |
| `.dockerignore`, `.gitignore`, `.gitattributes` | 컨텍스트 허용 목록·CSV/바이너리/캐시 커밋 방지·줄바꿈 |
| `app/asset_contract.py`, `app/dataset.py`, `app/ghcr.py` | 재사용한 hash/원본 검증/이미지 이름·태그·digest 도우미 |
| `tests/test_prepared_source.py` | 신규 현재 자산 계약·불일치 fail-fast·무학습/무추론·runner 모의 검사 |
| `config/assets.json` | 실제 게시·Public 확인한 공통 data/model의 고정 digest 참조 |
| `README.md`, `docs/architecture.md`, `docs/adr.md` | 현재 사용법·범위, 구조, 결정 이유를 분리해 관리 |

컨텍스트는 runner 임시 경로의 `data/`와 `model/`에 생성합니다.
`db/Dockerfile`과 `app/Dockerfile`의 COPY는 **각 생성 컨텍스트 기준**입니다.
저장소 루트나 `db/`, `app/` 자체를 그대로 빌드 컨텍스트로 넘기지 않습니다.
원자료·CSV·모델 바이너리를 Git, Actions Artifacts, Releases에 올리지 않습니다. 외부 DB 포트 노출을 추가하지 않습니다.

이전 원본 재전처리·새 학습·pair 초기화/추론 코드와 해당 테스트·중단 전용 초기화 workflow는 제거했습니다.
이미 전처리·분할된 자산을 읽는 현재 경로만 유지하며, 새 `.fit()` 경로를 추가하지 않습니다.
현재 runner의 DB 전체 대조와 모델 파일 검사는 `app.prepared smoke` / `verify-model-image`로 분리했습니다.
참가자 데이터 초기화·모델 자동 실행이 필요하면 현재 준비 데이터/모델 artifact 계약을 기준으로 별도 구현해야 합니다.

## 2단계: 독립 EDA 블록 준비

`app.blocks.catalog.run_checks(identifiers, context)`는 각 독립 파일의
`run(context: dict) -> {"block", "status", "details"}`를 호출합니다.
`app.executor_eda`는 `app.prepared.validate_data_manifest`를 재사용해 `data/assets`를 확인하고
metadata를 한 번 읽으며, `db_features.csv`의 113개 숫자 배열은 **한 행씩 스트리밍**합니다.
선택한 고정 raw CSV는 기존 `validate_dataset`로 검사한 뒤 선정조건·라벨 연결 **집계만** 만듭니다.
모델 이미지·checkpoint·추론 모듈은 읽거나 실행하지 않습니다.

| 블록 | 현재 확인 범위 | 실제 101,766행 결과 |
|---|---|---|
| `patient_overlap` (보강) | 분할별 고유 환자·입원 수, 환자 집합의 쌍별 교집합 | `failed`: 5,055 / 5,086 / 1,760 중복 |
| `demographics` (보강) | 인종·성별·연령구간별 입원/환자 수, target 0/1·비율, 원본 인종 결측/공개 대치 구분 | `completed`: 기술 집계 완료 |
| `schema` (보강) | 특성 목록/순서/중복, 행 키/순서/벡터 길이, 타입·결측·finite·min/max·상수 | `unverifiable`: 수치 계약은 `passed`, 임상 의미·단위는 확인 불가 |
| `cohort` (신규) | 입원/환자 단위·고유 키·사건 수, 출처 선정 설명과 실제 raw 조건검사 분리 | `unverifiable`: 연구 단위/추가 조건/선정 전 모집단·제외 수 미제공 |
| `prediction_timing` (신규) | 승인된 예측시점과 근거 있는 변수 가용시점 대조 | `unverifiable`: 승인된 예측시점·timestamp/113 변수 가용시점 사전 없음 |
| `label_observability` (신규) | `<30`=1, `>30`/`NO`=0 원본 연결과 30일 추적/외부병원 포착을 분리 | `unverifiable`: 인코딩 일치, 추적 완전성은 확인 불가 |
| `preprocessing_lineage` (신규) | hash 무결성과 과거 대치/스케일링/target encoding fit 범위를 분리 | `unverifiable`: 113-feature 파이프라인 미재현·split 전 encoding 우려·validation 개발 사용 |

분할 출력은 `train/validation/test`입니다. 이전 `valid` 입력은 명시적 alias로 기록하고 정규화합니다.
옛 overlap 소비자의 회귀를 위해 **alias 입력일 때만** `train_valid/valid_test` 조회 키를 deprecated로 병기합니다.
다른 split은 실패하며, target은 정확한 0/1, 결측 플래그는 boolean 또는 정확한 `"true"/"false"`만 받습니다.
인종 분포는 공개 `race`와 관측 `race_original`을 별도로 집계하므로 Caucasian 대치를 실제 관측으로 숨기지 않습니다.
사건 비율의 분모는 입원 수입니다. 환자는 반복 입원·양쪽 target에 기여할 수 있으며 부분집단 환자 수는 합산하지 않습니다.
자료 없는 집단·0행은 통과하지 않고, 임의 표본수/유의성 기준이나 임상 범위 판정을 만들지 않습니다.

실제 분할의 target 1 사건 수는 train **7,950**, validation **1,703**, test **1,704**입니다.
전체 고유 환자는 **71,518**명, 고유 입원 키는 **101,766**개입니다.
113개 수치 특성의 결측·비수치·nonfinite·행/길이 불일치는 없고 **8개 상수 특성**이 관측됐습니다.
raw의 입원일수 1–14일·검사 수 양수·투약 수 양수 조건은 각각 101,766행에서 확인했습니다.
입원/당뇨 선정에 관한 출처 설명은 독립 임상 판정이 아니며, 진단 3개 필드의 250-code proxy는 인증으로 쓰지 않습니다.
선정 전 모집단·제외 수를 0으로 만들거나, 초회 입원만 선택하거나, 환자/의심 특성을 삭제하지 않습니다.

상태의 의미:
- `passed`: 명시한 검사 범위의 근거와 조건이 모두 충족됨.
- `failed`: 측정 위반/잘못된 입력 확인. 보고서 생성 실패와는 별개.
- `unverifiable`: 필요한 자료/근거 부족. 확인된 하위 집계는 함께 보존.
- `completed`: 기술 집계 완료. 연구 적합성·임상 타당성 통과가 아님.

집계 JSON은 `report_generated: true`와 별도로 `assessment_status: failed`를 기록합니다.
현재 환자 중복 때문에 failed이며, 확인 불가 하위 항목을 전체 passed로 바꾸지 않습니다.
개별 키·행·feature vector는 보고서에 출력하지 않고 자유문구는 실행하거나 집계 결과에 그대로 재출력하지 않습니다.

검증된 자산 디렉터리에 대한 로컬 실행 예:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
python -B -m app.executor_eda --assets 'C:\workshop-assets\data\assets' `
  --raw-csv 'C:\workshop-assets\diabetes130_raw.csv' `
  --output 'C:\workshop-results\eda-aggregate.json'
python -B -m unittest discover -s tests -p 'test_eda_blocks.py' -v
```

raw 입력은 선택 사항이며 없으면 원본 조건/라벨 연결은 확인 불가입니다.
자산 context가 필요하면 기존 `prepare_data(...)`만 직접 재사용합니다.
`app.prepared prepare` CLI는 모델 포장도 호출하므로 EDA 준비에 사용하지 않습니다.
구형 pair 초기화/학습 소비자는 제거했습니다. 임상/원본 인종 근거 부족 상태는 현재 EDA에서도 그대로 남깁니다.

실데이터 집계, 합성 단위·통합 테스트와 원격 이벤트 실행은 서로 다른 근거입니다.
이 연결 작업에서는 참가자 초기화·모델 load/fit/predict·재전처리·재분할·자산 재게시를 하지 않습니다.
자산 게시 외 실제 Issue→Copilot→승인 이벤트 흐름은 실습 저장소에서 별도로 확인해야 합니다.

### Issue → 데이터 점검 → 연구검토 draft PR

`.github/workflows/prepare-eda-pr.yml`과 `app/research.py`가 연결을 담당합니다.

```text
Issue opened 또는 유지관리자의 workflow_dispatch(issue_number)
 → 신뢰/권한 게이트 + 현재 5필드 폼 검증 (본문은 데이터)
 → Issue 대상 메타데이터 보관 → 선행 workflow 완료 이벤트
 → 신뢰한 AI job에서 고정 DATA 추출·입력 검증 (DB 서비스 기동 없음)
 → 미확정 매핑만 AI 추천 → Python 검증
 → Python 기본 EDA(schema/patient_overlap/demographics/cohort)
 → Copilot: 공개 목적·기본 집계·미사용 블록으로 추가 점검 선정
 → plan-eda 검증 → execute-eda: 기존 결과 재사용·필요한 추가 점검만 계산
 → Copilot: 최종 EDA 기반 평가 지표 추천 (이 단계에서 EDA 추가 불가)
 → Python 최종 계획 조합 → native create_pull_request로 최초 draft PR1 생성
 → 사람의 댓글 → 기존 PR 대상으로 AI 작업 → 필요한 분석만 수행 → 같은 PR 갱신
 → 해당 실행에서 생성한 runtime context만 정리
```

이는 **이미지에서 검증된 준비파일 읽기**이며 실제 PostgreSQL 기동/DB 질의가 아닙니다.
모델 이미지를 pull/load하지 않고 raw 원본도 추가 다운로드하지 않습니다.
따라서 이미지에 없는 원본 행별 선정/라벨 추적 근거는 확인 불가로 남습니다.
원본 고정 hash·준비 파일 무결성, 원본 직접 관측 검증, 연구 적합성 판정은 서로 다릅니다.
기존 로컬 raw 검증 집계의 재사용 dry-run은 더 풍부한 원본 집계를 가질 수 있으나 원격 실행으로 표시하지 않습니다.

자동 제출은 GitHub의 `OWNER`/`COLLABORATOR` 작성자만 허용합니다.
외부 작성자·조직 `MEMBER`만으로는 자동 쓰기를 허용하지 않고 Actions Summary에 유지관리자 수동 실행 필요를 남깁니다.
수동 실행자는 API로 확인한 write/maintain/admin 권한이 필요합니다.
다른 Issue는 대상 아님으로 처리하고, 5개 section의 누락/중복/순서 손상·미체크 동의는 실패합니다.
목표·질문 값은 필수, 대상조건·추가요청 값은 비워도 됩니다. Issue 자유문구를 shell/Python/SQL로 실행하지 않습니다.
제출 title/body를 YML run 문자열에 직접 삽입하지 않으며 토큰·개별 환자 정보를 로그로 출력하지 않습니다.
동의한 공개 연구 텍스트 4필드와 허가된 미반영 댓글은 좁은 MCP 입력으로 Copilot에 제공합니다.
원본 환자 행·식별자 값·환경·인증정보는 제공하지 않으며 명백한 민감정보 의심 텍스트는 오류로 중단합니다.

PR의 허용된 보고서 디렉터리에는 다음 파일을 관리합니다:

| 경로 (`research/reports/issue-{번호}/`) | 내용 |
|---|---|
| `request.json` | 현재 5필드와 기본 제목·Issue/저장소 정보. 자유문구는 요청사항으로만 보존 |
| `eda.json` | 선택된 블록의 집계·상태·근거·한계와 data digest/data_id/revision/code commit/실행 링크 |
| `report.md` | 분할·사건·환자 중복·인구집단 표, 원본 결측/대치 구분, 연구 해석 경계 |
| `eda-plan.json`, `evaluation-plan.json` | 서로 다른 점검/결과 지표 조합·선택 출처·필수 입력·차단 근거 |
| `selection.json` | 연구별 추가·제외·주지표·threshold·매개변수. 기존 선택 보존; 권한 확인한 자연어 요청만 검증 후 변경. 초기 AI는 목적 힌트만 합침 |
| `recommendation-request.json` | 자유서술·개별 행을 제외한 공개 집계·등록 카탈로그·허용 evidence·context hash |
| `ai-recommendations.json` | 실제 Copilot이 제출하고 신뢰 코드가 검증한 추천. AI 대기를 빈 성공 파일로 대체하지 않음 |
| `input-schema.json` | 환자 행 없는 등록 스키마·의미 후보·타입·결측 집계·공개 코드북·context hash |
| `input-mapping.json` | 검증한 역할 매핑·원래 추천·profile/source 명세. 실제 EDA 입력과 hash로 연결 |
| `execution-plan.json` | 시작 입력 서명·데이터/모델 참조·코드·정책/카탈로그 hash와 실행할 등록 Tool·입력 참조. 원본 행·벡터 없음 |
| `coordination-plan.json` | AI 조율 이유·목적/선택/스키마/피드백 hash·최초 실행 EDA 근거·실행 코드. 연구/댓글 원문은 복제하지 않음 |
| `feedback-requests.json`, `feedback-applied.json` | 자연어 댓글 ID·작성자·본문 hash와 누적 반영 ID. 원문은 GitHub 댓글 API에서 재조회 |

최초 `primary_metric`/`threshold`는 미확정이며 사람이 선택합니다. 지표 값·임계값 최적화·새 예측 결과는 계산하지 않습니다.
hash/행/스키마/필수 metadata 파손·네트워크/GH/git 명령 오류는 중단합니다.
반면 준비파일 계약이 유효하면 환자 중복 `failed`·관측 근거 `unverifiable`를 **그대로 드러낸 draft PR**을 준비합니다.
원자료·CSV·pkl·행별 predictions·환자 키는 PR/Artifacts에 보관하지 않습니다.

재실행 정책:
- 같은 Issue는 concurrency로 직렬화하고 고정 branch와 열린 PR을 갱신합니다. 새 중복 PR을 만들지 않습니다.
- 생성파일 외 사용자 변경과 PR 제목/본문/댓글을 보존하며 force push·amend·자동 merge를 하지 않습니다.
- 닫힌/병합된 검토 PR 또는 닫힌 Issue는 자동 보류합니다. 재생성/재오픈하지 않습니다.
- 내용·코드·자산이 같으면 기존 실행 근거를 보존하고 불필요한 commit/push를 하지 않습니다.
  최신 실행 근거는 Actions Summary에서 확인합니다.
- 이전 push 후 PR 생성만 실패했다면 생성 branch의 Issue 소유 marker를 확인해 재시도합니다.
  소유 marker 없는 동일 이름 branch는 덮어쓰지 않습니다. 사용자 동시 push로 non-fast-forward가 나면 명시적으로 실패합니다.

원격 활성화 전제:
- 준비 워크플로는 contents/PR/issues read만 사용합니다. 댓글 큐는 contents write·PR/issues read를 사용합니다.
  최초 생성과 후속 갱신은 native safe-output 작업의 제한된 쓰기 권한을 사용합니다.
  토큰 기반 재호출을 제거해 `actions: write`는 필요하지 않습니다.
  대상 해석과 이전 게시 결과 재사용 검증에는 기존 artifact 조회를 위한 `actions: read`를 사용합니다.
  추천 엔진은 별도의 `copilot-requests: write`를 사용합니다.
  packages write/PAT/새 Secret은 없습니다.
- [참가자 주의사항](#참가자-주의사항)의 PR 생성 허용 설정을 확인해야 합니다.
  조직 정책·branch 규칙도 해당 쓰기를 허용해야 합니다. 템플릿 설정은 복사본에 자동 상속되지 않습니다.
- GITHUB_TOKEN이 만든 PR/push는 다른 CI를 자동 trigger하지 않을 수 있습니다.
  합성 회귀는 코드 변경 시 `ci.yml`에서 실행합니다. Issue·댓글 작업은 실제 요청의 권한·입력·허용 블록 검증을 수행합니다.
  별도 PR CI나 사람의 코드/연구 리뷰를 통과했다고 주장하지 않습니다.
- 저장소명/default branch는 동적입니다. 독립 복사본은 원본 업데이트를 자동 상속하지 않으므로
  원본과 실습 복사본 **각각**에 승인된 코드를 반영해야 합니다. 기존 Issue는 승인 후 수동 dispatch로 처리할 수 있습니다.

로컬 검증은 `python -B -m unittest discover -s tests -p 'test_research_workflow.py' -v`이며 외부 명령을 모의합니다.
`python -B -m app.research dry-run --issue-json ... --aggregate-json ... --repository owner/repo --output ... --code-commit ... --run-url ...`
은 기존 실제 집계를 사용해 집계·계획·선택 파일을 로컬 생성할 뿐 GH/Docker/git 쓰기를 실행하지 않습니다.
dry-run의 기준 commit은 미커밋 변경이 포함된 로컬 base이며 모의 실행링크는 실제 Action으로 표시하지 않습니다.

추천 시작 경로는 `app/recommendation_events.py`가 검증합니다. 같은 저장소·기본 브랜치의 등록된 선행 실행과
run ID·회차·source commit이 일치하는 전달 파일 하나만 허용합니다.
전달 파일이 없거나 만료되었으면 선행 준비·검토 워크플로를 업데이트·재실행하거나 추천의 수동 PR 번호 입력을 사용합니다.
워크플로 이름이 연결 계약이므로 이름을 바꾸면 `research-plan.md`의 `workflow_run.workflows`도 함께 변경합니다.
컴파일러의 정적 branch-filter 경고는 기본 브랜치 이름을 고정하지 않아 남습니다.
대신 읽기 전용 대상 해석 job이 실제 기본 브랜치·동일 저장소·등록된 경로를 확인한 뒤에만 agent를 허용합니다.

## 3단계: 댓글 선택과 사람의 승인

기본 선택은 `config/planning-policy.json`, 사용 가능한 블록은 `eda-catalog.json`과 `metric-catalog.json`에서 읽습니다.
AI는 공개 목적·질문·조건·추가 요청과 실제 EDA를 받아 등록 ID·이유·근거를 추천합니다.
초기 AI가 주지표·threshold를 임의 확정하지 않습니다. 사람이 댓글로 명시한 선택은 검증 후 반영하며 Merge 승인은 별개입니다.
필수 EDA `schema`와 `patient_overlap`은 제외할 수 없습니다.

**policy = 공통 규칙**, **selection = 해당 연구의 선택**입니다.

| 구분 | 현재 값 / 적용 |
|---|---|
| policy 필수 EDA | `schema`, `patient_overlap` — 제외 불가 |
| policy 기본 EDA | 위 2개 + `demographics`, `cohort` |
| policy 기본 평가 | `roc_auc`, `average_precision`, `brier_score`, `calibration_curve` |
| 조합 | 필수 보호 + 기본 + 실제 AI 추천 + 사용자 추가 − 사용자 제외 |
| selection | 연구별 add/exclude/objectives/primary/threshold/parameters. 다른 연구의 전역 기본을 바꾸지 않음 |

PR의 **Conversation에 자연어 피드백**을 작성할 수 있습니다.
예를 들어 “F1을 추가하고 AP를 주지표로, threshold를 0.3으로 설정해 주세요”는 **사용법 예시**이며 자동 적용된 실제 결과가 아닙니다.
write/maintain/admin 사람의 댓글만 원본 API·작성자·본문 hash를 확인하고 AI에 전달합니다.
AI는 등록 ID/형식의 조율안을 제안하고 Python이 검증한 뒤 같은 PR의 선택·계획·보고서를 갱신합니다.
AI가 변경 내용과 남은 선택·Merge 승인 대기를 댓글로 안내하며, 임의 코드/SQL 실행·자동 승인·모델 실행은 하지 않습니다.

명령 문법을 외우지 않고 원하는 추가·제외·주지표·임계값을 자유롭게 작성합니다.
전용 명령 파서는 없으며 Python은 자연어의 뜻을 해석하거나 댓글을 코드로 실행하지 않습니다.
write/maintain/admin 권한이 있는 사람의 요청만 AI에 전달합니다.
처리된 댓글의 편집은 재적용하지 않으므로 새 댓글로 변경합니다.
여러 댓글은 ID 순서로 처리하고 중복 적용을 막습니다. 미반영 자연어 댓글이 편집되면 새 hash로 다시 준비합니다.
AI 준비 이후 본문/권한/PR/연구 목적이 바뀌면 기존 조율안을 게시하지 않습니다.
댓글 전달 결과는 Actions Summary, AI가 반영한 요청은 `feedback-applied.json`으로 확인합니다.

보고서에서 **승인 준비 완료**를 확인합니다: 양쪽 AI 상태 `provided`, 명시적인 주지표, 선택한 평가 항목에 `blocked` 없음.
이후 사람이 **Ready for review → Merge**합니다. 자동 승인·병합은 없습니다.
**Merge된 계획과 해당 commit이 승인 기준**입니다. 별도 승인 JSON이나 Merge 후 승인 규약 검사 작업은 없습니다.
준비 상태는 검토 안내이며 GitHub의 Merge 버튼을 별도 코드로 차단하지 않습니다.
후속 실행을 연결할 때에도 입력·블록·매개변수의 실행 계약은 검증해야 합니다.

승인 범위는 **교육용 계획 확정**입니다. 환자 중복·validation 개발 재사용·확인 불가 이력을 보존하며,
모델 실행·지표 계산·누수 없는 독립 임상성능·임상 사용 승인을 뜻하지 않습니다.

## 입력 프로필과 앞단 매핑

입력 계약의 기준은 선택한 GHCR 이미지 안의 `data_manifest.json`이며, 읽기·검증·표준화는 `app/input_contracts.py`에서 수행합니다.
중복된 저장소 등록 목록 `config/input-profiles.json`은 제거했습니다.
현재 `prepared-data-only` v2는 기존 manifest와 해당 형식의 정해진 9개 컬럼 역할을 그대로 읽으므로 이미지 재발행이나 새 메타데이터 필드가 필요하지 않습니다.
확정 매핑은 재사용합니다. 기본 EDA를 먼저 실행하며, 매핑이 미확정인 경우만 의미 근거 확인을 선행합니다.

```text
Issue → 권한 확인·Issue 대상 전달 → workflow_run
  → 고정 이미지 무결성·스키마 확인 (아직 PR 없음)
  → bindings 있음: 검증된 이미지 입력 계약 재사용
  → bindings null: AI 스키마 매핑 추천
      → Python이 등록 컬럼·의미 후보·필수 역할·근거·hash 검증
  → 기본 EDA → AI가 기본 결과를 보고 미사용 점검 선정
  → Python 계획 검증·추가 점검 실행 → 최종 EDA 기반 지표 추천
  → 결과·추천을 담은 최초 PR1 → 자유 댓글 → 같은 PR 갱신·사람 Merge
```

앞단 AI에는 공개 연구 텍스트 4필드와 등록 컬럼명·타입·설명·의미 후보·결측 집계·정답 정의·공개 코드북을 제공합니다.
환자 행·환자 키 값·원자료·인증정보는 제공하지 않습니다.
AI는 `bindings`, 역할별 한국어 `reasons`, 허용 `evidence`, 스키마 `context_hash`를 돌려줍니다.
컬럼이 없거나 환자 키의 의미 근거가 없으면 검증 실패로 멈추며 입원 ID를 환자 ID로 추정하지 않습니다.
AI가 추천했다고 임의 코드·SQL·변환식을 실행하지 않습니다.

후단 AI는 공개 목적·현재 선택·최종 EDA 집계·카탈로그로 평가 지표를 추천합니다.
완료한 EDA 선택을 그대로 보존하며 여기서 새로운 EDA를 계산하지 않습니다.
댓글이 지표 선택만 바꾸면 이전의 검증된 EDA를 재사용합니다. EDA 추가 요청은 Plan/Executor에서 새 블록만 계산합니다.
이전 PR 집계는 성공한 동일 코드의 native 실행·기존 게시 artifact·불변 Git blob 출처까지 일치할 때만 재사용합니다.
조작된 집계는 hash를 다시 계산해도 거부하며, 게시 증거가 없거나 만료됐으면 재사용하지 않고 새 계산임을 명시합니다.
같은 runner job의 `PLANNING_CONTEXT.json`은 행 없는 계획 문맥이며 게시 단계도 이 작은 파일만 읽습니다.
인증된 이전 결과가 있으면 댓글 조율 전에 GHCR을 추출하지 않습니다. 새 EDA가 실제 필요할 때만 자산을 한 번 추출하고 입력을 검증합니다.
전체 행을 담는 `VALIDATED_CONTEXT.json`은 실제 계산용 비공개 캐시이며 Git/Artifacts/AI에 전달하지 않습니다.
새 계산 전에는 현재 파일 해시와 캐시 문맥을 확인하고, 계산 없이 집계를 재사용할 때는 원래 계획·입력 서명·결과 hash를 확인합니다.
게시 증거 artifact는 7일 보관하며, 증거가 만료되면 전체 준비·검증 경로로 명시적으로 돌아갑니다.
도구는 준비 시 비공개 runner 경로에 복사한 `research_tools.py`를 호출합니다. Python 안에서 다른 Python을 실행하지 않으며,
공개 문맥 파일 두 개는 `cat`으로 읽습니다. 컨테이너는 `create/cp/rm`으로 파일만 추출하고 DB를 `start/run`하지 않습니다.
게시 자료 검사는 `safe-outputs.steps`에서 실제 push/PR 생성 전에 한 번 실행합니다.
원본 본문의 receipt hash는 그대로 확인하고, native 선언의 본문은 같은 고정 gh-aw `sanitizeContent` 함수로 정규화한 기대 본문과 비교합니다.
따라서 `&lt;30`이 `<30`으로 바뀐 정상 변환은 허용하지만 실제 내용·제목·브랜치·대상 변경은 거부합니다.
잘못된 게시 자료는 쓰기 전에 멈추며 `agent`의 모든 실패를 일괄 차단하는 별도 승인 플래그나 새 job은 추가하지 않습니다.
구형 직접 게시 함수·미실행 준비 PR 생성기·사용하지 않는 매핑 읽기와 `run-data-checks` 호환 CLI는 제거했습니다.
이 공개 텍스트/전체 EDA는 runner의 transient `RESEARCH_CONTEXT.json`·`AI_REQUEST.json`으로 전달합니다.
커밋하는 `recommendation-request.json`은 원문을 제외한 구조화 요청이며 조율 hash로 결합합니다.
두 단계의 context hash는 다르며 입력 매핑 hash를 후단 요청에도 묶어 오래된 추천을 막습니다.
`input-schema.json`은 공개 요청, `input-mapping.json`은 검증된 바인딩·원래 추천·프로필·출처 명세입니다.
입력 검증과 EDA 실행 시 선택한 GHCR digest의 실제 파일·manifest·매핑을 대조합니다.
Merge 후 같은 자료를 다시 읽어 별도 승인 스냅샷을 만드는 중복 경로는 없습니다.

이미지 입력 계약의 `bindings`는 표준 역할→원본 컬럼, `split_values`는 원본 분할값→train/validation/test,
`target_values`는 원본 정답값→0/1입니다. 현재 split·정답 인코딩은 변환식을 생성하지 않고 등록된 값만 사용합니다.
새 대응 모델의 확률 출력 계획은 운영자가 `probability_output_planned: true`로 명시합니다.
미선언 시 후단 평가 계획의 확률 입력을 존재한다고 가정하지 않습니다. 실제 모델 호환성 검증은 4단계의 별도 범위입니다.

### 지원 범위와 경계

기존 Diabetes 이미지는 원래 `prepared-data-only` v2 무결성 검증을 그대로 사용합니다.
다른 등록 입력은 `registered-prepared-input` v1 계약을 사용합니다:
`metadata.csv`, `db_features.csv`, `data_manifest.json` 세 파일과 등록된 SHA256·data_id·변수 순서·분할 행수를 검증합니다.
이 형식은 manifest 안의 `input_metadata`에 profile_id·컬럼 의미/역할·bindings·분할/정답 매핑·정답 정의·출처·한계를 명시해야 합니다.
현재 v2 이미지에 같은 내용을 추가로 복제하라는 요구가 아니라, 알려진 역할 계약이 없는 다른 컬럼 구조에 필요한 명시적 의미 사전입니다.
data_id는 이 메타데이터를 포함하고 data_id 필드만 제외한 정규화 JSON hash입니다. 이미지와 계획/매핑의 식별자가 일치해야 합니다.
특성 테이블은 `row_key,split,row_position,feature_values` 형식의 준비된 유한 숫자 배열입니다.
식별자·분할·정답 역할의 원본 컬럼이나 표준 역할명을 입력 특성에 포함하면 거부합니다. 자동으로 특성을 삭제하거나 모델을 바꾸지 않습니다.
원본 metadata의 컬럼 이름은 달라도 되지만 의미 사전과 역할별 서로 다른 컬럼, 세 분할·이진 정답 계약은 필요합니다.
운영자는 명시적 의미 사전을 포함한 새 이미지와 대응 모델·출처/라이선스를 준비해야 합니다.
저장소에 데이터셋별 profile이나 data_id를 사전 등록하지 않습니다.

`config/prepared-source.json`은 게시 workflow가 새 이미지를 만들거나 재게시할 때 읽는 제작 명세입니다.
일반 실습의 첫 실행과 후속 실행은 이 파일 대신 고정 GHCR 이미지의 manifest를 사용합니다.
제작 명세에서 EDA 중복 수·결측 수의 고정 기준값은 제거했으며, Python이 실제 자산에서 계산합니다.
이전 이미지에 이미 포함된 출처·검증 기록은 수정하지 않습니다. 제작 명세를 Merge 후 승인 판단에 사용하지 않습니다.

환자 중복의 집합 비교는 그대로 재사용합니다. 다른 등록 프로필에는 Diabetes의 선정조건·30일 추적·Caucasian 대치·113-feature 이력을 적용하지 않습니다.
다른 컬럼명·분할/정답값을 가진 합성 패키지로 재사용을 검사했으며, 다른 실제 임상 데이터셋 검증을 수행했다고 주장하지 않습니다.
임의 원본 CSV 수입·새 전처리 fit·새 학습·다중분류·임의 CV 분할·환자 ID 없는 데이터의 자동 판정은 지원하지 않습니다.

## Issue Form

[research-request.yml](.github/ISSUE_TEMPLATE/research-request.yml) 폼은 작성됐습니다.
연구 목표·질문, 대상 조건, 추가 요청, 공개 데이터/민감정보 미포함 확인을 받습니다.
인구집단 선택 입력은 두지 않고, 제공된 연령·성별·인종을 기본 점검에 사용합니다.
현재 Diabetes 목적·EDA 질문·전체 집단을 기본값으로 제안하되 사용자가 수정할 수 있습니다.
113개 컬럼·target·checkpoint·GHCR digest를 직접 입력시키지 않으며 로컬 연결 코드는 시스템 명세/설정에서 읽습니다.

**폼 제출 → AI 목적·입력 조율 → Python 점검 → AI 추천 포함 PR1 → 자연어 피드백·선택 변경 → 사람이 Merge한 계획 기록**을 연결합니다.
현재 작업에서 Target 입력 필드·동의·폼 YAML은 변경하지 않습니다. 스키마는 등록 GHCR 자산에서 읽습니다.
사용자의 명시적 선택 변경과 사람의 최종 승인은 구분하며 자유 입력을 임의 Python/SQL로 실행하지 않습니다.

## 로컬 소스 검증

게시 런타임에는 ML 패키지가 필요 없습니다. 현재 계약만 검사하려면 Python 표준 라이브러리로 실행합니다.

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -B -m unittest discover -s tests -p 'test_prepared_source.py' -v
```

기존 고정 ML 의존성이 있는 개발 환경에서 전체 회귀 검사를 실행할 수 있습니다.

```powershell
python -B -m unittest discover -s tests -p 'test_*.py' -v
```

폼 계약 검사에는 `requirements-workflow.txt`, 지표 비교 회귀에는 `requirements-ml.txt`가 필요합니다.
새 검사는 합성 자산과 모의 Docker 호출만 사용합니다.
로컬 Docker/Desktop을 켜지 않습니다. 소스 검증과 실제 GitHub DB 스모크·클라우드 게시 성공은 별개입니다.

## 출처

[UCI Diabetes 130-US Hospitals](https://archive.ics.uci.edu/dataset/296/diabetes+130-us+hospitals+for+years+1999-2008),
John Clore, Krzysztof Cios, Jon DeShazo, Beata Strack.
DOI [10.24432/C5230J](https://doi.org/10.24432/C5230J), [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
준비 데이터와 모델의 고정 HF source 참조는 `config/prepared-source.json`에 기록합니다.
