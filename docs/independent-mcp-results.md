# 독립 MCP 구조 정정 결과

2026-09-27 · 앱 0.6.1. [적용 계획](independent-mcp-plan.md)의 1~6단계 중 구현·문서·로컬 검증 완료.

## 변경

- Workroom 전용 `bridge_status`/`bridge_publish`/`bridge_search`, 전용 저장 모듈, 실행 진입점, `bridge-config`/`--bridge-only`, 앱 내보내기 IPC·버튼·명령을 제거했다.
- 일반 MCP는 1.0의 8개, 2.0의 10개 도구로 복원했다. 서비스·CLI·연결 점검·온보딩·Electron UI 주요 소스는 전용 구현 이전 `def92cf`와 동일하다. 한영 전환과 영어 README는 유지한다.
- 기존 bridge DB나 연결 JSON을 자동 삭제·이관하지 않는다. 전용 데이터는 일반 검색에 포함되지 않으며 과거 직접 연결 계약은 폐기했다. 폐기된 구현·계약·검사 기록은 Git 이력 `44e3035`에 남는다.
- [독립 제품 원칙](independent-products.md), [일반 도구 흐름과 에이전트 지침](agent-memory-workflow.md), 배포 Skill을 수정했다. Workroom 파일, 사용자의 호스트 설정, 설치된 Skill은 변경하지 않았다.

## 직접 실행한 검사

| 검사 | 결과 |
|---|---|
| `pytest tests/test_agent_memory.py tests/test_mcp.py` | 5 passed |
| `python scripts/check.py` | Ruff lint·87개 파일 format 통과, 290 passed / 2 skipped, 105.74초 |
| Node 언어·창 상태 | 11 passed |
| 실제 렌더러 fixture | 51개 시나리오 통과, 페이지 오류 없음 |
| 실제 Electron 언어·온보딩·재시작 | 한국어/영어 유지, 작업 원문·설정 미리보기 보존, 모델·수집 미실행, 페이지 오류 0 |
| Windows 앱 빌드 | 성공, 아이콘 검증 통과. 패키지 16개 파일이 소스와 일치하고 전용 내보내기 코드 없음 |

`test_agent_memory.py`는 새로운 임시 프로젝트에서 두 계약 버전의 실제 MCP 프로세스를 각각 두 번 실행한다. 에이전트가 제공한 발췌의 등록·같은 mutation 재시도·원문 참조를 포함한 진행 기록 후 서버를 재시작해 같은 작업을 복원하고 한국어로 검색한다. 원문과 출처·버전·`agent_reported` provenance를 확인한다. 등록하지 않은 파일은 수집되지 않고 모델은 disabled이며 `.codex`가 생성되지 않는다. 과거 전용 DB 위치에 둔 검사 파일의 바이트가 그대로인지도 확인한다. 제거된 CLI 인수는 파서에서 거절된다.

로컬 증거: `.local/independent-mcp-validation/ui/fixture-state-report.json`, `.local/language-validation/1790509411553/report.json`. Electron 검사에서 OS 폴더 선택 응답만 격리 프로젝트로 대체했다. 사용자 실제 데이터와 프로젝트 설정을 검사용으로 쓰지 않았다.

## 판정 범위

이는 Jev의 일반 기억 도구와 독립 관리 앱 회귀 검증이다. 실제 Workroom 결과를 사용자 대신 수집하지 않았고, Codex·Claude Desktop을 자동 연결하지 않았다. 두 서버가 등록돼 있어도 에이전트 지침과 실제 호출이 있어야 기억이 저장·검색된다. 이번 변경으로 일반 코딩 효율 개선이나 모델 판단 정확도를 입증한 것은 아니다.
