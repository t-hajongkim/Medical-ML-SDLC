# 현재 결정 기록

## 2026-10-17: PR1부터 PR2까지 원격 실습 완주

- 결정자: 사용자가 “5까지 흐름완주해줘 최종 결과 Pages 연동은 나중에 하자”라고 원본·Test3 코드 반영과 새 Issue/PR1·댓글·Merge·추론·평가·PR2 검증을 승인했다.
- 기존 PR1 #3과 Issue·결과 브랜치는 보존하고 새 코드 기준의 새 Issue로 시작한다.
- validation 분할과 AP 주지표를 사용한다. 기존 7개 지표를 유지하되 F1·Recall·Precision의 동작 확인 임계값은 교육용 0.5로 명시한다.
  이 값은 임상 표준·최적 임계값이 아니며 실습용 변경 요청에만 사용한다.
- 승인된 완주 범위 안에서 PR1을 검토 후 정상 Merge한다. PR2는 검토용 draft로 생성하고 자동 Merge하지 않는다.
- Pages와 cloud agent 댓글 전환은 구현하지 않는다. 향후 Pages는 최종 결과 한 번 게시가 아닌 단계별 진행 상태·결과 누적 갱신으로 설계한다.
- 성공 여부는 실제 PR1/댓글 적용 기록·Merge commit·추론 artifact·PR2 파일과 출처를 대조해 기록한다.

## 2026-10-17: 성공한 고정 모델 평가를 별도 PR2로 검토

- 결정자: 사용자가 PR2 구현과 별도 publisher workflow를 승인했다. 새 승인 JSON·프레임워크는 추가하지 않는다.
- 기존 `evaluate-model.yml`의 단일 read-only job과 기존 테스트 계약은 바꾸지 않는다.
  `publish-evaluation.yml` 한 job만 actions read/contents+PR write를 가지며 trusted default 코드를 실행한다.
- source workflow ID/이름/경로·repo·run/attempt·성공·코드 비교를 artifact 조회보다 먼저 검사한다.
  정확한 이름의 유일한 비만료 artifact ID만 내려받고, 두 일반 집계 파일 외에는 허용하지 않는다.
- `result_review.py`는 기존 merged PR/보고서·Git blob·집계 검증·Markdown renderer·non-force commit 도우미를 재사용한다.
  고정 model ID는 검증한 DATA manifest와 checkpoint/readme/NOTICE 계약으로 대조하며 모델을 받거나 로드하지 않는다.
- 지표의 저장 threshold·선행조건·집계 행수를 확인한다. threshold null과 실제 not_applicable 이유를 보존하고 임의 수치를 만들지 않는다.
  top-k 결과는 이미 제거된 행 위치를 다시 요구하지 않으며, 전달된 위치 벡터는 계속 거부한다.
- Issue/split별 고정 branch와 결과 두 파일만 소유한다. 기존 JSON·canonical Markdown·source run·변경 경로를 확인한 뒤
  같은 의미면 기존 run URL/파일을 유지한다. 사람 제목·자동 영역 밖 본문·review 상태는 초기화하지 않는다.
- 게시를 저장소별 직렬화하고 최신 branch head·PR 상태/본문을 쓰기 직전에 재조회한다.
  이전 run의 새 결과 덮어쓰기·force 갱신·closed/merged PR 재생성을 금지하며 실패한 PR 생성의 branch는 복구용으로 유지한다.
- 원격 코드는 미게시이고 Test3 PR1 #3은 OPEN draft다. 실제 PR2 생성·원격 실행·cloud-agent migration은 하지 않았다.
  기존 PR1 피드백 지연·요약 정확성 공백은 후속 항목이며 이 변경에서 고치지 않는다.

## 2026-10-17: 고정 모델 추론과 실제 평가 연결

- 결정자: 사용자가 고정 모델 로딩·추론과 기존 15개 평가 지표의 Actions 연결을 구현하도록 승인했다.
- 입력은 이미 등록된 GHCR DATA/MODEL과 113-feature 순서만 사용한다. 모델 저장 버전과 같은 Python 3.11/sklearn 1.2.2 환경을 분리한다.
  학습·추가 scaler·재분할·새 원자료 다운로드는 하지 않는다. 임의 pickle/모델 URL을 받지 않는다.
- `inference.py`는 data/model manifest·source/hash를 검증한 뒤에만 역직렬화하며, 이름 있는 특성은 DataFrame 이름/순서를 맞춘다.
  metadata의 순서를 믿지 않고 row_key/split/row_position으로 정답을 연결한다.
- `model_evaluation.py`는 실제 Merge된 PR의 immutable merge SHA에 있는 저장 계획만 읽고 현재 trusted default 코드로 실행한다.
  Merge가 사람의 교육용 계획 승인이다. 별도 승인 snapshot이나 추가 수동 게이트는 만들지 않는다.
- 기본은 validation이고 test는 명시적 선택만 허용한다. threshold·참조 확률·임상 행동은 저장 계획 외에 생성하지 않는다.
  기존 `executor_eval.execute_plan`의 집계·not_applicable 계약은 변경하지 않는다.
