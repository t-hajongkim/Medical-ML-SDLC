# PR2: 고정 모델 평가 결과의 사람 검토

진행 갱신: 아래의 초기 “로컬 준비” 상태 이후 게시기 구현과 전체 582개 회귀 검사를 완료했다.
사용자가 원본·Test3 반영부터 새 PR1/댓글/Merge/추론/평가/PR2까지의 원격 실습 완주를 추가 승인했다.
기존 PR1 #3은 보존하고 새 코드로 계획을 생성한다. Pages·cloud agent 전환·PR2 Merge는 제외한다.
아래 “미게시/별도 승인” 문장은 초기 계약 작성 당시의 기준선이며, 추가 승인과 실제 원격 결과는 ADR·실행 이력으로 추적한다.

상태: **사용자 “PR2 준비해줘” 승인과 별도 publisher workflow 선택에 따른 로컬 준비 계약**. 이번 산출물은 PRD와 architecture의 계획 절뿐이다. PR2 게시기·테스트·workflow는 아직 구현하지 않았으며, 실제 PR2 생성·원격 실행·코드 공개를 뜻하지 않는다.

## 1. 목적과 현재 기준선

사람이 Merge한 PR1의 저장 계획으로 고정 모델 추론·지표 계산이 성공한 뒤, 그 **집계 결과만** 별도의 draft PR2로 제시한다. PR1은 교육용 평가계획 승인, PR2는 실제 계산 결과와 해석 한계의 사람 검토다. 어느 Merge도 임상 사용 승인을 부여하지 않는다.

- 선행 계약: [고정 모델 추론·기존 평가 지표 연결](2026-10-17-fixed-inference-evaluation.md).
- 로컬 `inference.py`·`model_evaluation.py`·`evaluate-model.yml`은 구현되어 있다. 기존 확인 기록은 신규 17개/전체 555개 테스트와 실제 고정 validation 15,265행 smoke다. 이는 이번 문서 작업에서 재실행한 결과도, 원격 Actions 성공 증거도 아니다.
- 원본 템플릿 `359688`과 Test3 안정 버전 `79b176e`에는 새 고정 추론·평가 코드가 아직 게시되지 않았다. 새 코드는 로컬 미커밋 상태이며 이 요청으로 Git·push·Test3 갱신을 하지 않는다.
- 전달받은 현재 상태에서 Test3 PR1 `#3`은 **OPEN draft**다. `threshold: null`이고 F1·Recall·Precision은 계획상 blocked다. 사용자는 PR1 Merge·threshold 지정·임상 승인·새 코드 원격 게시를 승인하지 않았다.
- 실제 PR2는 **사용자의 PR1 Merge와 새 코드 게시에 대한 별도 명시적 승인·반영 이후**에만 가능하다. 이 문서로 Merge하거나 workflow를 실행하지 않는다.
- PR1 피드백 지연과 요약 정확성의 두 수용 공백은 미해결 후속 항목으로 남아 있다. 해당 질문은 기존 PR1 범위 변경 승인이 아니며 이번 PR2 문서 작업으로 함께 수정하지 않는다.

이번 계약은 선행 PRD의 단일 read-only 계산 workflow와 기존 테스트 계약을 **그대로 유지**하면서, 후속 구현 시 “PR2 없음” 범위만 별도 게시 workflow로 확장한다. 학습·재전처리·재분할·새 평가계획·Pages는 추가하지 않는다.

## 2. 별도 계산·게시 workflow의 책임과 권한

기존 `.github/workflows/evaluate-model.yml`은 **하나의 read-only 계산 job인 채 변경하지 않는다**. 새 `.github/workflows/publish-evaluation.yml`이 `workflow_run`으로 이름이 정확히 `Evaluate merged research plan`인 선행 실행의 완료를 받고, 성공한 실행만 별도의 publisher 한 job에서 처리한다. 기존 workflow_run+artifact 전달 패턴을 재사용하며 새 AI workflow·중간 승인 파일은 만들지 않는다.

