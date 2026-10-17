# 고정 모델 추론과 기존 평가 지표 연결

상태: 사용자 승인에 따른 구현 계약. 이 문서 작성 시점에는 **계획**이며, 아래 실행 경로의 구현·원격 실행 성공을 뜻하지 않는다.

## 목적과 근거

사람이 Merge한 연구 검토 PR의 저장된 계획을 그대로 읽어, 등록된 고정 모델로 선택 분할의 확률을 구하고 기존 15개 지표 중 선택된 항목만 계산한다. Merge 자체가 교육용 계획에 대한 사람 승인이다. 별도 승인 JSON·추가 수동 승인 게이트는 만들지 않는다.

현재 근거:
- `README.md`: 준비된 113-feature 입력, 조건부 LogisticRegression 등록, 고정 이미지와 알려진 한계.
- `config/assets.json`: 실제 게시된 DATA/MODEL digest의 정본.
- `config/prepared-source.json`, `app/prepared.py`: 고정 출처·파일 해시·분할·모델/데이터 manifest 검증 계약. source 설정은 제작·검증 근거이지 새 다운로드 지시가 아니다.
- `app/planning_eda.py`, `app/planning_eval.py`, `app/research.py`: 저장된 실행계획·평가계획·입력 서명과 연구 보고서 경로.
- `app/executor_eval.py`: `execute_plan(plan, context)`와 15개 지표의 순수 계산·집계 출력 계약.

## 1. 입력과 추론 경계 — `app/inference.py`

- 지원 모델은 등록된 `sklearn.linear_model.LogisticRegression` 하나다. 입력은 이미 표준화된 **정확히 113개 특성의 고정 순서**, classes `[0, 1]`, positive class `1`이다. `1`은 `<30`, `0`은 `>30` 또는 `NO`다.
- DATA는 `ghcr.io/t-hajongkim/medical-ml-sdlc-db@sha256:a81f2187eaac6b465b3443df423ee369ab7e7cd216e71ab9c0a4aeb8a88ab947`, MODEL은 `ghcr.io/t-hajongkim/medical-ml-sdlc-model@sha256:1617d5200111f0529d29d4a59517177dc6eb6cc5bf4a5ba88408c1cd83f8ee38`로 고정한다. 실행 시 신뢰 코드의 `config/assets.json` 및 저장된 계획과 모두 대조한다. 태그·사용자 지정 이미지·모델 URL·모델 파일 경로로 대체하지 않는다.
- **`joblib.load` 전에** 기존 `prepared.validate_data_manifest(data_assets)`와 `prepared.validate_model_manifest(model_assets, data_manifest)`를 호출한다. data/model ID·출처·특성 순서·target mapping·allowlist·파일 해시를 검증하며 checkpoint는 4,018 bytes, SHA256 `f923be3373fbd958df6f1d9a0694fc4a36c6727f6c48090637be84ed56e9e823`인 등록 파일만 허용한다. 불일치 시 역직렬화하지 않는다.
- 로드 후에도 실제 객체 타입·`n_features_in_ == 113`·classes를 검사한 뒤 `predict_proba`의 class `1` 열만 사용한다. 이진 확률의 형상·행수·유한성·범위 `[0,1]`을 검사한다.
- `db_features.csv`의 `row_key/split/row_position`과 `metadata.csv`의 `row_key/split/target`, manifest의 split counts를 엄격히 연결한다. metadata에는 `row_position`이 없으므로 feature의 위치를 기준으로 순서를 확정한다. 키 중복·누락·split 불일치·중복/누락 위치·특성 길이/순서 오류·비유한 입력·비이진 target은 실패한다. 단순 zip·행수 일치만으로 정렬을 승인하지 않는다.
- 기본 분할은 `validation`; 명시적으로 선택한 경우에만 `test`를 사용한다. 둘 다 현재 15,265행이다. `train`·전체 분할 평가·오류 시 다른 분할로 fallback은 금지한다. 실행마다 실제 사용 분할을 기록한다.
- 특성·정답·확률·sample ID는 비공개 메모리 문맥으로만 다음 호출자에게 전달한다. 학습/전처리 fit·추가 scaler·재분할·raw 다운로드는 없다. 호환되지 않는 120-feature scaler는 사용하지 않는다.
- 선택적 로컬 smoke CLI는 이미 검증된 준비 파일 묶음을 다시 검증해 사용하는 경로만 제공할 수 있다. 임의 checkpoint 경로를 받는 범용 로더는 만들지 않는다.

## 2. Merge 후 상위 호출자 — `app/model_evaluation.py`