- 단일 읽기 전용 hosted job에서 자산 추출·추론·지표 계산을 한 번 연결한다. 확률/정답/키/특성은 공개하지 않고 집계 두 파일만 artifact/Summary로 남긴다.
- scratch 모델 이미지는 CMD가 없으므로 기존 파일 추출기의 `docker create`에 실행하지 않을 `/bin/true`를 명시했다.
  컨테이너 start/run으로 모델 이미지를 실행하지 않는다.
- 실제 고정 validation 15,265행의 추론·기본 네 지표를 Python 3.11/sklearn 1.2.2에서 로컬 검증했다.
  원격 Merge Actions 실행 증거나 독립 임상 검증으로 표현하지 않는다.
- 사용자가 Test3를 우선 기존 PR1 안정 버전으로 업데이트하도록 명확히 요청했다.
  Test3는 원본359688과 동일한 소스의79b176e로 갱신하고 PR 생성 허용만 켰다. 새 추론/평가 코드는 아직 로컬이며 Test3에 푸시하지 않았다.
- 다시 볼 조건: 새 모델 포맷/새 데이터셋 지원, 모델 저장 버전 변경, 실제 원격 Merge 실행 연결 검증, PR2/Pages 추가.

## 2026-10-17: native 본문 정규화 호환과 게시 경계 정리

- 질문: 정상 본문이 `&lt;30`에서 `<30`으로 정규화되어 게시 검사가 실패한 문제를 어떻게 고칠 것인가?
- 선택/결정자: 사용자가 원본 템플릿 수정을 승인했다. 기존 상세 PR 본문을 유지하는 최소 수정으로 복구한다.
- agent 종료 때의 Python `post_check`를 없애고 공식 `safe-outputs.steps`에서 실제 쓰기 전에 같은 검사를 한 번 실행한다.
  기대 본문은 현재 고정 gh-aw Actions의 `sanitizeContent`를 직접 사용한다. 독자적인 엔티티 변환 규칙을 복제하지 않는다.
- 원본 본문 hash·정확한 대상/제목/브랜치·create/update 선언 검증은 유지한다.
  검사 실패면 뒤의 native 게시가 실행되지 않게 하되, 모든 agent 실패를 막는 포괄 조건·새 job·새 승인 플래그는 추가하지 않는다.
- 실제 실패 실행의 원본문과 native 선언을 공식 정규화 함수로 재검증하고, 정답 내용이 바뀐 본문은 거부하는 회귀 사례를 확인한다.
- 복사본 PR 생성 허용 설정은 템플릿 소스나 원본 설정에서 상속되지 않는다. 전역 쓰기 권한이나 PAT 추가로 우회하지 않는다.
  권한 오류만 남은 게시 재시도와 agent까지 실패한 기존 실행의 자료 확인을 구분해 안내한다.
- 범위: 원본 템플릿만 수정한다. Test2/Test3 소스·설정·기존 결과 브랜치와 실행은 변경하지 않는다.
  긴 본문 재출력을 줄이는 게시 인터페이스 변경은 방식 선택 후 별도로 다룬다.
- 다시 볼 조건: gh-aw Actions 버전·본문 정규화 정책·본문 전달 인터페이스를 변경할 때.

## 2026-10-17: job 내부의 반복 준비와 구형 경로 제거

- 질문/결정자: 사용자가 제거·리팩토링을 승인했으며, job 개수보다 한 job 내부에서 반복되는 자원 준비를 줄이도록 범위를 명확히 했다.
- 선택: 최초 Issue의 기본 EDA 선행과 AI Plan/Executor 경계는 유지한다.
  인증된 이전 집계가 있으면 행 없는 문맥으로 댓글을 조율하고, 새 EDA가 필요할 때만 GHCR 추출·입력 검증을 한 번 실행한다.
- 지표 추천 적용은 검증된 작은 계획 문맥만 사용한다. 같은 단계의 PR 중복 조회와 작성자별 권한 중복 조회를 제거한다.
- Markdown 안의 긴 Python 어댑터는 `research_tools.py`로 이동하고, 도구가 별도 Python을 다시 실행하지 않고 함수를 직접 호출한다.
  보고서 tree 선택 전에 어댑터를 비공개 runner에 복사하며 게시 재조회/post-check는 저장소 코드 import 없이 처리한다.
- 사용하지 않는 직접 게시·조기 준비 PR·매핑 읽기 함수와 `run-data-checks` 호환 CLI를 폐기한다.
  실제 보고서 생성·제작용 Dockerfile·후속 지표 실행기는 유지한다.
- 이전 게시 증거 보관 기간은 1일에서 7일로 변경한다. 보관 만료는 명시적 새 계산 사유이며 검증을 생략하지 않는다.
- 봇 댓글은 작업 시작 전에 제외하고 폼 파싱 의존성은 실제 필요한 target 검증 job에 명시한다.
  CI 범위나 native job 경계 자체를 줄이는 변경은 이번 job 내부 최적화에 포함하지 않는다.
- 다시 볼 조건: EDA 도구가 데이터 자산을 수정하게 되거나, 서명/매핑/도구 매개변수 계약 또는 게시 권한 경계를 변경할 때.
- 로컬 구현 범위이며 원격 저장소와 Test2에는 푸시하지 않는다.

