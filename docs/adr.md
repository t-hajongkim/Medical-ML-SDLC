# 현재 결정 기록

## 2026-10-15: 고정 준비 데이터 + 별도 조건부 모델 artifact 게시

- 최신 승인: GHCR 기초 셋업을 구현하고 검증된 모델도 YML에 적재하되 학습·추론을 게시 작업에 넣지 않는다.
- 선택: 기존 hash/UCI/GHCR 도우미와 DB 경계를 재사용하고 `app/prepared.py`·`config/prepared-source.json`·
  `tests/test_prepared_source.py`로 현재 자산 계약만 분리한다. 임의 새 모델·데이터 분할·런타임은 추가하지 않는다.
- 데이터: immutable HF `splits/` 파일을 bytes/SHA/행/열/finite/target/metadata로 검증하고 그대로 보관한다.
  고정 UCI 원본을 encounter에 연결해 patient 키와 인종 원본 결측 정보를 복원한다. raw CSV는 이미지/Git에서 제외한다.
- 분할: 알려진 train/test 5,086, train/validation 5,055, validation/test 1,760 환자 중복을 숨기지 않는다.
  DB는 중복 0 대신 원본 집계 일치를 검사한다. 공개 validation 재사용과 source target encoding 한계를 문서화한다.
- 모델: 고정 113-feature LogisticRegression pickle과 MIT source README를 해시 검사만 해 조건부 등록한다.
  `app/Dockerfile`을 **FROM scratch 파일 보관 이미지**로 정리한다. 모델 로딩/predict/fit와 120-feature scaler는 제외한다.
- 근거: 수동 로컬 test 지표 재현은 이미 확인했지만 sklearn 1.2.2→1.6.1 경고와 113 입력 원본 변환 이력 미해결이 있다.
  따라서 준비 CSV 실습용 등록이지 독립 임상성능 인증이나 원본 입력 추론 완성이 아니다.
- 현재 manifest: data schema 2 / prepared-data-only, model schema 2 / model-artifact.
  기존 schema 1 pair_id/학습 전처리기/환자 독립 분할 검사를 몰래 생략하지 않고 새 게시 입력 계약을 명시한다.
- runner 검증: 실제 DB 전 행·값 대조와 model filesystem 모든 파일/명세 검사로 분리한다.
  모델 container는 create/cp만 사용하며 실행하지 않는다. 실제 검증/게시를 로컬에서 실행하지 않는다.
- 게시: 공통 -db / -model 두 패키지와 고정 digest Summary. 최초 Public 전환·config 참조 입력은 사람이 담당한다.
  게시 YML은 stdlib만 쓰고 최소 contents read/packages write 권한을 유지하며 자동 remote commit하지 않는다.
- 초안 보존: 기존 학습/pair/infer 코드·회귀 테스트는 제거하지 않는다.
  Initialize Workshop은 현재 artifact 계약과 호환되지 않아 첫 step에서 명시적으로 중단하고 쓰기 권한을 제거했다.
  참가자 초기화/모델 실행 연결/Issue→EDA→PR1/PR2/평가/Pages는 후속 결정이다.
- Issue Form은 부모 작업으로 작성·별도 게시됐다. 자산 명세를 폼에서 직접 입력받거나 자유 입력을 코드로 실행하지 않는다.
- 상태: 실제 로컬 자산 수입/포장·source-only 검증과 YAML/셸/경로 점검까지만 완료한다.
  실제 Docker/GHCR 게시/Action dispatch/이 GHCR 코드의 commit·push는 미실행이다.

## 아래 이전 정리·CKD 결정은 최신 항목으로 대체된 이력

## 2026-10-15: 역할별 파일 배치와 생성물 정리

- 요청: 늘어난 파일 구조를 `medical-sdlc`의 역할별 배치 원칙을 참고해 정리한다.
- 선택: 데이터 이미지 정의는 `db/Dockerfile`, 추론 이미지 정의는 `app/Dockerfile`로 이동한다.
  준비 코드의 복사 원본만 바꾸고 생성 컨텍스트 이름·COPY·Python import·기존 검사 계약은 유지한다.
- 유지: 독립 검사 블록, 세 워크플로, 출처·고정 명세, 테스트, 요구·구조·결정 문서.
  역할이 다른 두 Dockerfile은 중복이 아니므로 합치지 않는다. 새 기능 폴더·파일은 추가하지 않는다.
- 제거: 확인된 네 `__pycache__` 디렉터리의 `.pyc` 생성물만 제거한다.
  기존 ignore는 유지하고 `-B`·`PYTHONDONTWRITEBYTECODE=1`로 검증 시 캐시 재생성을 막는다.
- 근거: 원격 참조의 `db/` 경계를 적용하되 이 실습에 없는 논문 자동화·compose·대시보드를 복제하지 않는다.
- 미해결: 실제 Diabetes 호환 자산 미확보, 공통 이미지 참조 `null`,
  운영자 `.fit()` 경로와 추론 전용 확정 설계의 충돌. 이 정리에서 기능 삭제·새 자산 입력 방식은 결정하지 않는다.
- 검증 경계: 합성 source-only 회귀 검사와 YAML·셸·경로 점검. 실제 데이터 학습·Docker·게시·commit/push 없음.

## 이전 범위의 결정 (현재 실행 지침 아님)

