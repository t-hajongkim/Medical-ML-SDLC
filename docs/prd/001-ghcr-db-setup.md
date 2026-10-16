# 준비 데이터·모델 자산 GHCR 게시 준비

## 최신 후속 승인: 조합형 평가 계획·댓글·PR1 승인과 템플릿 반영 (2026-10-16)

최신 범위는 기본 + 실제 Copilot 추천 + 사용자 추가/제외의 조합형 복구와 3단계 사람 승인까지다.
추가 승인으로 입력 profile·config/조건부 AI 스키마 매핑도 포함한다. 매핑 검증 전 EDA를 실행하지 않는다.
후속 명시 승인으로 알려진 config도 AI 목적·입력 조율 후 EDA를 실행하고 PR1 자연어 피드백을 지원한다.
Target 폼은 유지하고 공개 목적 4필드·현재 선택·허가된 미반영 피드백을 transient AI 문맥에 제공한다.
후단 추천은 공개 목적과 실제 전체 EDA에 근거한다. 환자 행·식별자 값·인증정보는 AI에 제공하지 않는다.
사수님과 같은 workflow_run 체인·정확한 대상 전달을 사용하며 봇 역할 예외나 PAT는 추가하지 않는다.
아래 2026-10-15 항목은 각 단계 당시의 승인 기록이며 최신 범위는 이 절을 우선한다.

1. EDA 점검과 결과 지표를 각각 카탈로그/독립 계산 블록으로 분리한다. 전자는 데이터, 후자는 예측 결과 입력이다.
2. 기본/필수 정책·사람 선택·실제 AI 파일 소유를 구분한다. 필수 EDA 보호, alias 정규화, 선택·주지표·매개변수 보존.
3. gh-aw Copilot native 인증을 사용해 공개 연구 목적·스키마·집계/등록 ID/근거를 처리한다. 별도 AI Secret·가짜 기본 AI 추천은 없다.
4. 자연어/명령 댓글의 실제 사람 write/maintain/admin 권한을 검증한다.
   자연어는 AI 조율안→Python 검증으로 같은 PR에 반영한다. 등록 ID·매개변수만 변경하며 원문을 코드로 실행하지 않는다.
   ID·본문 hash·작성자·반영 상태를 보존하며 원문/권한 변경과 미반영 피드백은 게시/승인 기록을 막는다.
5. 추천 조합 완료와 승인 준비를 구분한다. AI provided·명시적 주지표·blocked 없음 확인 후 사람이 Merge한다.
6. 병합 SHA의 입력/계획/이미지/해시/선택/제한을 불변 교육용 승인 기록으로 추가한다.
   자동 승인·병합·모델/지표 실행은 없고 실패·미확정 병합은 승인 기록을 만들지 않는다.
7. 로컬 회귀/컴파일/정적 검사 후 Medical-ML-SDLC 템플릿에만 코드와 PR 생성 허용 설정을 반영한다.
   사용자가 복사본의 설정·실제 파이프라인 확인을 직접 하고 그 뒤 코드 리뷰한다.
   템플릿에서 실제 Issue/Copilot/승인 시연이나 수동 workflow dispatch를 하지 않는다.

제외: 복사본 자동 동기화·설정 변경, 개인 DATA 초기화, 새 학습, 최종 모델 추론·평가·Pages.

## 이전 후속 승인: Issue→EDA→연구검토 draft PR 준비 (2026-10-15)

사용자 요청: 연결 YML까지 같이 구현하고 완료·검증 후 사용자 코드 리뷰를 요청한다.
범위는 **로컬 통합 구현·검증**이며 원본/독립 복사본의 원격 반영·dispatch·실제 PR 생성 승인이 아니다.

요구/완료 기준:
1. 현재 5필드 폼의 Issue opened와 유지관리자 수동 issue_number 입력을 처리한다.
   목표/질문·section signature·동의는 검사하고 선택 값은 비어도 된다. 다른 Issue·외부 작성자는 이유 있는 제외/수동 보류로 처리한다.