1. GitHub API로 **정확한 PR 번호**의 실제 merged 상태·동일 저장소·기본 브랜치 대상·`research/review-issue-{번호}` head 및 보고서 Issue 연결을 확인한다. 최신 PR을 추측하지 않는다.
2. 실제 API의 immutable `merge_commit_sha`에서 `research/reports/issue-{번호}`의 허용된 보고서 JSON만 읽는다. 최소 `request.json`, `evaluation-plan.json`, `eda-plan.json`, `eda.json`, `execution-plan.json`, `selection.json`, `ai-recommendations.json`, `input-schema.json`, `input-mapping.json`을 사용하고, 관련 출처 검증에 필요한 생성 JSON만 추가한다. `github_io`의 tree/blob 무결성·일반 파일·엄격 JSON 검증을 재사용한다. 누락·symlink·경로 이탈·중복 JSON 키는 거부한다.
3. 저장된 평가계획을 새 추천/선택으로 교체하지 않는다. evaluation schema/stage·`ai_status: provided`·literal `planning_complete: true`, 등록 ID·매개변수 및 EDA/입력 매핑·plan hash·DATA/MODEL digest·data ID·catalog/policy 계약의 일치를 검증한다. `planning_eda.validate_execution_plan`, `input_signature`, `executor_eda.execution_result_hash` 등 기존 계약/집계 검증을 재사용한다. 계획·EDA 파일의 내용 hash도 실행 출처로 기록한다.
4. 계획에 기록된 `code_commit`의 관련 실행 코드·설정·계약이 현재 checkout한 신뢰 기본 브랜치 소스와 일치하는지 검증한다. 보고서만 추가된 Merge SHA와 이전 코드 SHA가 다르다는 이유만으로 실패시키지 않되, 실제 관련 소스/계약 변경은 stale 오류로 중단한다. 과거 PR 코드를 checkout/import해 실행하거나 자동 재계획해 우회하지 않는다.
5. 검증 후 고정 자산을 준비해 inference를 호출하고 비공개 context를 `executor_eval.execute_plan(saved_plan, context)`에 전달한다. 기존 15개 지표는 **수학 라이브러리**로 유지하며 모델/GitHub/네트워크/승인 로직을 넣지 않는다. EDA를 재계산하지 않는다.
6. 저장 계획에 없는 threshold·top fraction·임상 행동·비교모델/참조 확률을 생성하지 않는다. 지표별 선행조건 부족은 기존 `not_applicable`과 이유를 보존한다. 잘못된 계획·데이터·모델 계약은 명시적 실행 실패이며 정상 결과로 가장하지 않는다.

## 3. GitHub Actions 실행 계약

- 신규 `.github/workflows/evaluate-model.yml`은 research 검토 PR의 `pull_request: closed` 중 **merged인 경우**와 `workflow_dispatch`만 받는다. 수동 입력은 정확한 merged PR 번호와 `validation`/`test` 분할뿐이다. PR 열기·분석/피드백 댓글·Issue 생성으로 실행하지 않는다.
- GitHub-hosted runner **단일 job**, `contents: read`, `pull-requests: read`, `packages: read`만 사용한다. 신뢰된 기본 브랜치를 checkout하고 그 SHA를 기록한 뒤, 보고서는 실제 Merge SHA의 파일로만 읽는다. 보고서 브랜치/commit의 실행 코드나 workflow·의존성을 실행하지 않는다.
- 모델 저장 버전과 맞는 **Python 3.11 + scikit-learn 1.2.2** 런타임을 명시한다. 새 `requirements-inference.txt`에 호환되는 NumPy(<2)·SciPy·joblib 등 필요한 의존성을 정확히 pin하고 별도 검증한다. 기존 `requirements-ml.txt`(sklearn 1.6.1/NumPy 2)·기존 CI 환경을 무조건 대체하지 않는다. Actions 참조도 고정 SHA를 사용한다.
- Public 고정 이미지의 익명 다운로드/무결성 검증을 지원한다. Docker는 `pull/create/cp/rm`으로 파일 추출만 한다. DB `start/run`·포트 노출·게시/재학습·raw 재다운로드는 하지 않는다.
- 결과는 허용된 **집계 JSON + Markdown만** Actions artifact와 job Summary에 남긴다. PR 번호/merge SHA/신뢰 실행 SHA/저장 계획 hash/EDA 결과 hash/data ID/model ID/두 digest/분할/런타임/선택 지표·상태/알려진 한계를 포함한다. 입력 CSV·checkpoint·환자/입원 키·행 위치·labels·확률·특성 벡터·top-k 선택 행은 어떤 로그/실패 메시지/캐시/artifact에도 남기지 않는다.
- 실패 이유는 행 없는 고정 범주로 명시하고 exit failure로 끝낸다. 실패/취소 시에도 추출 컨테이너·비공개 파일·중간 산출물을 정리한다. 실패를 성공 성능 보고서로 게시하지 않는다.
- 새 PR2·Pages·PR 댓글/자동 승인/자동 Merge·원격 workflow dispatch·임상 사용 승인 연결은 없다. AI가 임의 모델 URL/경로·새 Python/셸 코드를 실행하게 하지 않는다.

