# 준비 데이터·모델 자산 GHCR 게시 준비

## 현재 승인된 범위 (2026-10-15)

사용자 요청: GHCR 기초 셋업을 구현하고 알려주며, 문제 없는 검증 모델도 YML에 적재한다.
여기서 적재는 **고정 파일 포장·공통 레지스트리 게시 준비**다. 새 학습·전처리 fit·모델 로딩·predict를 뜻하지 않는다.
최초 단계는 로컬 구현·검증만 수행했다. 이후 2026-10-15 사용자의 "오케이 등록해주고" 승인으로
셋업 commit/push, GitHub-hosted Action dispatch, 실제 DB/모델 파일 검증과 GHCR 등록을 수행했다.
로컬 Docker·새 모델 학습·모델 재실행은 여전히 금지한다.
Issue Form 한 파일의 별도 승인 게시와 GHCR 구현/게시 승인은 구분한다.

### 현재 요구

1. **Establish Database Image** 표시 이름과 workflow_dispatch를 유지한다.
   GitHub-hosted runner, contents read / packages write, `github.repository` 기반 공통 `-db`/`-model` 이름을 사용한다.
2. `config/prepared-source.json`의 immutable HF revision에서 `splits/`의 train/validation/test와 demographics를 받는다.
   rows 71,236/15,265/15,265, 113 숫자 X + target, 고정 bytes·SHA·열 순서를 검사한다. root CSV를 선택하지 않는다.
3. 고정 UCI raw SHA `f792c388d9b470aac4fbf21c39b176534d55a116e7626881c30bbe7ab0341422`로
   encounter 키·target·인구집단·행 순서를 대조하고 patient 키를 복원한다. 원본은 빌드 context/Git에 넣지 않는다.
4. 이미 준비된 숫자 값을 변경하거나 재분할하지 않는다. `.fit()`·scaler 적용·모델 predict는 호출하지 않는다.
   target은 원본 `<30`만 1이고 `>30`/`NO`는 0이다.
5. metadata에 race_original/race_was_missing을 보존한다.
   공개 sidecar의 Caucasian 대치값을 실제 관측값으로 숨기지 않는다. test 원본 race 결측 341개를 검증한다.
6. 알려진 환자 중복 train–test 5,086 / train–validation 5,055 / validation–test 1,760을 그대로 기록하고 대조한다.
   DB 입력 계약은 model pair가 아니라 준비 데이터·metadata·data_id다. 독립 patient-disjoint split을 요구하지 않는다.
7. 데이터 이미지는 PostgreSQL init/healthcheck와 준비 CSV·metadata·source manifest를 담는다.
   runner에서 실제 DB의 모든 metadata/feature/asset_info 값을 검증한 뒤 게시한다. host 포트 매핑은 하지 않는다.
8. 모델은 고정 HF revision의 4,018-byte `logistic_regression.pkl`,
   SHA `f923be3373fbd958df6f1d9a0694fc4a36c6727f6c48090637be84ed56e9e823`만 조건부 등록한다.
   별도 `FROM scratch` 파일 이미지에 checkpoint·명세·MIT source README·NOTICE만 담는다.
9. 모델 명세는 이미 표준화된 ordered 113 X, classes 0/1, positive <30, 원본/체크포인트 SHA,
   sklearn 저장 1.2.2 / 수동 검증 1.6.1 버전 경고와 준비 CSV용 조건부 사용 한계를 명시한다.
   저장 artifact를 이번 publisher에서 역직렬화하거나 실행하지 않는다.
10. 모델 이미지 검사는 filesystem 추출·존재/해시/manifest 일치로 수행한다. 컨테이너 추론 가능성을 주장하지 않는다.
    120-feature scaler·새 실행 런타임·원본 입력 추론 파이프라인을 임의로 추가하지 않는다.