2. default branch 코드와 config의 고정 data_image만 사용한다. Issue 자유문구/사용자 ref/URL을 코드로 실행하지 않는다.
3. Public 이미지 준비파일을 create/cp로 추출해 기존 manifest 검증→7 EDA 블록을 호출한다.
   모델/추론·새 분할/전처리·DB 기동은 없다. 생성 컨테이너·소유 runtime 폴더만 정리한다.
4. 입력 손상/명령 오류는 실패하되 유효 데이터의 failed/unverifiable 판정은 draft 검토 PR을 막지 않는다.
5. Issue 번호 기반 reports 경로에 요청 설정·집계·Markdown 표·평가 계획 추천만 생성한다.
   provenance와 각 상태/한계, 실제 분모/사건 수를 보존하고 개별 키·원자료·예측 행을 PR/Artifacts에 넣지 않는다.
6. ROC-AUC/AP/Brier/보정곡선/임계값 지표는 이유와 필수입력만 제안한다.
   주지표/threshold 미확정, DCA 요건 부족 보류, 비교모델 없는 NRI 적용 불가. 지표 계산/threshold 최적화는 하지 않는다.
7. 같은 Issue의 고정 branch/열린 PR을 갱신하며 댓글/사용자 변경은 보존한다.
   closed/merged PR 보류, 무변경 commit 없음, force push/amend/자동 merge 없음.
8. GITHUB_TOKEN 최소 권한과 수동 권한 확인을 사용하고 새 자격증명/전역 설정 변경을 추가하지 않는다.
   Actions PR 생성 허용 설정·token-created PR의 다른 CI trigger 제약을 전제로 알린다.
9. 합성/mock 회귀·기존 회귀·실제 EDA를 재사용한 로컬 payload·YAML/actionlint/Bash 구문을 검증한다.
   실제 GitHub 이벤트/이미지 추출/push/PR 검증 미실행을 구분한 뒤 사용자 리뷰로 넘긴다.

## 후속 승인: 2단계 EDA 블록 준비 (2026-10-15)

사용자의 "1단계 완료되었으면 2단계 블록들 준비 시작해줘" 승인을 따른 로컬 구현·검증 범위다.
기존 3개 overlap/demographics/schema를 현재 validation 분할·원본 인종 결측·타깃·숫자 품질에 맞춰 보강하고,
cohort/prediction_timing/label_observability/preprocessing_lineage 4개를 독립 파일과 기존 catalog에 추가한다.

완료 기준:
- 검증된 prepared/assets를 입력받아 7개 블록을 호출하고 집계 JSON을 생성한다. stage1 검증을 재사용하며 모델 실행은 없다.
- strict target/bool, validation/명시적 valid alias, unknown split/없는 키/순서·길이 불일치/0행을 합성 회귀로 검사한다.
- 101,766행의 known overlap 5,055/5,086/1,760와 원본 race 결측 1,596/336/341를 보존한다.
- 입원/환자·원본/대치 race·선정 설명/행별 근거·label 인코딩/추적·hash/fit 이력을 각각 분리한다.
- 예측시점/변수 가용성·추적 종료/외부병원 포착·113-feature 역사적 fit 범위가 없으면 unverifiable로 남긴다.
- 보고서 생성 성공은 연구 적합성 passed가 아니다. 개별 키/행 출력·자유문구 실행·임의 집단/제외 수/통계 기준 생성은 금지한다.

제외: 지표 빌딩 블록·모델 load/fit/predict·추가 scaler·재분할·변수/환자 삭제·이미지 재게시·Docker·Action dispatch·commit/push.
당시 Issue/PR 자동 호출은 미연결이었다. 최신 후속 승인으로 로컬 연결을 추가하되 원격 활성은 리뷰 후다.
기존 1단계 workflow/Issue Form/config/source hash/Dockerfile은 수정하지 않는다.

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