아래 CKD 원본 DB·Foundry 결정은 이전 설계의 근거를 남긴 것이다.
현재 대상은 Diabetes이며 Public GHCR의 준비된 분할·호환 모델을 사용하는 추론 전용 실습이다.
현재 배치는 `architecture.md`, 실행 전제·미해결 상태는 루트 README를 따른다.
특히 아래의 `raw.ckd`, `NaN` 적재 규칙, Foundry 체크포인트 경로를 현재 구현 완료로 해석하지 않는다.

## 2026-10-15: 공통 CKD DB 이미지와 참가자 결과 이미지 분리

- 질문: 참가자마다 바뀌는 데이터 때문에 운영자의 원본 GHCR 이미지를 계속 갱신해야 하는가?
- 선택지: 공통 이미지 덮어쓰기 / 공개 Artifacts / 참가자별 GHCR 결과 패키지.
- 선택: 공통 원본은 운영자의 `-db` 이미지, 참가자 결과는 각자 `-data` 패키지.
- 근거: 원본 재현성과 참가자별 처리 결과를 분리한다. 결과 패키지 게시는 후속 Action에서 자동화한다.
- 결정자: 사용자와의 대화.
- 다시 볼 조건: 실제 민감 데이터 사용, 저장량·보존기간·권한 요구 변경.

## 2026-10-15: 원본 값 보존과 NaN 표현

- 질문: DB 적재 때 타입·공백·라벨·결측을 함께 정리할 것인가?
- 선택: 원본 CSV는 byte 단위로 보존하고 DB의 모든 원본 열은 `text`로 적재한다.
  결측 기호 `NaN`만 SQL `NULL`로 바꾸고 정렬용 순번을 추가한다.
- 근거: 전처리 체크리스트가 연구 단계의 변경을 명시적으로 다뤄야 한다.
- 결정자: 승인된 셋업 요구에 따른 구현 선택.
- 다시 볼 조건: 새 데이터 버전·스키마·결측 기호 도입.

## 2026-10-15: 검사는 GitHub Actions, 게시는 사용자 수동 실행

- 질문: 로컬 Docker로 이미지를 실행해 확인할 것인가?
- 선택: 로컬 Docker 실행은 하지 않는다. 실제 DB 검증은 GitHub-hosted Actions에 배치한다.
  소스 계약 검사는 별도로 수행하고, 원격 검사·게시는 사용자의 커밋·푸시 후 실행한다.
- 근거: 사용자가 로컬 Docker 사용을 원하지 않으며 최초 게시를 직접 확인·실행한다.
- 결정자: 사용자.
- 다시 볼 조건: 사용자가 별도의 검증 환경을 명시적으로 승인할 때.

## 2026-10-15: 체크포인트 제외

- 선택: 모델 체크포인트는 Foundry 쪽에 두며 이 DB 이미지·빌드 컨텍스트에서 제외한다.
- 근거: 사용자가 정한 모델 저장·추론 경로를 유지한다.
- 결정자: 사용자.
- 다시 볼 조건: 지원되는 커스텀 모델 등록·배포 경로를 검토할 때.

## 2026-10-15: Establish Database Image와 단일 job

- 질문: 순차 작업마다 새 runner를 준비해야 하는가? 검사를 줄일 수 있는가?
- 선택: 수동 게시 Action을 `Establish Database Image`로 바꾸고 CI·수동 게시 모두 각자 한 job으로 구성한다.
  같은 실행 안의 checkout·Python 준비는 한 번만 수행한다.
- 검사 범위: 소스 테스트가 CSV 검증을 포함하므로 중복 CLI 단계는 제거한다.
  실제 DB 기동·원본 전체 대조는 별도 실패 지점이어서 유지하고 실패 시 게시하지 않는다.
- 근거: 작업 분리로 인한 runner 시작·환경 준비 중복을 줄이되 게시 전 검증은 유지한다.
  초기에는 자동 CI와 수동 게시 각각에서 이미지를 검사하도록 했다.
  이 부분은 아래의 CSV 임시 다운로드 결정으로 대체되어, 실제 이미지 검사는 수동 게시에만 남긴다.
- 결정자: 사용자 요청에 따른 구현.
- 다시 볼 조건: 실제 실행 시간 측정 후 이미지 재사용·캐시가 필요하거나 검사 job의 권한 분리가 우선될 때.

## 2026-10-15: CSV는 Git에 두지 않고 운영자 게시 때만 준비

- 질문: 원본 CSV가 삭제됐을 때 Git에 복원할 것인가?
- 선택지: Git 복원 / 참가자가 매번 UCI 다운로드 / 운영자 게시 Action의 임시 다운로드.
- 선택: 삭제를 유지한다. 운영자 수동 Action이 runner 임시 공간에서 UCI 원본을 받아 고정 체크섬으로 검증하고
  DB 이미지에 포함한다. 참가자는 미리 게시된 공통 GHCR 이미지를 사용한다.
- 근거: 원본 배포는 기존 두 사례처럼 GHCR로 수행하며 Git에는 코드·명세·출처만 둔다.
  자동 CI에는 데이터가 필요 없는 계약 검사만 남기고 실제 원본·DB 검사는 수동 게시에 모은다.
- 결정자: 사용자.
- 공개 범위: Public 템플릿 저장소 + Public GHCR로 확정했다.
  신규 패키지의 기본 Private 상태를 사용자가 최초 한 번 Public으로 바꾼다.
- 다시 볼 조건: 원본 버전·스키마 변경, UCI 배포 주소 변경, 초기화 Action의 원본 이미지 참조를 고정할 때.