## 2026-10-17: 기본 EDA 우선과 결과 이후 PR1 생성

- 질문: 준비 PR과 EDA 전 AI 조율 중심의 연결을 원래 연구 흐름에 어떻게 맞출 것인가?
- 선택/결정자: 사용자가 구현 계획을 승인했다. GHCR 입력 확인·필요한 매핑 준비 후 기본 EDA를 먼저 실행한다.
  AI는 그 실제 결과를 보고 미사용 EDA 블록만 추가 제안하며, Python Plan 검증·Executor 계산 뒤 최종 지표 추천을 만든다.
- 최초 PR1은 결과·추천이 준비된 뒤 생성한다. 초기 진입은 Issue 대상, 이후 자연어 피드백은 기존 PR 대상으로 구분한다.
  새 요청마다 PR을 만들지 않고 같은 PR의 계획·보고서를 갱신한다.
- 지표 선택만 바뀐 피드백은 EDA를 실행하지 않는다. EDA 변경은 필요한 블록만 계산하고 동일 입력의 검증된 결과를 재사용한다.
  파일·매핑·코드가 달라지면 재사용을 거부한다. 검사하지 않은 입력을 ready로 표시하지 않는다.
- 최종 계획 조립과 파일 저장을 분리한다. 같은 최종 입력의 계획을 저장 과정에서 재생성하지 않는다.
  추가 EDA로 근거가 달라진 경우 필요한 재계산은 유지한다.
- 합성 회귀 검사는 CI에 모으고 실제 요청의 권한·입력·허용 블록·필수 매개변수 검사는 실행 경계에 유지한다.
  gh-aw의 읽기 전용 AI와 제한된 게시 권한 경계를 보존하고 lock은 공식 compiler로 생성한다.
- 범위는 PR1까지다. 새 모델 학습·재분할·Model 이후 자동 연결은 제외한다.
  로컬에서 구현·검증·기록하고 원격 저장소와 Test2에는 푸시하지 않는다.
- 다시 볼 조건: 후속 모델/평가 연결, 새 입력 형식, 공개 명령·과거 보고서 호환 범위를 변경할 때.
- 구현 확인: 기본 선행·미사용 추가 점검·최초 PR 지연 생성·지표만 변경 시 EDA 재사용·동일 PR 갱신을 합성 입력으로 확인했다.
  이전 PR 결과 재사용은 기존 성공 실행의 게시 artifact와 Git blob을 확인하며, 자기 hash만 맞춘 조작 집계는 거부한다.
  별도 승인 기록·새 Secret은 추가하지 않는다. 게시 본문 경계는 native HTML 주석 제거와 호환되는 보이는 Markdown을 사용한다.
  원격 AI 실행·실제 자산/모델 실행 증거로 해석하지 않는다.

## 2026-10-16: 자연어 피드백 중심으로 승인 규약 간소화

- 결정자/선택: 사용자 요청으로 별도 불변 승인 JSON·Merge hook·명령 전용 파서를 제거한다.
  사람이 Merge한 계획과 commit을 승인 기준으로 사용한다. 아래의 이전 승인 snapshot 결정은 이 결정으로 대체된다.
- 단계별 호출자를 조사해 승인 전용 `validate_record`, `validate_saved_binding`과 관련 전용 검사를 제거한다.
  기존 구형 준비/학습 경로 삭제 이후 호출자가 없는 pair 계약 도우미도 제거한다.
  실제 입력·허용 블록·필수 매개변수·현재 피드백 출처 검사는 실행 및 AI 게시 경계에 남긴다.
- 규약 제거 후에도 중복인 입력 매핑/피드백 Git 파일 읽기와 JSON 파싱만 `github_io.py`로 공통화한다.
  쓰기 전략이 다른 PR 게시·데이터 파일 검증·순수 지표 계산을 억지로 하나로 합치지 않는다.
- 자연어 뜻은 AI가 해석한다. Python은 권한 확인·전달·변경안 검증·저장만 수행한다.
  계획 준비 상태는 안내이며 별도 코드가 Merge를 차단하지 않는다.
- 범위: 로컬 Git에 이전 작업과 수정 후 상태를 기록한다. 원격 저장소와 Test2는 업데이트하지 않는다.
- 다시 볼 조건: 후속 모델/평가 실행을 연결할 때 Merge commit과 실행 입력을 명시하고 준비 조건을 실제 실행 경계에서 검증한다.

## 2026-10-16: GHCR manifest를 실행 입력의 기준으로 사용

- 질문: 데이터셋별 저장소 profile과 제작 명세를 연구 실행에서도 중복 조회해야 하는가?
- 선택/결정자: 사용자가 GHCR 메타데이터 우선 전환과 변경 파일 보고를 승인했다.
  기존 이미지 manifest를 재사용하고 `config/input-profiles.json`의 데이터셋/data_id 등록 목록을 제거한다.
- 제작 명세의 원본 URL/revision/SHA·특성 순서·분할·모델 계약은 유지한다.
  EDA 결과의 고정 중복/결측 기준값은 제거하고, 현재 이미지에 새 input_metadata나 재발행을 강제하지 않는다.