| 경계 | 계약 |
|---|---|
| 기존 `evaluate` 계산 job | 기존 Merge/정확한 수동 merged PR 입력, 기본 validation·명시 test, trusted default 코드 검증, 고정 DATA/MODEL, pinned Python 3.11/sklearn 1.2.2를 유지한다. `contents: read`, `pull-requests: read`, `packages: read`이며 쓰기 토큰을 받지 않는다. job 추가·새 output·기존 테스트 완화가 필요하지 않다. |
| 계산 출력 | 공개 파일은 `evaluation.json`·`report.md` **두 개뿐**이다. 기존 aggregate Summary를 유지한다. 계산·출력 검증·artifact 업로드가 모두 성공해야 게시가 가능하다. |
| 전달 artifact | 이름은 선행 계산의 run ID/attempt를 사용한 `model-evaluation-{source_run_id}-{source_run_attempt}`다. 게시 workflow 자신의 run ID를 사용하지 않는다. 검증한 선행 실행에 속한 artifact 하나만 받으며 이전/최신 run 검색·이름 prefix 검색·fallback은 없다. |
| 다운로드 전 출처 검사 | event 전달값만 신뢰하지 않고 현재 API에서 정확한 source run을 재조회한다. 동일 repo·등록 workflow ID/경로 `.github/workflows/evaluate-model.yml`·정확한 workflow 이름·완료/성공·허용 source event(`pull_request`/`workflow_dispatch`)·run ID/attempt 및 실행 코드의 신뢰성을 먼저 검증한다. 다른 경로/이벤트/repo·실패/취소·확인 불가/stale 코드는 artifact 조회·다운로드 전에 거부한다. |
| 새 게시 job | 별도 workflow의 publisher 한 job이다. 성공한 source run만 처리하며 계산 실패·취소를 우회하지 않는다. 이 workflow에만 `actions: read`, `contents: write`, `pull-requests: write`를 부여한다. actions/packages write·새 PAT/Secret·전역 쓰기 권한은 없다. |
| 게시 코드 | trusted default branch를 checkout하고 실제 40자리 `HEAD`를 고정 기록한다. `persist-credentials: false`를 유지하고 source run의 workflow/관련 코드가 trusted default 계약과 일치하는지 다운로드 전에 검사한다. artifact·사용자 ref·PR1/PR2 코드로 checkout/import/의존성을 바꾸지 않는다. Actions 참조는 고정 SHA를 사용한다. |
| 게시 입력 | artifact 루트에 정확히 두 일반 파일만 허용한다. 누락·추가 파일·중복 이름·symlink·경로 이탈·잘못된 인코딩/JSON·계약을 벗어난 크기는 게시 전에 거부한다. artifact의 셸/Markdown을 명령으로 실행하지 않는다. |

새 `app/result_review.py`는 **출처 검증·정형 보고서 생성·제한된 GitHub 게시**만 담당한다. 모델 로딩·Docker·데이터 다운로드·EDA 재계산·추론·지표 재계산·AI 호출은 하지 않는다. publisher에는 `requirements-inference.txt`·`requirements-ml.txt` 또는 sklearn/NumPy/joblib 등 ML 의존성을 설치하지 않는다. 기존 경량 workflow 의존성과 표준 라이브러리/GitHub 도우미만 재사용한다. `executor_eval`은 기존 수치 라이브러리로 남긴다.

## 3. 게시 전 검증 — 쓰기보다 먼저

선행 run/path/event/repo/success/code 검증을 artifact 다운로드보다 먼저 수행한다. artifact 목록에서도 source run/attempt·이름·유일성·만료 여부를 확인한 뒤 다음 검증을 진행한다. 이 순서를 위해 별도의 계산 job output이나 계산 workflow 수정은 요구하지 않는다.

