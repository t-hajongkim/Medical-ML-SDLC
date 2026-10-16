# Medical ML SDLC Workshop

구현 범위는 **Target → AI 목적·입력 조율 → Python EDA → AI 지표 추천을 포함한 PR1 → 자연어 피드백 반영 → 사람의 PR 병합 후 승인 계획 기록**입니다.
전처리·train/validation/test 분할·대응 모델 준비는 완료된 입력 계약으로 가정하며 폼은 유지합니다.
준비된 Diabetes 데이터와 검증된 모델 파일은 공통 GHCR에 게시했습니다.
EDA 점검 7개와 결과 지표 15개는 별도 카탈로그로 관리하며, 기본·AI·사용자 선택을 조합합니다.
**계획 확정은 모델 실행·최종 성능 평가·임상 사용 승인이 아닙니다.** Model·최종 Evaluation·Pages 연결은 후속 범위입니다.
게시 YML은 새 학습·전처리 fit·모델 로딩·predict를 실행하지 않습니다.
GHCR은 이미지 레지스트리이지 실행 중인 DB가 아닙니다.

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

결과 브랜치는 있는데 PR이 없고 `gh pr create`가 실패했다면 **복사한 저장소 자체의 PR 생성 허용 설정**을 확인합니다.
템플릿에서 켠 옵션은 복사본에 상속되지 않습니다.
설정 수정 후 Actions의 실패한 실행에서 **Re-run failed jobs**를 선택하면 기존 브랜치를 재사용합니다.
새 Issue를 만들거나 결과 브랜치를 삭제할 필요가 없습니다.
이 설정을 확인해도 실패한다면 워크플로의 `pull-requests: write`와 조직 정책·브랜치 규칙을 확인합니다.

## 먼저 볼 워크플로

