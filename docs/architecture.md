# GHCR 자산·조합형 EDA·평가 계획 승인 구조

## 현재 연결 범위: Target → EDA → Evaluation Plan

```text
연구 Issue / prepare-eda-pr.yml
  → default branch의 신뢰 코드와 폼 YAML 계약
  → 고정 Public 데이터 이미지의 준비파일 검증
  → EDA 카탈로그: 필수 + 기본 + AI + 사용자 추가 - 사용자 제외
  → 별도 Evaluation 카탈로그: 결과 지표 실행 계획만 조합
  → 집계·두 계획·사람 소유 selection.json → 연구검토 draft PR
  → recommend-blocks.lock.yml의 Copilot 엔진
      공개 집계·구조화 목적·카탈로그만 제공
      등록 ID·한국어 이유·허용 evidence를 검증
      생성파일 6개와 자동 PR 본문 영역만 갱신
  → review-plan.yml의 명령 전용 /plan 댓글
      사람 write/maintain/admin 권한 확인
      선택·감사 기록의 원자적 저장 → 신뢰 소스 clone에서 보고서 재생성
  → 사람이 Ready for review → Merge
  → 병합 commit의 파일·이미지·선택·해시·제한을 새 불변 승인 기록에 저장
```

EDA는 데이터 점검, Evaluation은 예측 결과 지표다. 서로 다른 카탈로그와 실행 블록을 사용한다.
15개 지표 계산 모듈은 준비했지만 이 연결에서는 모델 추론·지표 계산을 호출하지 않는다.
기본/필수 선택은 `planning-policy.json`, 연구별 선택은 `selection.json`, 실제 AI 입력은
`ai-recommendations.json`으로 분리한다. AI는 사람의 주지표·threshold를 결정하지 않는다.
`planning_complete`는 추천 조합 완료이지 사람의 승인 완료가 아니다.
보고서의 별도 승인 준비 조건은 AI provided·명시적 주지표·선택 평가항목 blocked 없음이다.

AI pending/stale은 미완료로 남기며 가짜 빈 추천을 성공으로 만들지 않는다.
Copilot native 인증은 github.token과 copilot-requests: write이며 PAT/AI Secret을 요구하지 않는다.
일반 연결의 actions: write는 추천 워크플로 요청에만 필요하다.
사람 선택 파일·요청 원문은 AI 게시 대상에서 제외하며 PR의 사람 본문·제목·댓글을 보존한다.

명령 댓글은 등록된 ID와 검증된 매개변수만 변경한다. 일반 문장·인용·코드블록을 명령으로 해석하지 않는다.
연속 댓글은 미처리 ID 순서로 처리하고, 이미 처리한 댓글 편집은 새 요청으로 보지 않는다.
선택 저장 뒤 보고서 갱신이 실패해도 기존 선택을 보존하며 재실행으로 갱신을 복구한다.
PR 코드·사용자 셸·SQL은 실행하지 않는다. 원래 checkout의 HEAD/index/origin도 변경하지 않는다.

Merge 후 `research/approved/issue-N/{merge-SHA}.json`은 현재 main의 보고서가 아니라
실제로 병합한 commit의 입력을 검증해 기록한다. 최신 main에는 그 승인 파일만 추가하고 force push하지 않는다.
승인 범위는 educational_demo_only이며 임상 승인 false, 알려진 환자 중복·불확실성을 보존한다.
동일 기록은 no-op이고 내용이 달라지면 불변성 오류다. 준비가 안 된 병합에는 승인 기록을 만들지 않는다.

운영 범위는 템플릿 코드 반영과 PR 생성 허용 설정까지다.
독립 복사본 설정·갱신과 실제 Issue/Copilot/PR 승인 흐름 확인은 사용자가 수행한다.
템플릿에서 이 연결의 실제 원격 이벤트 성공을 검증했다고 주장하지 않는다.

## 2단계: 준비파일을 읽는 EDA 경로