1. 현재 GitHub API에서 정확한 PR1 번호를 다시 조회한다. `model_evaluation.validate_pull`을 재사용해 실제 merged 상태·동일 저장소의 head/base·현재 default base·`research/review-issue-{issue}`·보고서 Issue 소유권을 확인한다. API의 immutable merge SHA가 검증한 source 실행 문맥 및 JSON provenance와 같아야 한다. source event/API로 확인할 수 있는 PR 연결도 교차 검사하며, 수동 실행에서 artifact가 전하는 대상도 실제 merged PR·계획으로 검증한다. 다른 PR·fork·저장소·Issue·분할·미Merge PR은 거부한다.
2. 같은 merge SHA에서 기존 JSON 허용 목록만 `model_evaluation.read_reports`/`github_io`로 읽는다. tree/blob SHA·일반 파일·UTF-8·중복 키/비유한 수 거부를 유지한다. `validate_reports`의 저장 계획·실행계획·EDA/입력 서명·catalog/policy·DATA/MODEL digest 검증을 재사용한다. `plan_hash`는 **저장 평가계획의 canonical hash**이며 EDA 실행계획 hash와 혼동하지 않는다.
3. `evaluation.json`의 `schema_version: 1`, `kind: fixed-model-evaluation`과 허용된 필드/형식을 엄격히 검사한다. repository·PR1·Issue·merge SHA·plan code SHA·execution code SHA·**source run** URL을 현재 API/Actions 문맥·사전 검증한 코드 출처·실제 게시 checkout과 대조한다. `execution_code_commit`은 신뢰된 기본 브랜치 이력의 실제 계산 소스여야 하며 현재 publisher의 관련 코드/계약과 일치해야 한다. source run의 event head SHA를 계산기가 별도로 checkout한 execution SHA와 무조건 동일시하지 않는다. 보고서만 추가된 commit 차이는 기존 규칙대로 허용하되 관련 실행 코드/설정 차이는 stale로 중단한다. merge의 조상 관계와 기존 `verify_code` 검사를 유지하며, hash를 스스로 다시 맞춘 JSON만으로 출처를 인정하지 않는다.
4. data/model digest·data ID·model ID·입력/특성 수·positive class·분할/행수·runtime을 검증한 고정 자산/manifest 및 저장 계획 계약과 대조한다. 기존 inference summary의 runtime 기록을 source의 pinned 의존성·계산 workflow 계약과 비교하고 불일치하면 중단한다. 기존 계산 결과 형식은 변경하지 않으며 publisher 자신의 경량 런타임을 추론 런타임으로 오인하지 않는다. 게시기가 ML 의존성을 설치하거나 모델을 로드해 확인하지 않는다.
5. `selected_ids`·순서·주지표가 저장 계획과 같아야 하며 metrics는 선택 ID별 정확히 한 개여야 한다. 기존 지표별 집계 출력 검사를 가능한 한 재사용해 `block/status/value/details`, 유한 수·형상·count/범위·정당한 `not_applicable` 이유를 검증한다. 없는 지표·중복·누락·잘못된 상태/값·threshold 불일치·계획상 누락된 선행조건에 대한 가짜 completed 결과는 거부한다. 검증은 모델/지표 재실행을 요구하지 않는다.
6. 최상위뿐 아니라 중첩된 details/provenance/limitations도 허용 목록으로 검사한다. 환자/입원 키·row key/position·sample ID·정답/예측/특성 벡터·checkpoint·CSV·top-k `selected_indices`는 어느 깊이에도 허용하지 않는다. calibration/DCA의 **등록된 집계 곡선**은 해당 지표의 형식으로만 허용하며, 임의 숫자 배열을 집계로 취급하지 않는다.
7. `eda_execution_result_hash`·`eda_assessment_status`를 Merge된 EDA와 대조하고 `clinical_use_approved`는 literal `false`만 허용한다. 알려진 데이터/모델·전처리·validation 재사용·환자 중복 한계가 빠지거나 긍정 판정으로 바뀐 결과는 거부한다. 자유 텍스트는 검증된 계약 근거만 사용하고 Markdown/링크는 안전하게 생성한다.
8. 검증한 **JSON만 정본**으로 삼아 canonical JSON과 `report.md`를 결정적으로 생성한다. artifact Markdown을 그대로 PR 파일/본문으로 복사하지 않으며 JSON과 canonical renderer의 불일치는 거부한다. 이 검증에 `model_evaluation.markdown` 등 기존 renderer를 재사용할 수 있다. AI 요약·외부 모델·임의 추천을 필요로 하지 않는다.

검증 실패는 행 없는 고정 사유로 명시하고 **아무 branch/commit/PR 쓰기도 하지 않는다**. 기존 source/계약 검사가 stale로 판정하면 PR1의 기존 검토·계획 갱신 절차로 돌아가야 하며, 이 PRD가 오래된 계획의 검증을 면제하지 않는다.