11. 데이터/모델 이미지를 별도 공통 GHCR 패키지에 게시하며 두 digest를 Summary에 출력한다.
    config에 가짜 digest를 적거나 원격 파일을 자동 commit하지 않는다. Public 전환과 실제 참조 입력은 운영자 작업이다.
12. 공개 validation의 개발 재사용·환자 중복·source target encoding/과거 113 변환 이력 불확실성을 기록한다.
    누수 없는 진정 독립 holdout, 임상성능 검증 완료 또는 무조건 모델 호환 주장을 하지 않는다.
13. Git에는 코드·명세·집계·참조만 둔다. 원본·준비 CSV·모델 바이너리·환자 개별 행을 Git/로그/Issue에 넣지 않는다.
14. CI는 새 source-only 합성 테스트와 기존 회귀를 검사한다. 로컬 Docker skip 4개를 늘려 성공을 흉내내지 않는다.
    기존 pair/inference 초안이 현재 artifact 계약과 다른 점을 명시하고 참가자 초기화 job은 비활성화한다.
15. 연구 요청 폼은 목표·질문·대상/관심 집단·추가 요청·민감정보 미포함 확인을 받는다.
    자산·schema·target·digest는 시스템 명세에서 읽을 후속 설계이며 사용자 직접 입력을 요구하지 않는다.
    폼 자유 입력은 임의 Python/SQL로 실행하지 않는다.

### 현재 완료 기준 / 제외