| 파일 / Actions 표시 이름 | 역할 | 현재 상태 |
|---|---|---|
| [publish-db-image.yml](.github/workflows/publish-db-image.yml) / **Establish Database Image** | 고정 공개 자산 검증 → DB 이미지 빌드·스모크 → 모델 파일 이미지 빌드·무결성 검사 → 두 공통 GHCR 게시 | [2026-10-15 실제 게시 성공](https://github.com/t-hajongkim/Medical-ML-SDLC/actions/runs/38722808263). 두 패키지 Public·익명 조회 확인 |
| [initialize-workshop.yml](.github/workflows/initialize-workshop.yml) / **Initialize Workshop** | 이전 데이터/모델 pair 기반 참가자 초기화 초안 | 현재 계약과 호환되지 않아 첫 step에서 명시적으로 중단. 쓰기 권한 제거. 실행하지 않습니다 |
| [ci.yml](.github/workflows/ci.yml) / **Check workshop setup** | 현재 자산 계약과 이전 코드의 합성 fixture 회귀 검사 | 외부 자산 다운로드·Docker·GHCR 게시 없음 |
| [prepare-eda-pr.yml](.github/workflows/prepare-eda-pr.yml) / **Prepare research review** | Issue·고정 이미지 스키마 검증 → EDA 미실행 draft PR → 정확한 추천 대상 전달 | Issue opened 또는 유지관리자의 수동 실행 |
| [recommend-blocks.md](.github/workflows/recommend-blocks.md) / **연구 블록 추천** | 선행 완료 → 공개 목적·스키마 조율 → Python EDA → 실제 결과 기반 추천 → 같은 PR 갱신·댓글 안내 | `workflow_run` 또는 수동 PR 번호. 공식 생성 lock, native 인증. PAT·AI Secret 없음 |
| [review-plan.yml](.github/workflows/review-plan.yml) / **Review selections and freeze approved plan** | 자연어 피드백 대기열·기존 명령 처리 / 사람이 Merge한 교육용 계획을 불변 기록 | 현재 write 권한·본문 hash 검증, 등록 ID/매개변수만 처리, 자동 승인·병합 없음 |

운영자는 자산 게시 YML을 검토합니다. 참가자 개인 패키지·초기화·PR2·최종 모델 평가·Pages는 이번 범위가 아닙니다.

자동 추천은 사수님 참고 저장소의 후속 검증과 같은 **`workflow_run` 완료 이벤트**로 연결합니다.
토큰으로 새 `workflow_dispatch`를 호출하지 않아 호출자를 봇으로 바꾸지 않습니다.
사람의 기본 admin/maintainer/write 역할 검사와 같은 저장소의 생성 PR·허용 파일 검증을 유지합니다.
`on.bots` 예외·`roles: all`·추가 PAT·조직 권한 확대는 추가하지 않습니다.
추천 실행이 success여도 agent가 skipped라면 실제 AI 추천이 아닙니다.
계획의 `ai_status: provided`와 `ai-recommendations.json` 반영 여부를 확인합니다.
전달 artifact에는 PR 번호·실행 ID/회차·commit·요청 여부만 담습니다. 원자료·환자 행은 담지 않습니다.
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
| `config/prepared-source.json` | 신규 고정 준비 데이터/모델 URL·revision·bytes·SHA·113 변수 순서·target·한계 명세 |
| `data/source_manifest.json`, `data/NOTICE.txt` | UCI 원본 고정 명세·출처·라이선스·변경 및 한계 기록 |
| `db/Dockerfile`, `db/init.sql`, `db/healthcheck.sh` | 데이터 전용 PostgreSQL 이미지·적재 계약·준비 상태 검사 |
| `app/Dockerfile` | 재사용·정리한 모델 **artifact** 이미지 정의. 추론 실행 이미지가 아님 |
| `.dockerignore`, `.gitignore`, `.gitattributes` | 컨텍스트 허용 목록·CSV/바이너리/캐시 커밋 방지·줄바꿈 |
| `app/asset_contract.py`, `app/dataset.py`, `app/ghcr.py` | 재사용한 hash/원본 검증/이미지 이름·태그·digest 도우미 |
| `tests/test_prepared_source.py` | 신규 현재 자산 계약·불일치 fail-fast·무학습/무추론·runner 모의 검사 |
| `config/assets.json` | 실제 게시·Public 확인한 공통 data/model의 고정 digest 참조 |
| `docs/prd/001-ghcr-db-setup.md`, `docs/architecture.md`, `docs/adr.md` | 현재 요구·구조·결정과 과거 범위의 구분 |

컨텍스트는 runner 임시 경로의 `data/`와 `model/`에 생성합니다.
`db/Dockerfile`과 `app/Dockerfile`의 COPY는 **각 생성 컨텍스트 기준**입니다.
저장소 루트나 `db/`, `app/` 자체를 그대로 빌드 컨텍스트로 넘기지 않습니다.
원자료·CSV·모델 바이너리를 Git, Actions Artifacts, Releases에 올리지 않습니다. 외부 DB 포트 노출을 추가하지 않습니다.

`app/assets.py`·`config/preparation.json`·`app/bootstrap.py`·`app/infer.py`와 기존 pair 테스트는 **이전 설계 회귀용 초안**으로 남깁니다.
이전 `.fit()` 경로·전처리기 요구·patient-disjoint pair 계약을 현재 publisher가 호출하지 않습니다.
기존 container 테스트 4개는 이전 pair/inference 이미지 계약용이며 새 artifact 이미지의 실제 실행 검증이 아닙니다.
현재 runner의 DB 전체 대조와 모델 파일 검사는 `app.prepared smoke` / `verify-model-image`로 분리했습니다.
후속 초기화·추론 연결 때 이 초안을 새 계약으로 정렬해야 합니다.

## 2단계: 독립 EDA 블록 준비

`app.blocks.catalog.run_checks(identifiers, context)`는 각 독립 파일의
`run(context: dict) -> {"block", "status", "details"}`를 호출합니다.
`app.eda`는 `app.prepared.validate_data_manifest`를 재사용해 `data/assets`를 확인하고
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
python -B -m app.eda --assets 'C:\workshop-assets\data\assets' `
  --raw-csv 'C:\workshop-assets\diabetes130_raw.csv' `
  --output 'C:\workshop-results\eda-aggregate.json'
python -B -m unittest discover -s tests -p 'test_eda_blocks.py' -v
```

raw 입력은 선택 사항이며 없으면 원본 조건/라벨 연결은 확인 불가입니다.
자산 context가 필요하면 기존 `prepare_data(...)`만 직접 재사용합니다.
`app.prepared prepare` CLI는 모델 포장도 호출하므로 EDA 준비에 사용하지 않습니다.
기존 pair 회귀용 `assets/bootstrap` 소비자도 수치 행을 새 schema 검사에 전달하도록 맞췄으며,
임상/원본 인종 근거 부족 상태는 그대로 남깁니다. 이 경로는 현재 EDA runner가 호출하지 않습니다.

실데이터 집계, 합성 단위·통합 테스트와 원격 이벤트 실행은 서로 다른 근거입니다.
이 연결 작업에서는 참가자 초기화·모델 load/fit/predict·재전처리·재분할·자산 재게시를 하지 않습니다.
자산 게시 외 실제 Issue→Copilot→승인 이벤트 흐름은 실습 저장소에서 별도로 확인해야 합니다.

### Issue → 데이터 점검 → 연구검토 draft PR

`.github/workflows/prepare-eda-pr.yml`과 `app/research.py`가 연결을 담당합니다.

```text
Issue opened 또는 유지관리자의 workflow_dispatch(issue_number)
 → 신뢰/권한 게이트 + 현재 5필드 폼 검증 (본문은 데이터)
 → 신뢰한 default branch 코드 + config/assets.json의 고정 data_image
 → Public 데이터 이미지 pull → docker create/cp로 준비파일만 추출 → 생성 컨테이너 삭제
 → 기존 manifest/스키마 검증 → EDA 미실행 준비 보고서
 → research/review-issue-{번호} → default branch 대상 연구검토 draft PR
 → 추천 대상 메타데이터 보관 → 선행 workflow 완료 이벤트
 → Copilot: 공개 연구 목적·스키마·현재 선택·허가된 미반영 피드백 확인
 → 미확정 매핑만 AI 추천 → Python 검증·표준 context
 → AI 조율안(등록 목적·점검 ID·허가된 선택 변경) → Python 검증·실제 EDA
 → Copilot: 목적과 전체 실제 EDA를 근거로 평가 지표 추천
 → Python 조합 → 집계·계획·조율/피드백 감사 기록 → 같은 PR 갱신·AI 댓글
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
| `review-state.json` | 명시적 댓글의 작성자·본문 해시·적용/거부 내역. 명령 댓글 처리 때만 생성 |
| `input-schema.json` | 환자 행 없는 등록 스키마·의미 후보·타입·결측 집계·공개 코드북·context hash |
| `input-mapping.json` | 검증한 역할 매핑·원래 추천·profile/source 스냅샷. EDA와 승인 기록에 hash로 연결 |
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
- 준비·댓글 워크플로는 `contents: write`, `pull-requests: write`, `issues: read`의 **GITHUB_TOKEN만** 사용합니다.
  토큰 기반 재호출을 제거해 `actions: write`는 필요하지 않습니다. 추천 대상 해석 job만 전달 artifact를 위한 `actions: read`를 사용합니다.
  추천 엔진은 별도의 `copilot-requests: write`를 사용합니다.
  packages write/PAT/새 Secret은 없습니다.
- [참가자 주의사항](#참가자-주의사항)의 PR 생성 허용 설정을 확인해야 합니다.
  조직 정책·branch 규칙도 해당 쓰기를 허용해야 합니다. 템플릿 설정은 복사본에 자동 상속되지 않습니다.
- GITHUB_TOKEN이 만든 PR/push는 다른 CI를 자동 trigger하지 않을 수 있습니다.
  이 workflow 자체가 실행하는 EDA/intake/prepared-source 회귀와 입력 검증 및 실행 링크가 현재 근거입니다.
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
워크플로 이름이 연결 계약이므로 이름을 바꾸면 `recommend-blocks.md`의 `workflow_run.workflows`도 함께 변경합니다.
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

결정론적 명령 댓글도 그대로 지원합니다. 명령은 **명령만 별도 댓글**로 작성합니다.

```text
/plan add eda prediction_timing
/plan add evaluation f1
/plan primary average_precision
/plan threshold 0.3
```

| 명령 | 의미 |
|---|---|
| `/plan add eda ID`, `/plan remove eda ID` | 데이터 점검 추가·제외 |
| `/plan add evaluation ID`, `/plan remove evaluation ID` | 결과 지표 추가·제외 |
| `/plan primary ID` | 주지표 지정과 해당 지표 선택. 현재 주지표를 제거하려면 먼저 다른 주지표 지정 |
| `/plan threshold NUMBER` | 임계값 명시. 자동으로 0.5를 넣거나 최적화하지 않음 |
| `/plan parameter top_fraction 0.1` | top fraction 지정. `n_bins`, `thresholds`, `risk_thresholds`도 등록 범위에서 지원 |
| `/plan clinical-action TEXT` | DCA가 전제하는 교육용 행동 명시. 실제 개입·임상 승인 아님 |
| `/plan recommend` | 실제 Copilot 재추천 요청. 고급 선택은 웹에서 `selection.json` 수정 후 이 명령으로 갱신 가능 |

write/maintain/admin 권한이 있는 사람만 변경할 수 있습니다. 자연어는 검증된 조율의 입력이지 실행 코드가 아닙니다.
처리된 댓글의 편집은 재적용하지 않으므로 새 댓글로 변경합니다.
여러 댓글은 ID 순서로 처리하고 중복 적용을 막습니다. 미반영 자연어 댓글이 편집되면 새 hash로 다시 준비합니다.
AI 준비 이후 본문/권한/PR/연구 목적이 바뀌면 게시하지 않으며, 미반영 자연어 요청이 남으면 승인 기록도 만들지 않습니다.
유효 명령과 거부 명령은 분리해서 Actions Summary·`review-state.json`에 표시합니다.
선택 저장 후 갱신이 실패하면 같은 실행을 재시도해 명령을 중복 적용하지 않고 보고서를 복구할 수 있습니다.

보고서에서 **승인 준비 완료**를 확인합니다: 양쪽 AI 상태 `provided`, 명시적인 주지표, 선택한 평가 항목에 `blocked` 없음.
이후 사람이 **Ready for review → Merge**합니다. 자동 승인·병합은 없습니다.
병합된 commit의 파일·선택·지표 매개변수·데이터/모델 digest·해시·병합자를 읽어
`research/approved/issue-{번호}/{merge-SHA}.json`에 새 불변 승인 기록을 만듭니다.
동일 기록은 재생성하지 않고 다른 내용으로 덮어쓰지 않습니다. 동시 변경·브랜치 보호는 우회하지 않습니다.
대기/차단/주지표 미선택 상태의 병합은 승인 기록 생성에 실패하며, 기록이 없으면 후속 평가 승인이 아닙니다.

승인 범위는 **교육용 계획 확정**입니다. 환자 중복·validation 개발 재사용·확인 불가 이력을 보존하며,
모델 실행·지표 계산·누수 없는 독립 임상성능·임상 사용 승인을 뜻하지 않습니다.

## 입력 프로필과 앞단 매핑

입력 계약은 `config/input-profiles.json`, 읽기·검증·표준화는 `app/input_contracts.py`에서 관리합니다.
현재 `diabetes_prepared`는 검증된 9개 컬럼 매핑을 재사용합니다.
**매핑을 다시 추측하지 않는다는 뜻이지 앞단 AI를 생략한다는 뜻은 아닙니다.**
알려진 스키마도 AI가 공개 목적·입력 계약·점검 구성을 먼저 확인한 뒤 Python EDA를 요청합니다.

```text
Issue → 고정 이미지 무결성·스키마 확인
  → EDA 미실행 draft PR → workflow_run → AI 공개 목적·스키마 조율
  → bindings 있음: 검증된 config 재사용
  → bindings null: AI 스키마 매핑 추천
      → Python이 등록 컬럼·의미 후보·필수 역할·근거·hash 검증
  → AI 조율안 → Python 검증·표준 context·실제 EDA
  → AI 목적/전체 EDA 기반 지표 추천 → 같은 PR1·자연어 피드백·사람 승인
```

앞단 AI에는 공개 연구 텍스트 4필드와 등록 컬럼명·타입·설명·의미 후보·결측 집계·정답 정의·공개 코드북을 제공합니다.
환자 행·환자 키 값·원자료·인증정보는 제공하지 않습니다.
AI는 `bindings`, 역할별 한국어 `reasons`, 허용 `evidence`, 스키마 `context_hash`를 돌려줍니다.
컬럼이 없거나 환자 키의 의미 근거가 없으면 검증 실패로 멈추며 입원 ID를 환자 ID로 추정하지 않습니다.
AI가 추천했다고 임의 코드·SQL·변환식을 실행하지 않습니다.

후단 AI는 공개 목적·현재 선택·필수와 선택 EDA의 전체 실제 집계·카탈로그로 블록을 추천합니다.
이 공개 텍스트/전체 EDA는 runner의 transient `RESEARCH_CONTEXT.json`·`AI_REQUEST.json`으로 전달합니다.
커밋하는 `recommendation-request.json`은 원문을 제외한 구조화 요청이며 조율 hash로 결합합니다.
두 단계의 context hash는 다르며 입력 매핑 hash를 후단 요청에도 묶어 오래된 추천을 막습니다.
`input-schema.json`은 공개 요청, `input-mapping.json`은 검증된 바인딩·원래 추천·프로필·출처 명세입니다.
Merge 승인 기록은 이 매핑을 병합 commit의 registry와 다시 대조하고 함께 보존합니다.

등록 프로필의 `bindings`는 표준 역할→원본 컬럼, `split_values`는 원본 분할값→train/validation/test,
`target_values`는 원본 정답값→0/1입니다. 현재 split·정답 인코딩은 변환식을 생성하지 않고 등록된 값만 사용합니다.
새 대응 모델의 확률 출력 계획은 운영자가 `probability_output_planned: true`로 명시합니다.
미선언 시 후단 평가 계획의 확률 입력을 존재한다고 가정하지 않습니다. 실제 모델 호환성 검증은 4단계의 별도 범위입니다.

### 지원 범위와 경계

기존 Diabetes 이미지는 원래 `prepared-data-only` v2 무결성 검증을 그대로 사용합니다.
다른 등록 입력은 `registered-prepared-input` v1 계약을 사용합니다:
`metadata.csv`, `db_features.csv`, `data_manifest.json` 세 파일과 등록된 SHA256·data_id·변수 순서·분할 행수를 검증합니다.
data_id는 manifest의 data_id 필드를 제외한 정규화 JSON hash이며 registry의 값과 일치해야 합니다.
특성 테이블은 `row_key,split,row_position,feature_values` 형식의 준비된 유한 숫자 배열입니다.
식별자·분할·정답 역할의 원본 컬럼이나 표준 역할명을 입력 특성에 포함하면 거부합니다. 자동으로 특성을 삭제하거나 모델을 바꾸지 않습니다.
원본 metadata의 컬럼 이름은 달라도 되지만 의미 사전과 역할별 서로 다른 컬럼, 세 분할·이진 정답 계약은 필요합니다.
운영자는 새 이미지·profile·data_id·대응 모델·출처/라이선스를 등록해야 합니다. profile 추가가 이미지 생성·배포를 대신하지 않습니다.

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

폼 계약 검사에는 `requirements-workflow.txt`, 전체 이전 ML 회귀에는 `requirements-ml.txt`가 필요합니다.
새 검사는 합성 자산과 모의 Docker 호출만 사용합니다.
기존 `WORKSHOP_DATA_IMAGE`, `WORKSHOP_MODEL_IMAGE`, `WORKSHOP_PREPARED_DIR`를 지정하지 않아 실제 container 검사 4개는 skip을 유지합니다.
로컬 Docker/Desktop을 켜지 않습니다. 소스 검증과 실제 GitHub DB 스모크·클라우드 게시 성공은 별개입니다.

## 출처

[UCI Diabetes 130-US Hospitals](https://archive.ics.uci.edu/dataset/296/diabetes+130-us+hospitals+for+years+1999-2008),
John Clore, Krzysztof Cios, Jon DeShazo, Beata Strack.
DOI [10.24432/C5230J](https://doi.org/10.24432/C5230J), [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
준비 데이터와 모델의 고정 HF source 참조는 `config/prepared-source.json`에 기록합니다.