## 4. PR2의 고정 게시 계약

| 항목 | 값 |
|---|---|
| branch | `research/result-issue-{issue}-{split}` |
| base | API로 검증한 현재 default branch |
| split | `validation` 또는 명시적 `test`만. 두 분할은 별도 branch/PR이며 train·fallback 없음 |
| draft 최초 제목 | `평가결과 #{issue}: 고정 모델 · {split}` |
| 생성 파일 1 | `research/results/issue-{issue}/{split}/evaluation.json` |
| 생성 파일 2 | `research/results/issue-{issue}/{split}/report.md` |

저장소 상대 경로 표기는 GitHub tree 경로이며, 두 파일 외에는 생성·수정·삭제하지 않는다. PR1의 `research/reports/...` 파일·selection·threshold·코드·설정·README를 건드리지 않는다. 다른 분할이나 다른 Issue 결과를 묶지 않는다.

PR 본문은 `report.md` 전체를 중복하지 않고 다음의 짧은 자동 영역으로 구성한다.

- 출처 PR1 링크·Issue 링크, 실제 계산 run과 두 결과 파일 링크.
- 고정 모델/실제 분할/집계 행수, 새 학습·전처리·재분할 없음.
- 선택된 지표의 실제 값 또는 `not_applicable`과 사유. 주지표가 계산되지 않았으면 그대로 표시한다.
- EDA 판정, 환자 중복·validation 재사용·조건부 교육용 추론 등 핵심 경고와 “임상 사용 승인 아님”.
- 사람의 결과 검토 대기 안내. 모델 성능 통과·임상적 유효성·완전한 독립 평가를 단정하지 않는다.

`threshold: null`은 그대로 남긴다. 예컨대 F1·Recall·Precision이 threshold를 요구하면 실행 결과는 `not_applicable`, 값은 `null`, 이유는 `missing_parameters`/threshold 미지정 설명이어야 한다. 임의 `0.5`·성능 수치·임상 threshold를 넣지 않는다. PR1 계획의 blocked와 실제 평가의 `not_applicable`을 서로 다른 상태로 설명한다. EDA의 `failed`/`unverifiable`를 성능 계산 성공으로 덮어쓰지 않는다.

## 5. 재실행·멱등성·충돌과 사람 편집