- 알려진 v2 형식의 역할 계약은 호환 어댑터로 보존한다.
  다른 컬럼 구조는 이미지 안에 명시적 의미 사전이 필요하며 환자 키·라벨 의미를 추측하지 않는다.
- registry 제거로 약해질 수 있는 승인 출처 검증은 고정 GHCR digest에서 실제 manifest를 다시 읽어 보완한다.
  PR의 재계산 가능한 hash만으로 출처를 인정하지 않는다.
- 다시 볼 조건: 새 입력 형식/다중분류/새 이미지 제작기를 지원할 때. 실행 중 교체/재학습·Test2 변경은 범위 밖이다.

## 2026-10-16: 구형 전처리·pair 초안 제거와 Test2 동기화 중단

- 사용자 승인으로 중단 전용 초기화 workflow, 이전 assets/bootstrap/infer/preparation 및 관련 pair/container 테스트를 제거한다.
  현재 준비 자산 게시·입력 계약·계획/실행·권한·승인 검증과 재사용 공통 도우미는 유지한다.
- 로컬과 원본 템플릿에만 반영한다. 사용자가 추후 삭제/일괄 업데이트하기로 한 Test2는 수정하지 않는다.
- 이후 GHCR 우선 전환은 위의 별도 승인 결정으로 진행한다. 의미 사전 없는 임의 매핑은 허용하지 않는다.

## 2026-10-16: 사용자 승인에 따른 네 업무 파일 리팩토링

- 요구/결정자: 사용자가 planning_eda/executor_eda/planning_eval/executor_eval 네 업무 파일과
  “실행 중 데이터 교체·재계획 루프 불필요”를 확정하고 실제 리팩토링·전후 HTML·블록별 코드 리뷰를 승인했다.
- 선택: 추가 추상 계층보다 기존 모듈을 역할별로 재편한다. 얇은 Intent·조율은 planning_eda에,
  계획은 planning_eval에, EDA 실행·집계·캐시는 executor_eda에 둔다.
  별도 cloud 연결 파일은 기존 research 공통 CLI/세션/게시 입구로 이관한다.
- 대안: Intent 전용 에이전트·독립 실행계약/Executor 추가 파일·LangGraph 의존성·재계획 루프는 선택하지 않는다.
  단방향 계획 JSON과 등록 함수 참조를 사용하며 실제 AI 추론 엔진은 gh-aw Copilot을 유지한다.
- 입력: 시작 data_id에 맞는 등록 profile을 선택하고 컬럼 의미·타입·역할·모델 입력 순서를 검증한다.
  미등록·모호한 새 데이터는 추정하지 않는다. 실행 중에는 입력을 고정한다.
- 실행 계약: schema/매핑·data/model digest·코드·도구/정책 hash·Tool 입력 참조를 저장한다.
  plan-eda는 계산하지 않고 execute-eda가 같은 plan_hash를 확인한다.
  입출력 불일치·필수 진단 누락·변조 캐시는 실패, 새로 선택한 블록만 추가 실행한다.
- 보존: 기본/AI/사용자 선택 조합·댓글 권한·민감정보 의심 거부·승인 snapshot·계산/게시 분리·멱등 재조회.
  업무명 research-plan과 review-eval-plan으로 workflow 원본/등록 경로·테스트·문서를 함께 변경한다.
- 범위: executor_eval은 기존 지표의 순수 실행기로 구현하지만 모델 IO·PR2·추론·Pages workflow는 새로 켜지 않는다.
  실제 EDA/지표 수치 예시는 기존 실제 실행과 명시한 합성 회귀만 사용한다.
- 검토: 기존 75f79d2 기준과 새 Git 객체의 실제 코드를 같은 전후 HTML에 표시하고 이후 코드를 블록별로 설명한다.
  workflow job 통합은 별도 변경이며 runner 수를 줄였다고 주장하지 않는다.
- 다시 볼 조건: 새로운 준비 포맷/분할/다중분류, 의미 사전 미제공 입력, 최종 모델/평가 승인 자동 연결이 필요할 때.

## 2026-10-16: 실제 Test2 적용 호출 시간 초과와 결과 재조회

- 관측: run 38846466834에서 EDA·추천 검증 후 적용은 61.15초에 완료했으나 호출 측은 60초에 응답 대기를 종료했다.
  도구 서버의 600초 설정만으로 전체 호출 제한이 늘지 않았다. 재호출은 기존 중복 방어에 막혀 게시 플래그를 받지 못했다.
- 선택: 최초 검증한 전체 EDA를 private session hash에 결합하고 적용 때 재검증·재사용한다.
  추가로 선택한 미측정 점검만 실행하며 데이터 파일/manifest·현재 PR·입력 계약 검증은 생략하지 않는다.
- 재시도: 동일 추천·동일 PR/head·본문·저장 commit/tree/hash를 확인한 뒤 기존 게시 플래그만 반환한다.
  재계산·재커밋·임의 코드 import는 하지 않는다. 다른 추천·변경된 PR은 새 실행이 필요하다.
  원자적 receipt와 동시 적용 lock을 사용한다.
