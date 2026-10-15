# Medical ML SDLC Workshop

현재 구현 범위는 **준비된 Diabetes 데이터와 검증된 모델 파일을 운영자 공통 GHCR 패키지로 게시할 기초 셋업**입니다.
게시 YML은 새 학습·전처리 fit·모델 로딩·predict를 실행하지 않습니다.
GHCR은 이미지 레지스트리이지 실행 중인 DB가 아닙니다.

## 먼저 볼 YML 3개

| 파일 / Actions 표시 이름 | 역할 | 현재 상태 |
|---|---|---|
| [publish-db-image.yml](.github/workflows/publish-db-image.yml) / **Establish Database Image** | 고정 공개 자산 검증 → DB 이미지 빌드·스모크 → 모델 파일 이미지 빌드·무결성 검사 → 두 공통 GHCR 게시 | [2026-10-15 실제 게시 성공](https://github.com/t-hajongkim/Medical-ML-SDLC/actions/runs/38722808263). 두 패키지 Public·익명 조회 확인 |
| [initialize-workshop.yml](.github/workflows/initialize-workshop.yml) / **Initialize Workshop** | 이전 데이터/모델 pair 기반 참가자 초기화 초안 | 현재 계약과 호환되지 않아 첫 step에서 명시적으로 중단. 쓰기 권한 제거. 실행하지 않습니다 |
| [ci.yml](.github/workflows/ci.yml) / **Check workshop setup** | 현재 자산 계약과 이전 코드의 합성 fixture 회귀 검사 | 외부 자산 다운로드·Docker·GHCR 게시 없음 |

운영자는 첫 번째 YML을 검토합니다. 참가자 개인 패키지·초기화·PR2·최종 모델 평가·Pages는 이번 범위가 아닙니다.
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

## Issue Form

[research-request.yml](.github/ISSUE_TEMPLATE/research-request.yml) 폼은 작성됐습니다.
연구 목표·질문, 대상 조건, 추가 요청, 공개 데이터/민감정보 미포함 확인을 받습니다.
인구집단 선택 입력은 두지 않고, 제공된 연령·성별·인종을 기본 점검에 사용합니다.
현재 Diabetes 목적·EDA 질문·전체 집단을 기본값으로 제안하되 사용자가 수정할 수 있습니다.
113개 컬럼·target·checkpoint·GHCR digest를 직접 입력시키지 않으며 후속 연결에서는 시스템 명세/설정에서 읽을 계획입니다.

**폼 제출 → 자동 EDA → 결과·지표 추천 초안 PR1 연결은 아직 구현하지 않았습니다.**
사용자의 지표 요청을 자동 확정하거나 자유 입력을 임의 Python/SQL로 실행하지 않습니다.

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

새 검사는 합성 자산과 모의 Docker 호출만 사용합니다.
기존 `WORKSHOP_DATA_IMAGE`, `WORKSHOP_MODEL_IMAGE`, `WORKSHOP_PREPARED_DIR`를 지정하지 않아 실제 container 검사 4개는 skip을 유지합니다.
로컬 Docker/Desktop을 켜지 않습니다. 소스 검증과 실제 GitHub DB 스모크·클라우드 게시 성공은 별개입니다.

## 출처

[UCI Diabetes 130-US Hospitals](https://archive.ics.uci.edu/dataset/296/diabetes+130-us+hospitals+for+years+1999-2008),
John Clore, Krzysztof Cios, Jon DeShazo, Beata Strack.
DOI [10.24432/C5230J](https://doi.org/10.24432/C5230J), [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
준비 데이터와 모델의 고정 HF source 참조는 `config/prepared-source.json`에 기록합니다.