- 고정 실제 로컬 자산의 SHA/bytes/rows/schema/원본키 연결·known overlap/원본 결측 검증과 자산 포장이 통과한다.
- 합성 테스트에서 손상·행/열/target/키 불일치가 명시적으로 실패하며, 새 publisher가 fit/load/predict를 부르지 않는다.
- YML/내장 셸/COPY 경로와 필수 문서가 일치한다.
- source-only 성공과 실제 runner 검증·게시를 구분해 보고한다.
  [실제 run 38722808263](https://github.com/t-hajongkim/Medical-ML-SDLC/actions/runs/38722808263)에서
  DB 전체 대조·모델 filesystem 검사·두 push가 성공했고 Public 및 익명 조회를 확인했다.
  실제 digest는 `config/assets.json`에 등록하며 이미지 빌드 source commit
  `5c0fc144687766c0306e8b7150f3228579ac7a0f`를 후속 참조 기록 커밋과 구분한다.
- 참가자 초기화/개인 DATA 패키지, 실제 모델 추론 연결, Issue→EDA→PR1, PR2, 최종 모델 평가, Pages는 제외한다.

## 아래는 이전 CKD 요구 기록 (현재 입력·실행 계약 아님)

> **이전 CKD 범위의 요구 기록입니다. 현재 실행 지침이 아닙니다.**
> 현재 실습은 위의 Diabetes 준비 데이터·조건부 모델 파일 자산 게시이며 새 학습·Foundry를 제외합니다.
> 최신 배치·워크플로 전제·현재 게시 경로와 이전 학습/pair 초안의 구분은
> [README](../../README.md)와 [architecture.md](../architecture.md)에 명시했습니다.
> 기존 요구를 지우거나 현재 자산 준비가 완료됐다고 바꾸지 않고 보존합니다.

## 승인된 범위

2026-10-15 사용자의 "코드 작성하고 나한테 알려줘 셋업 준비 끝내고 내가 확인할게" 요청에 따른 로컬 셋업 준비다.
사용자가 검토·커밋·푸시·최초 게시를 직접 진행한다. 원격 이슈 생성, 커밋, 푸시, GHCR 게시와 공개 범위 변경은 이번 작업에서 실행하지 않는다.

## 요구사항

1. 사용자가 의도적으로 삭제한 `data/ckd_raw.csv`를 Git에 복원하지 않는다.
   운영자 수동 게시 Action의 runner 임시 공간에서만 원본을 다운로드한다.
2. 공개 UCI CKD 원본의 400행·24개 입력 변수·정답 1열을 검증한다.
3. PostgreSQL 이미지는 원본 CSV와 초기화 SQL·출처를 포함한다. 모델 체크포인트는 포함하지 않는다.
4. 원본 값의 문자열 표현을 보존하는 `raw.ckd` 테이블에 CSV를 적재한다.
   원본 결측 기호 `NaN`만 SQL `NULL`로 표현한다. 공백·탭·라벨·숫자 문자열을 정리하거나 대체하지 않는다.
5. 원본 행 순서에 따른 `row_id`를 추가한다. 환자 식별자로 해석하지 않는다.
6. DB 암호를 이미지나 코드에 넣지 않는다. 실행할 때 PostgreSQL의 환경변수로 전달한다.
7. 계정·저장소 이름을 소문자로 변환한 `ghcr.io/<owner>/<repository>-db`에 게시한다.
   후속 참가자 결과용 `-data` 이름도 같은 검증 함수를 사용하도록 준비한다.
8. GHCR 게시 워크플로는 `workflow_dispatch`로만 실행한다.
   단위 검사·실제 이미지 검사가 성공해야 `GITHUB_TOKEN`으로 게시한다.
9. 자동 CI는 원본 없이 소스·모의 다운로드 검사를 수행하며 외부 데이터 다운로드·이미지 빌드·게시는 하지 않는다.
   실제 원본·이미지·DB 검증은 운영자 수동 게시 Action에서 수행한다.
10. Public 저장소 + Public GHCR로 운영한다. 게시 결과에 태그·digest·원본 검증 요약을 표시한다.
    GitHub의 신규 패키지는 기본 Private이므로 최초 Public 전환은 사용자가 수행한다.
11. 워크숍과 실제 DB 검증은 로컬 Docker 없이 GitHub Actions에서 실행한다.
12. 수동 게시 Action 이름은 `Establish Database Image`다.
    수동 게시의 한 job에서 소스 검사 → 임시 원본 준비 → 빌드 → DB 대조 → 게시를 수행한다.
13. 원본 다운로드는 등록된 UCI URL·고정 크기·체크섬·스키마·행·결측 개수로 검증한다.
    실패한 파일은 남기지 않고, 기존 컨텍스트나 저장소 내부에 데이터를 쓰는 요청은 거부한다.
14. 참가자는 게시된 공통 GHCR 이미지를 사용한다. UCI 다운로드는 운영자의 원본 이미지 준비용이며
    참가자 실습의 원본 획득 경로로 사용하지 않는다.

## 이번 범위가 아닌 것

- 참가자별 초기화 Action 실행 흐름, 전처리 결과 패키지의 실제 게시.
- 전처리·분할 생성·ML 학습·체크포인트 등록·Foundry 추론.
- 마스킹 뷰·LLM 접근 권한·실제 병원 데이터의 보안 설계.
- 별도 GitHub Releases 단계.

## 완료 기준

- 원본 CSV가 Git에 없고, 다운로드된 원본의 체크섬이 고정 명세와 일치해야 이미지를 만든다.
- 유효하지 않은 이미지 이름·태그는 명시적인 오류로 거부한다.
- GitHub Actions에 실제 DB와 원본 CSV를 모든 행·열에 걸쳐 대조하는 검사를 배치한다.
  이 준비 작업에서는 원격 CI 실행을 완료했다고 주장하지 않는다.
- 400행·결측 1,012개·원본 탭이 남아 있는 라벨 2개가 보존된다.
- 같은 버전의 이미지를 다시 실행해도 같은 원본 DB가 만들어진다.
- 모델 파일·개발 코드·Git 이력이 이미지 빌드 컨텍스트에 포함되지 않는다.
- 사용자에게 검토 가능한 로컬 변경으로 남기고 외부 게시를 수행하지 않는다.
- 원본 없이 소스 검사가 통과하며, 원본 다운로드 실패·변조·잘못된 저장 경로는 명시적 오류로 중단된다.
