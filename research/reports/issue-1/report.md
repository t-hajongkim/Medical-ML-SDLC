# 연구검토: AI 입력·점검 계획 준비

원본 Issue: https://github.com/t-hajongkim/Medical-ML-SDLC/issues/1

**입력·점검 계획을 AI가 확인하기 전이므로 EDA는 실행하지 않았습니다.**
등록된 config 매핑을 재사용하되 AI가 연구 목적·스키마·블록 입력 계약을 먼저 확인합니다.
AI가 점검 실행을 요청하면 Python이 등록된 입력·타입·매개변수와 블록 ID를 검증해 계산합니다.
매핑이 불명확하면 추정한 환자 키로 중복 0명을 보고하지 않습니다.

입력 프로필: `diabetes_prepared`
data_id: `c34ec224503e2038c79fd11c47e0f603c7e392ba46f4e8c14e1661bfaf5303fd`
스키마 context_hash: `1da8ddee19360eb49545815e2a9a8cd62f3110592fcacf8500940b260ca38aa7`
데이터 이미지: `ghcr.io/t-hajongkim/medical-ml-sdlc-db@sha256:a81f2187eaac6b465b3443df423ee369ab7e7cd216e71ab9c0a4aeb8a88ab947`
실행 코드: `f69a4e0738736945b9b57ecab312a0a2d1a17181`

공개 연구 목적·질문은 AI에 제공하지만 환자 원본 행·식별자 값·인증정보는 제공하지 않습니다.
기본 역할 검사를 통과한 Copilot이 입력 매핑을 추천합니다. 새 Python/SQL 코드는 생성·실행하지 않습니다.
등록된 후보와 근거로 해결할 수 없으면 데이터 사전·프로필을 먼저 보완해야 합니다.
이 단계에서는 Merge로 승인 계획을 확정할 수 없습니다.