1. 정확한 repo/head/base의 PR을 **모든 상태로** 조회한다. 적합한 open PR은 재사용하고 여러 후보·다른 base/repo/Issue/split·불명확한 소유권은 중단한다. `research.existing_pull` 등 기존 조회 도우미를 재사용하되 PR2의 제목·경로·소유권 검증을 추가한다.
2. 동일 branch의 PR2가 closed 또는 merged이면 `deferred`로 끝낸다. 자동 reopen·대체 branch/새 PR·기존 Merge 결과 덮어쓰기는 없다.
3. branch와 PR이 없으면 검증한 default commit에서 두 파일만 담은 branch/commit을 만들고 draft PR을 연다. branch가 이미 있거나 PR 생성만 실패한 재시도에서는, canonical 결과·출처와 두 파일만의 변경 범위를 포함해 **이 게시 흐름이 소유한 branch임을 입증한 경우에만** PR 생성을 복구한다. 이름이 맞는 branch라는 이유만으로 인수하지 않는다.
4. 기존 open PR/branch는 저장된 JSON·canonical Markdown·PR1/Issue/split/source 관계와 전체 변경 파일 목록을 검사한다. 예상 밖 파일·수동 결과 파일 수정·코드 변경·충돌·불명확한 소유권이면 중단하고 사람 조정을 요구한다. PR 본문 밖의 사람 편집은 파일 충돌과 구별한다. 추가 receipt/manifest 파일은 만들지 않는다.
5. 새 결과와 기존 결과를 canonical 의미로 비교할 때 **provenance의 workflow run URL만** 제외한다. 입력 digest·계획/EDA hash·source/runtime·분할·선택 지표·값/상태·한계 등 나머지 의미가 같으면 기존 run URL을 유지해 두 파일을 재생성한다. 기존 run 링크도 해당 저장소의 정당한 이전 계산 출처인지 검증한다. 새 run 링크를 남기기 위한 커밋/본문 갱신은 하지 않는다.
6. 커밋 전에 생성 예정 두 파일의 Git blob과 현재 branch blob을 비교한다. 둘 다 같으면 커밋·push 없이 `unchanged`; 자동 본문도 같으면 PR 수정 호출도 하지 않는다. 실제 의미 변경이면 같은 branch/PR의 두 파일만 새 commit으로 갱신한다. PR1/계획이 달라진 경우에도 새 merged 출처와 branch 소유권을 먼저 다시 검증한다.
7. `research.merge_auto_body`로 자동 영역만 갱신하고 사람의 prefix/suffix·검토 메모를 보존한다. 자동 영역 표시가 깨졌거나 중복이면 중단한다. 재실행으로 사람 제목·댓글·reviewer·Ready for review 상태를 강제로 초기화하지 않는다. 최초 PR만 draft로 생성하며 이후 자동 Ready 전환은 없다.
8. 기존 계산 workflow의 같은 PR1/split 직렬화는 변경하지 않는다. 별도 publisher는 대상 branch 쓰기를 직렬화하고 branch head와 PR 상태/본문을 쓰기 직전에 재조회한다. 다른 run의 오래된 계산이 더 최신의 다른 결과를 덮어쓰지 않도록 source run/attempt·기존 결과 출처를 확인한다. 관측한 head를 부모로 쓰는 non-force 갱신만 허용한다. 경쟁 변경을 발견하면 새 상태에서 전체 검증·blob 비교를 다시 하거나 명시적 conflict로 중단한다. 무조건 retry·force push·reset/rebase로 사람 변경을 덮어쓰지 않는다.
9. branch/commit 후 PR 생성이 실패해도 rollback으로 branch를 지우지 않는다. 검증 가능한 branch는 다음 실행에서 복구한다. PR 생성 성공 여부가 불확실한 API 오류는 정확한 head/base를 재조회해 중복 생성을 막는다.

읽기에는 `github_io.read_tree/read_file/read_object/git_sha`, PR 조회·자동 본문·명령/저장소 검증에는 기존 `research` 도우미를 재사용한다. 가능한 Git tree/blob/commit 쓰기 도우미도 재사용하되 PR1 전용 native safe-output 동작이나 PR1 계획 파일을 PR2에 섞지 않는다. 새로운 범용 게시 프레임워크·AI 권한은 만들지 않는다.

## 6. 실패와 상태 정직성

- 계산 실패/취소·artifact 누락/변조·출처/집계 검증 실패: PR2 게시 없음. 검증 실패를 정상 성능 보고서로 만들지 않는다.
- 권한 부족·PR 생성 허용 설정 꺼짐·API 실패: 계산 성공과 **게시 실패를 분리**해 표시한다. 성공한 계산 artifact는 유지하지만 PR2 완료를 주장하지 않는다. 권한을 늘리거나 PAT·다른 계정으로 우회하지 않는다.
- closed/merged PR2: `deferred`; 의미 변화 없음: `unchanged`; 정상 생성/갱신만 `created`/`updated`로 구별한다. 성공·실패·충돌 로그 어디에도 거부된 입력 원문이나 환자별 값은 출력하지 않는다.
- 자동 approve/Merge·PR1 상태 변경·새 승인 JSON·Pages·배포·원격 dispatch·임상 의사결정 연결은 없다. PR2의 사람 Merge 이후 후속 연결도 이번 범위 밖이다.

## 7. 수용 검사 — 신규 테스트부터 red → green

후속 구현자는 `tests/test_result_review.py`를 먼저 추가하고 기존 계산/권한 계약을 약화하지 않는다. 기존 `evaluate-model.yml`의 **단일 read-only job과 현재 정적 테스트를 변경하지 않는다**. 새 publisher workflow의 정적 검사는 신규 테스트로 별도 추가한다.