- 실패: 준비된 결과의 필수 push/본문 요청이 없거나 report_incomplete이면 workflow를 실패시킨다.
  로컬 결과 생성과 GitHub 게시 완료를 구별한다. 이번 실패를 자동 새 AI 호출로 대체하지 않는다.
- 실제 native 출력의 PR 번호는 숫자 문자열일 수 있다. 정확한 정수와 동일한 십진 문자열만 허용하고,
  다른 번호·선행 0·소수·boolean은 거부한다. 실제 실행 출력으로 게시 확인 검사를 재생한다.
- 다시 볼 조건: 호출 제한 정책 변경, 추가 점검이 다시 60초를 넘는 경우, 비동기 작업/결과 조회가 필요할 때.

## 2026-10-16: 공개 목적·입력 조율과 PR1 자연어 피드백 복구

- 질문: 이미 전처리된 입력의 스키마·블록 config를 AI가 먼저 맞추고, 실제 EDA와 목적을 근거로 PR1을 만들 수 있는가?
- 선택지: 기존처럼 알려진 config이면 먼저 EDA / 알려진 config도 AI 목적·입력 검토 후 EDA.
  자연어 댓글 무시 / 기존 /plan만 사용 / 권한 확인한 자연어를 AI가 형식화하고 Python 검증 후 반영.
- 선택: 사용자 요청에 따라 알려진 입력도 앞단 AI 조율을 거친다. 확정 매핑은 재추측하지 않고 재사용한다.
  등록 스키마가 미확정일 때만 AI가 역할 매핑을 추천한다. Python이 컬럼·타입·의미·hash·등록 블록을 검증한다.
- 목적: 폼의 공개 연구 목적·질문·조건·추가 요청 4필드를 transient 문맥으로 제공한다.
  기존 폼을 바꾸거나 스키마를 사용자에게 다시 입력시키지 않는다. 스키마는 등록 GHCR에서 읽는다.
- 2→3: 필수만이 아니라 앞단에서 실제 실행한 전체 EDA와 공개 목적을 AI에 제공한다.
  초기 AI는 등록 목적을 합치고 EDA를 추가할 수 있지만 임의 주지표·threshold·변수 삭제·새 fit은 허용하지 않는다.
- PR1: write/maintain/admin 사람의 자연어 댓글을 ID·작성자·본문 hash만 담은 대기열로 전달한다.
  실제 API 원문·현재 권한·대상 PR을 확인한 뒤 AI가 등록 조율안을 제안하고 Python이 같은 PR의 선택·계획을 갱신한다.
  기존 /plan은 유지한다. 반영한 댓글 편집은 재적용하지 않으며 새 댓글을 요구한다.
- 감사: 조율 이유·선택/목적/스키마/피드백 hash·최초 EDA 근거를 저장한다. 원문/환자 행은 감사 기록에 복제하지 않는다.
  게시 전 원문/권한/PR 변경은 중단하며 미반영 자연어 요청·조율 기록 불일치는 승인 기록 생성을 막는다.
- 결정자: 범위·UX는 사용자("Target은 지금 정도로 충분", "우선 여기까지 진행하자").
  검증 계약·형식·출력 허용 목록·기존 선택 보존 방식은 assistant가 구현한다.
- 범위: Target 유지, 1→2·2→3·PR1 피드백과 모델 GHCR/로컬 로드 확인까지.
  모델 자동 로딩·PR2·최종 Evaluation·Pages는 이번에 구현하지 않는다. 템플릿만 반영하며 실제 원격 실행은 사용자가 확인한다.
- 대체: 아래의 "현재 Diabetes는 추가 입력 AI 없이 진행", "자유서술 미제공", "명령만"은 이전 결정 이력이다.
- 다시 볼 조건: 미등록 스키마·임의 CSV·새 feature engineering·다중분류·모델 자동 검사/PR2 연결이 필요할 때.

## 2026-10-16: 앞단 config/AI 입력 매핑을 이번 수정에 포함

- 사용자 요구: "데이터 범용성 확장을 위해 config로 input들 빼거나 ai가 schema input에 맞는 parameter들로 콜".
  "응 그렇게 진행하고" 승인에 따라 후속으로 미루던 입력 어댑터를 이번 수정에 포함한다.
- 선택: 등록된 준비 데이터는 확정 config를 재사용하고, 등록 프로필의 bindings가 null인 새 스키마만 AI로 추천한다.
  임의 원본 수입·새 fit·임의 함수 실행은 추가하지 않는다. 세 분할·이진 정답·준비 숫자 특성 계약을 유지한다.
- 역할: AI는 승인된 스키마·타입·설명·의미 후보·코드북을 받아 bindings/reasons/evidence를 추천한다.
  Python은 등록 의미·컬럼·형식·hash를 검증하고 표준 context를 만든 뒤에만 EDA를 호출한다.
  환자 키를 입원 ID로 대체하거나 없는 식별자를 생성하지 않는다.
