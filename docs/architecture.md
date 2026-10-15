# GHCR 자산 게시 구조

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
| `.github/ISSUE_TEMPLATE/research-request.yml` | 연구 요청 폼. 자동 EDA·PR1 미연결 |
| `config/assets.json` | data/model digest 둘 다 현재 null. 게시 후 사람이 입력 |

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
실제 Docker/게시/Action dispatch는 사용자 검토 뒤 수행한다.

## 해석 및 후속 경계

저장 checkpoint는 sklearn 1.2.2, 기존 수동 검증 환경은 1.6.1이며 버전 경고 아래 test 지표를 재현했다.
현재는 준비 CSV 실습용 조건부 등록만 한다. 원본 입력 파이프라인·실행 추론 이미지·임상 평가를 완성한 것이 아니다.
공개 validation의 개발 재사용, 환자 중복, target encoding/역사적 113 변환 불확실성 때문에 독립·누수 없는 holdout 주장을 하지 않는다.

참가자 초기화/개인 패키지, Issue→EDA 블록→결과·지표 추천 PR1, PR2, 최종 모델 평가, Pages는 후속 범위다.
폼 자유 입력은 연구 요청 데이터이지 임의 Python/SQL 실행 코드가 아니다.