| 검사 | 기대 결과 |
|---|---|
| happy create | 실제 merged PR1 fixture·현재 source/runtime·정확한 동일-run artifact로 지정 branch, 두 결과 파일, 정확한 draft 제목/대상의 PR2 하나만 생성 |
| update와 본문 보존 | 의미가 달라진 집계는 같은 open PR/branch만 갱신. 사람 prefix/suffix·제목·검토 상태 보존, 자동 영역만 교체 |
| no-op | 동일 입력/결과의 새 run은 기존 검증된 run URL을 유지. JSON 키 순서 등 비의미 차이는 canonical 처리. 두 blob 비교 후 커밋/push/불필요 PR 호출 없음 |
| validation/test 분리 | 기본 validation·명시 test는 각 고정 branch/경로 사용. 다른 split 결과 혼입·train·fallback 거부 |
| branch 복구 | commit 후 PR 생성 실패를 재시도하면 소유권·두 파일 diff 검증 후 기존 branch에서 PR 하나만 생성. 출처 불명 branch는 거부 |
| wrong target/ownership | unmerged/fork/다른 repo·base·PR1·Issue·split·merge SHA·PR2 head, 여러 PR 후보·추가 편집은 모든 쓰기 전에 거부 |
| digest/plan/source/runtime | DATA/MODEL digest·ID·plan/EDA/mapping hash·지표 선택/주지표·실행 source SHA·조상/stale·pinned runtime 불일치와 조작 provenance 거부. publisher의 ML import/install 없음 |
| source 선행 검증 | 다른 source workflow path/ID/name·event·repo·run/attempt·실패/취소·불신/stale code는 artifact 다운로드 호출 전에 거부. trusted default 코드만 실행 |
| unsafe vector/artifact | 중첩 row/sample/patient ID·labels/probability/features·selected_indices·비등록 배열, extra/symlink/누락/중복 파일·다른 source run/attempt·잘못된 JSON·위조 Markdown 거부 |
| invalid results | NaN/Inf·허용 밖 필드·중복/누락/비등록 지표·잘못된 metric shape/status/value/count·completed로 위장한 선행조건 부족·clinical approval true 거부 |
| threshold·경고 | null threshold이면 F1/Recall/Precision 값 null + not_applicable 이유 보존. EDA failed/unverifiable·환자 중복·validation 재사용·전처리 한계가 결과/짧은 본문에 남음 |
| closed/merged | 동일 PR2가 closed/merged이면 deferred, reopen/새 PR/commit/push 없음 |
| race/recovery | head·본문·상태의 동시 변경을 감지하고 non-force 재검증/충돌로 종료. 오래된 source run의 덮어쓰기 방지, API 응답 유실 후 재조회로 중복 PR/commit 방지 |
| publication permission | PR 생성 차단/403·branch/PR 쓰기 실패를 게시 실패로 구분. 계산 artifact 유지·비밀 미출력·권한 우회 없음. 복구 시 성공한 기존 단계 재사용 |
| workflow 정적 경계 | evaluate 기존 파일/단일 read-only job/테스트 계약 유지. 별도 publish-evaluation.yml의 workflow_run 정확한 선행 이름·성공 조건·publisher 한 job·actions read/contents+PR write·trusted default checkout·다운로드 전 source 검증·source run/attempt exact artifact·두 파일 제한·pinned Actions·ML deps/AI/모델/Pages/자동 Merge 없음 |

최소 검증은 신규 결과 게시 테스트와 직접 관련된 모델 평가/workflow 정적 테스트를 한 번에 실행하고, 이후 기존 전체 회귀를 확인한다. GitHub/API fixture와 합성 집계로 먼저 검증하며, 실제 자산 smoke 기록을 원격 PR2 게시 검증으로 표현하지 않는다. 실제 원격 검증은 사용자의 코드 게시 승인·PR1 Merge 뒤 해당 run artifact/PR 링크로 별도 확인한다.

## 8. 이번 planner 작업의 종료 경계

로컬에서 본 문서와 `docs/architecture.md`의 current/planned PR2 절만 기록한다. app·tests·workflow·의존성·README·ADR 변경, Git 명령/commit/push, 클라우드 이슈/PR 생성, Test3 새 모델 코드 게시, 에이전트 추가 호출, 원격 실행은 하지 않는다. **문서 준비 완료 후 정지한다.**