## 4. 설명과 해석 경계

구현 시 README의 기존 **박스형 전체 흐름 그림**을 복원하고 Target → EDA → 계획 PR → 사람 Merge → 고정 추론 → 기존 지표 → Actions 집계 결과의 경계를 표시한다. 현재 구현/계획을 혼동하지 않도록 상태를 갱신한다.

GitHub 테스트는 두 종류로 설명한다. 기존 `ci.yml`의 합성 계약/회귀 테스트는 외부 자산 다운로드·Docker·실제 추론을 증명하지 않는다. 신규 Merge 후 실행은 고정 자산을 실제로 추론·평가하되, 해당 run의 artifact/Summary가 있어야 실행 성공을 주장할 수 있다. 로컬 테스트 green·YAML 검사만으로 원격 성공을 주장하지 않는다.

등록은 준비 CSV의 **조건부 교육용 추론**이다. train–test 5,086 / train–validation 5,055 / validation–test 1,760 환자 중복, 모델 개발의 validation 재사용, split 전 target encoding 및 113-feature 전처리 이력 불확실성, 원본 인종 결측/대치 한계를 그대로 남긴다. 올바른 predict·지표 계산은 누수 없는 독립 holdout·원본 입력 호환·임상적 유효성 인증이 아니다.

## 5. 수용 검사 — 신규 테스트를 먼저 작성

| 검사 | 기대 결과 |
|---|---|
| `tests/test_inference.py`: loader 호출 순서 | data/model manifest 검증이 모두 먼저 성공해야만 `joblib.load` 호출. 변경된 hash·출처·manifest·digest·특성 순서는 load 전에 거부 |
| 실제 객체/입력 계약 | 비등록 타입·113 불일치·classes 불일치·잘못된 확률 형상/길이/NaN/범위는 실패 |
| 행 연결 | metadata 입력 순서가 달라도 key/split/position으로 동일하게 정렬. 중복/누락 키·위치·split·target 오류는 실패 |
| 분할과 무학습 | 기본 validation, 명시 test만 사용. train/알 수 없는 분할 거부. fit/scaler/재분할/raw 다운로드 호출 없음 |
| `tests/test_model_evaluation.py`: PR/보고서 출처 | 실제 merged PR 및 immutable merge SHA만 사용. unmerged/타 저장소/다른 head/보고서 Issue/파일 변조/경로 이탈 거부. PR 코드 checkout/실행 없음 |
| 계획/계약 연결 | 저장 계획을 그대로 executor에 전달. plan/EDA/mapping/catalog/policy/data/model/source 불일치·stale·누락은 inference 전에 실패. 보고서만 다른 Merge는 허용 |
| 기존 지표 호환 | 선택 ID만 실행, 입력 계획 불변. threshold 미정·DCA/NRI 선행조건 부족은 기존 `not_applicable`; 임의 값 생성 없음 |
| 공개 출력/실패/정리 | 집계 JSON/Markdown과 provenance만 허용. 성공/오류 모두 row key·위치·벡터·top-k 행 미노출, 비공개 파일/컨테이너 정리 |
| workflow 정적 계약 | merged research PR/정확한 수동 PR만, 단일 hosted job/read-only 권한/신뢰 checkout/별도 pinned 런타임/집계 artifact. 댓글 trigger·DB 기동·PR2/Pages/쓰기 없음 |
| 회귀와 실제 smoke | 기존 테스트 약화 없이 신규 테스트 red→green, 기존 합성 전체 회귀 유지. 승인된 실행 시 고정 Public 자산 실제 smoke의 집계/행수/출처/정리를 별도로 검증 |

## 이번 planner 작업의 종료 경계

이번 산출물은 본 PRD와 `docs/architecture.md`의 계획 절뿐이다. 코드·테스트·workflow·의존성·README 수정, Git 작업·push·원격 실행은 하지 않는다. **사용자가 원격 변경을 요청하지 않아 클라우드 이슈 생성/할당은 수행하지 않았다.**