```text
검증된 data/assets (+ 선택적 고정 UCI raw)
  → app.eda.load_context
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

기존 pair 초안의 `assets.py/bootstrap.py`는 회귀 호환상 수치 행을 schema에 전달하고 **수치 계약만** 초기화 게이트로 삼는다.
전체 schema/인종 원본 근거의 unverifiable 상태는 반환값에서 유지한다. 현재 EDA가 그 초안 실행 경로를 호출하지 않는다.

Issue→EDA→연구검토 draft PR과 3단계 연결은 위 현재 범위를 따른다.
모델 추론·실제 결과 지표 실행·재전처리·재분할·자산 재게시는 이번 범위 밖이다.
게시 workflow·고정 source/digest·Dockerfile은 유지하고 폼의 자동 연결 안내만 갱신했다.

## 2단계 자동 연결

```text
prepare-eda-pr.yml: issues.opened / workflow_dispatch(issue_number)
  → default branch checkout (사용자 ref 입력 없음)
  → 폼 YAML 의존성 설치와 EDA/intake/prepared-source 회귀
  → app.research:
      작성자 신뢰/수동 권한 + GitHub API JSON의 정확한 5필드 폼 검사
      config의 고정 data_image만 pull
      docker create/cp → data/assets → 컨테이너 삭제 (start/run 없음)
      기존 validate_data_manifest + 선택한 app.eda 블록
      입력 손상은 중단; failed/unverifiable 연구 판정은 보존
      research/reports/issue-{번호}/의 집계·두 계획·추천 요청·사람 선택
      고정 research/review-issue-{번호} branch → default branch 대상 draft PR
      생성한 runtime directory만 finally 정리
```

모델 이미지·raw 원본은 받지 않고 DB 서비스를 실행하지 않는다. 임의 ref/image/path/SQL/Python을 입력받지 않는다.
권한은 contents write / pull-requests write / issues read / actions write다.
Public data pull에는 packages write·새 자격증명이 필요 없다.
OWNER/COLLABORATOR Issue만 자동 진행하고 다른 작성자는 수동 dispatch를 요구한다.
수동 실행자는 API write/maintain/admin 권한을 확인한다.
저장소·default branch는 event의 동적 값이고 본문은 event 파일/API JSON에서만 파싱한다.

PR 결과는 원본 Issue·data digest/data_id/revision·코드 commit·실행 링크, 상태/집계 표/한계와 평가 계획 추천 초안을 담는다.
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
| `config/prepared-source.json` | source revision/URL/SHA/bytes/rows/schema/target/모델 버전·조건부 등록 명세 |
| `app/asset_contract.py`, `app/dataset.py`, `app/ghcr.py` | 기존 hash·UCI 명세·GHCR 이름/digest 도우미 재사용 |
| `data/` | 출처/NOTICE와 원본 명세만. Git에 CSV·바이너리 없음 |
| `db/` | PostgreSQL Dockerfile/init.sql/healthcheck.sh |
| `app/Dockerfile` | `FROM scratch` 모델 artifact. 학습/추론 런타임 없음 |
| `tests/test_prepared_source.py` | 새 자산 계약·모의 다운로드/runner·무학습/무추론 검증 |
| `.github/workflows/ci.yml` | 전체 합성 회귀 검증. ML 의존성은 이전 fixture 테스트 때문에만 사용 |
| `.github/ISSUE_TEMPLATE/research-request.yml` | 기존 5필드·동의 유지, 자동 연결과 복사본 PR 생성 설정 안내 |
| `.github/workflows/prepare-eda-pr.yml`, `app/research.py` | Issue intake→데이터 이미지 준비파일→선택 EDA·두 계획→draft PR |
| `app/planning.py`, `config/*-catalog.json`, `config/planning-policy.json` | 독립 카탈로그·정책·AI·사용자 선택 조합, 별도 승인 준비 판정 |
| `app/blocks/metrics/` | 독립 결과 지표 15개와 입력 검증. 3단계에서는 계산하지 않음 |
| `recommend-blocks.md`, `recommend-blocks.lock.yml`, `app/cloud_recommendations.py` | 실제 gh-aw Copilot 엔진·공개 집계·추천 검증·기존 PR 안전 갱신 |
| `review-plan.yml`, `app/review.py` | 사람의 명령 댓글과 Merge 기반 불변 승인 기록 |
| `tests/test_research_workflow.py` | 입력/권한/이미지·GH 모의/멱등 갱신/closed PR/집계·추천 계약 회귀 |
| `config/assets.json` | 실제 게시된 data/model 두 digest 고정 참조. 두 패키지 Public·익명 조회 확인 |

`app/prepared.py`는 ML 모듈을 import하지 않는다. 모델 pickle을 역직렬화하지 않으며 bytes·hash·명세만 검사한다.
기존 `app/assets.py`의 전처리·새 모델 학습, `app/bootstrap.py`의 pair 검증/개인 패키지,
`app/infer.py`의 예전 실행 계약은 현재 게시 경로에서 분리한 **후속 정렬 대상 초안**이다.
`initialize-workshop.yml`은 첫 step의 명시적 오류/중단으로 초안 실행을 차단하고 쓰기 권한을 제거했다.
새 model artifact는 기존 inference image와 다르므로 초기화가 완료됐다고 해석하지 않는다.

## 입력과 DB 계약

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