- 현재 Diabetes는 등록된 환자 연결·원본 결측 정보를 유지하며 추가 입력 AI 호출 없이 config를 사용한다.
  매핑 없는 입력은 EDA 미실행 PR을 만들고 같은 workflow_run 체인에서 먼저 입력 AI를 처리한다.
- 후단 블록 추천은 별도의 새 hash를 사용하며 입력 매핑 hash도 포함한다.
  검증된 매핑·원래 추천·profile/source 스냅샷을 보존하고 Merge 시 registry와 다시 대조한다.
- generic 보고서·블록은 Diabetes 전용 cohort/30일 추적/Caucasian 대치/113-feature 주장을 적용하지 않는다.
- 검증 범위: 다른 이름/분할값/정답값의 합성 준비 패키지와 실제 집합 비교·ZIP·Git fixture를 사용한다.
  다른 실제 임상 데이터·실제 원격 AI 성공으로 표현하지 않는다. 모호한 입력은 확인/등록 보완이 필요하다.
- 다시 볼 조건: 임의 CSV, 다중분류, 새로운 분할 종류, 원문 목적의 AI 해석, 모델 실행·Pages가 필요할 때다.

## 2026-10-16: 사수님 workflow_run 패턴으로 AI 연결 수정

- 관측: Test2 PR #3의 추천 run 38770216543은 pre_activation만 success이고 agent는 skipped였다.
  GITHUB_TOKEN 호출자 github-actions[bot]의 협업자 역할 none이 기본 사람 역할 검사에 걸렸다.
- 질문: 호출 이벤트 전환, 역할 검사 완화, 명시적 봇 허용 중 무엇을 사용할까?
- 검토 이력: repository_dispatch·명시적 봇 허용안을 검토했지만 배포하지 않았다.
  사용자 요청 "사수님이랑 다른 방식으로 구현하기 싫어서"에 따라 원본의 이벤트 연결을 다시 확인했다.
- 선택: 원본 analysis-verification.md처럼 workflow_run을 사용한다. 기존 결정론적 EDA는 유지하고
  준비·검토 성공 완료 후 AI가 이어받는다. 토큰으로 새 workflow_dispatch를 호출하지 않는다.
- 대상 전달: 원본의 최신 PR 선택을 복제하지 않고 PR 번호·run ID/회차·source SHA만 담은 작은 artifact로 연결한다.
  동시 Issue를 섞지 않고 같은 저장소·기본 브랜치·등록된 선행 경로와 일치할 때만 AI를 실행한다.
  부정·만료·모호한 입력은 실패로 드러내며 요청하지 않은 실행과 Merge 확정은 AI를 시작하지 않는다.
- 권한: 기본 사람 admin/maintainer/write와 기존 same-repo PR·파일 제한을 유지한다.
  on.bots·roles: all·새 PAT·compiler 업그레이드는 없다. 준비·검토의 actions: write는 제거한다.
- 검증: source run·회차·정확한 PR·실제 ZIP 파싱·변조/만료/잘못된 경로를 합성 API로 검증한다.
  기본 브랜치 이름을 고정하지 않아 정적 branch-filter 경고는 남지만, agent 전 source branch 검증으로 제한한다.
- 결정자: 사수님과 같은 이벤트 방식을 요구한 사용자. 구체적 전달 계약·기존 블록 보존은 assistant가 구현한다.
- 반영: 템플릿만 수정·push한다. Test2와 실제 Copilot/PR 실행은 사용자가 관리한다.
  당시 제외했던 입력 어댑터는 위 추가 승인으로 포함한다. 임의 분할 종류의 일반화는 여전히 제외한다.

## 2026-10-16: 조합형 복구·PR1 승인 구현과 템플릿만 반영

- 질문: 고정 평가 후보 대신 원래의 기본 + 실제 AI 추천 + 사용자 추가/제외를 어떻게 복구할까?
- 선택: EDA 점검 7개와 Evaluation 결과 지표 15개를 별도 카탈로그·독립 파일로 관리한다.
  기본/필수/목적은 planning-policy.json, 사람 선택은 selection.json에 둔다.
  순수 planner는 조합·입력 요건만 검증하고 3단계에서 모델·지표를 실행하지 않는다.
- AI 선택: 원래 참고 저장소와 같은 gh-aw의 Copilot 엔진을 사용한다.
  GitHub Models API/별도 PAT·AI Secret을 추가하지 않고 native github.token/copilot-requests 인증을 쓴다.
  공개 집계·구조화 목적·등록 카탈로그만 보내며 자유서술·환자 행·작업공간은 제공하지 않는다.
  pending/stale과 실제 provided를 구분하고 가짜 빈 성공 추천을 만들지 않는다.
- 사용자 변경 선택: 명령만 있는 /plan 댓글을 권한 확인 후 신뢰 코드가 처리한다.
  자유서술을 AI가 임의의 주지표·임상 승인으로 변환하는 방식은 선택하지 않았다.
  이유: 선택·제외·threshold 의도를 재현하고 코드/인용 예시의 오실행을 막기 위해서다.
  결정자: 기존 조합형·사람 승인 요구를 구현한 assistant. 자연어 댓글 UX가 필요하면 별도 검토한다.
