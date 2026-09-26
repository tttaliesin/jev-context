# 0.2.0 검증 기록

2026-09-22 · Windows x64 · Python 3.12.14 · Intel CPU · 메모리 약 32GB

## 코드와 데이터

- `scripts/check.py`: Ruff lint·format 통과, pytest 88개 통과·1개 건너뜀
- 마지막 출처 변경 처리와 상태 표시 수정 후 관련 회귀 검사 25개 재실행 통과
- 건너뛴 검사는 Windows symlink 생성 권한 필요, junction 경계 검사는 통과
- 1.0→2.0 DB 백업·복원·이전 계약 거절·열린 이전 서비스의 다음 호출 거절 확인
- 필수 근거 보존·평가 변경 시 활성화 취소·판단 중 원문 변경 감지·전체 응답 예산 확인
- 도구 목록 revision·신선도 검사, 필수 후보 유지, 전달 중복 키와 쓰기 전달 거절 확인
- 완료 기준별 성공 증거·대상 revision 검사, 삭제 후 완료 충족 무효화 확인
- 판단 상세 분할 조회와 출처 삭제 후 재조회 차단 확인
- uv lock 검사, 0.2.0 wheel·sdist 빌드 통과
- 배포 파일에 모델·로컬 DB·개인 설정·가상환경 미포함 확인
- 변경한 Markdown 5개 파일의 구조·링크 검사 통과

## 실제 모델과 MCP

모의 SDK와 별도로 실제 multilingual Laya 가중치를 로컬 CPU에서 실행
`scripts/smoke_mcp_v2.py`에서 공식 MCP Python 클라이언트로 서버 초기화·10개 도구 목록·10개 도구 호출 성공
`context_prepare`의 실제 모델 judgment observed와 근거 반환, 도구 추천·읽기 전달·기준 등록·조회·격리 자료 삭제까지 확인
검사 프로젝트는 임시 위치에서 생성·정리, 사용자 프로젝트의 원문은 삭제하지 않은 검사
상세 결과는 `.local/evaluations/mcp-v2-smoke.json`에 보관

2026-09-23 재실행한 한국어 진단 30문항의 단일 질문 호출 p50 약 196ms·p95 약 313ms, 준비 약 8.8초
이 수치는 짧은 진단 입력의 로컬 추론 측정이며 전체 Codex 작업 지연이나 토큰 절감 측정이 아닌 범위
관련성 6/12, 주장 관계 8/12, 도구 적합성 3/6, 총 17/30
이전 profile fingerprint의 2026-09-22 결과(p50 약 172ms·p95 약 235ms, 총 16/30)는 `.local/evaluations/laya-ko-development.2026-09-22.json`에 보관
반대 근거 오답 2개로 자동 선별 gate 실패, shadow 유지
문항은 agent-authored development fixture이며 사람 검토 heldout이나 실제 사용자 작업 30개 평가가 아닌 자료
상세 결과는 `.local/evaluations/laya-ko-development.json`에 보관

## 설치와 남은 확인

실제 프로젝트 DB schema 2 전환, 이전 DB와 프로젝트 설정 백업 생성
프로젝트 MCP 설정과 Skill을 백업 후 갱신, `codex mcp get jev_context --json`에서 enabled와 `--prepare-engine` 인자 확인
설치 Skill과 저장소 Skill의 바이트 일치 확인
현재 대화의 도구 목록에서 `jev_context`는 노출되지 않아 Desktop 세션 연결은 미검증 상태
재시작 또는 MCP 다시 연결 후 실제 호스트 도구 호출 확인 필요

한국어 품질 개선, 사람 검토 heldout 평가, 전체 작업 효율 비교와 Desktop 사용 흐름 검증 전에는 제품 완료로 판정하지 않는 결론