- 복구/보존: 미처리 댓글을 순서대로 반영하고 ID·본문 해시·작성자·적용/거부를 기록한다.
  선택 저장 후 보고서 갱신 실패는 재실행으로 복구한다. 사람 본문·제목·기존 선택·source Git 상태를 보존한다.
- 확정: AI provided·명시적 주지표·blocked 없음 확인 후 사람이 Merge한다.
  merge SHA의 입력/계획/자산을 검증해 새로운 불변 교육용 승인 기록을 추가한다.
  자동 승인·병합·후속 모델 실행은 없으며 임상 승인 false와 알려진 한계를 보존한다.
- 최신 사용자 승인: "이것까지 마무리하고 코드리뷰 시작하자 그리고 깃헙에까지 푸쉬해줘".
  순서를 구현·로컬 검증 → 템플릿 반영 → 사용자의 파이프라인 확인 → 코드 리뷰로 변경했다.
- 반영 범위 결정자: 사용자. "내 템플렛에 허용해줘 복사한 깃헙은 내가 알아서할게".
  Medical-ML-SDLC 템플릿에만 코드·PR 생성 허용을 반영한다. 기본 workflow token 권한 read는 유지한다.
  Test1 복사본의 코드·설정·Issue·PR은 변경하지 않는다.
- 실행 제한: "템플렛에서 검증 안할거야 그리고 내가 직접할거야".
  템플릿에서 Issue 생성/수동 dispatch/실제 Copilot/PR 병합 시연을 하지 않는다.
  로컬 합성·기존 실데이터 집계 검증을 실제 원격 파이프라인 성공으로 표현하지 않는다.
- 다시 볼 조건: 자연어 댓글·다른 비교모델·임상 사용·4~6단계 실행·개인 DATA 초기화가 필요하거나
  조직 정책/브랜치 보호/엔진 이용 권한이 현재 전제를 바꾸는 경우다.

## 2026-10-15: 연구 요청→EDA→검토용 draft PR 연결의 로컬 구현 승인

- 최신 승인: "이것도 같이 구현해주고 이 단계까지 전부 완료되면 나한테 코드리뷰 요청해줘".
  로컬 구현·검증→사용자 리뷰→승인 후 원격 반영 순서를 지킨다. 원본/독립 복사본에 commit/push/dispatch/실제 PR을 하지 않는다.
- 선택: 새 YML 1개와 표준 라이브러리 `app.research.py`, 신규 합성 테스트만 추가한다.
  기존 5필드 폼·config·publisher·Dockerfile은 바꾸지 않고 `app.executor_eda`와 manifest/이미지 참조 검증을 재사용한다.
- 데이터 접근: 고정 Public data_image만 pull하고 create/cp로 준비파일을 추출한다.
  DB 서비스를 실행/질의하지 않고 모델 이미지·raw 원본을 받지 않는다. 생성 container와 runtime context만 정리한다.
- 안전: default branch 코드만 실행하며 Issue 본문은 JSON 데이터다. OWNER/COLLABORATOR만 자동 진행,
  그 외 작성자는 유지관리자의 수동 dispatch를 요구한다. 수동 actor의 write/maintain/admin 권한을 확인한다.
- 실패 경계: 필수 폼/동의·hash/행/키/수치 계약 파손과 네트워크/GH/git 오류는 중단한다.
  알려진 환자 중복 failed와 관측/이력 unverifiable는 숨기지 않고 유효한 데이터의 검토용 draft PR에 남긴다.
- 출력: 번호 기반 허용 폴더의 요청 JSON·집계 EDA·Markdown 표 보고서·평가 계획 추천 JSON만 stage한다.
  후보의 이유·필수입력만 기록하고 primary_metric/threshold는 null, DCA 보류·비교모델 없는 NRI 적용 불가로 둔다.
- 멱등성: 같은 Issue 고정 branch/열린 PR 갱신, 생성파일 외 사용자 변경과 PR 제목/본문/댓글 보존.
  닫힌/병합 PR은 보류, 동일 내용은 commit 없음. 기존 실행 증거를 유지하며 현재 실행 링크는 Summary에 남긴다.
  force push/amend/자동 merge는 없고, PR 생성만 실패한 branch는 소유 marker를 확인해 재시도한다.
- 권한: GITHUB_TOKEN contents write / pull-requests write / issues read만 사용한다.
  PAT/새 Secret/packages write/설정 자동 확대는 없다. Actions의 PR 생성 허용과 token-generated PR의 CI trigger 제한을 문서화한다.
- 검증: 외부 명령은 모의하고 기존 101,766행 EDA를 이용해 실제 상태·집계/추천 순서를 로컬 payload로 확인한다.
  실제 GitHub 이벤트·컨테이너·push/PR 생성 검증은 사용자 리뷰 후 승인된 원격 실행까지 남는다.
- 복사본: 독립 저장소는 원본 변경을 상속하지 않는다. 동일 동적 코드라도 각 default branch 반영이 필요하다.

## 2026-10-15: 2단계 독립 EDA 블록의 로컬 준비

- 승인: "1단계 완료되었으면 2단계 블록들 준비 시작해줘". 원격 Issue/PR·push/dispatch 승인은 아니다.
- 선택: 기존 catalog와 3개 파일을 보강하고 cohort/prediction_timing/label_observability/preprocessing_lineage를
  각 독립 파일로 등록한다. 작은 공유 입력 도우미와 `app.executor_eda` 집계 runner, 합성 회귀 테스트만 추가한다.
- 입력: 기존 검증된 data/assets manifest와 metadata/db_features. stage1 hash·원본 검증은 복제하지 않고 재사용한다.
  숫자 113개×101,766행은 행별 스트리밍하고 모델·checkpoint를 읽거나 실행하지 않는다.
- 분할: validation을 정규 명칭으로 사용한다. valid는 로그에 명시하는 alias이며 옛 overlap 조회키는 alias 입력에만
  deprecated로 보존해 기존 회귀 단언을 유지한다. 다른 split·잘못된 bool/target은 실패한다.
- 해석: 입원/환자·원본 인종/대치 인종·인코딩/추적 완전성·무결성/누수 이력을 분리한다.
  출처 선정 설명은 행별 판정이 아니며 upstream 모집단/제외 수·연구단위/추가 조건은 모른다고 보고한다.
- 상태: 기술 집계 completed, 측정 위반 failed, 근거 부족 unverifiable.
  schema의 수치 계약 passed를 임상 의미/단위 검증이나 전체 통과로 확대하지 않는다.
- 검증: 실제 중복 5,055/5,086/1,760, 원본 race 결측 1,596/336/341, 고유 환자 71,518,
  입원 101,766, target 1 사건 7,950/1,703/1,704를 확인했다. 상수 특성 8개는 관찰로 남기며 삭제하지 않는다.
- 회귀 호환: 과거 assets/bootstrap의 schema 소비자에 실제 수치 행을 전달한다. 기존 pair 초기화는 측정된 수치 계약을
  게이트로 삼고, 강화된 schema와 demographic의 근거 부족을 그대로 반환한다. fit/predict 로직을 바꾸거나 새 EDA에서 호출하지 않는다.
- 보존: 1단계 workflow/Issue Form/고정 명세·source hash·digest/Dockerfile을 변경하지 않는다.
  로컬 Docker·클라우드 실행·commit/push·새 분할/변환/모델 변경·성능지표 블록은 수행하지 않는다.
- 당시 후속: Issue 자동 호출/결과 PR·지표 제안은 미구현이었다. 위 최신 승인으로 로컬 연결만 추가했고 원격 활성은 대기한다.
  연구 적합성 인증이나 임상 성능 주장을 하지 않는다.

## 2026-10-15: 사용자 승인에 따른 실제 GHCR 등록

- 승인: 사용자의 "오케이 등록해주고" 요청으로 코드 반영·수동 Action·실제 자산 등록 범위를 진행했다.
- 원격: `t-hajongkim/Medical-ML-SDLC`의 최신 main을 별도 클론하고 35개 셋업 파일만 허용 목록으로 반영했다.
  원본 작업 폴더의 HEAD/index/origin은 변경하지 않았고 기존 Issue Form blob
  `1da6ca210c6b5d9e5d5e0fd0beabd52399c5f63b`를 그대로 보존했다.
  `.gitattributes`에 해당 폼의 text 변환 예외를 두어 원격 CRLF 바이트를 정규화하지 않는다.
- 빌드 소스: `5c0fc144687766c0306e8b7150f3228579ac7a0f`.
  기존 버전 덮어쓰기를 피하기 위해 태그 `diabetes-prepared-v1-20261015-5c0fc14`를 사용했다.
- 실행: [run 38722808263](https://github.com/t-hajongkim/Medical-ML-SDLC/actions/runs/38722808263) 성공.
  DB metadata/feature 각 101,766행 및 asset_info 전체 대조, 모델 파일 무결성 검사, 두 GHCR push가 성공했다.
- 공개: workflow token의 package read API에서 두 visibility를 Public 확인했고 익명 OCI manifest 조회도 성공했다.
  모델의 실제 registry layer를 메모리에서만 읽어 checkpoint/source README/manifest/NOTICE 해시를 검증했다.
  모델을 역직렬화하거나 predict하지 않았다. 별도 Public 변경이나 권한 확대는 하지 않았다.
- 참조: 실제 data/model digest를 `config/assets.json`에 사후 기록한다.
  이미지 source/revision은 빌드 커밋을 유지하고 사후 참조 커밋으로 위조하지 않는다.
- 회귀: 미게시 설정 거부 테스트는 저장소의 실제 등록 상태 대신 별도 null fixture를 검사하도록 분리했다.
  null/mutable 참조 거부와 유효한 pinned 참조 수용을 모두 검증하며 기존 검사 단언을 제거하지 않는다.
- 남은 범위: 참가자 초기화는 계속 차단된 초안이다. 다음은 Issue 접수→EDA 점검→검토용 결과 PR 연결이며
  이번 등록 작업에 새 자동화·PR2·Pages를 구현하지 않았다.

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
- 당시 로컬 구현 단계의 상태: 실제 로컬 자산 수입/포장·source-only 검증과 YAML/셸/경로 점검까지만 완료했다.
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
