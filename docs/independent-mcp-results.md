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

## 원격 검사에서 발견한 복구 결함

구조 정정 커밋 `bd89b3b`의 [첫 원격 CI](https://github.com/tttaliesin/jev-context/actions/runs/36316808014)는 기존 강제 종료 후 복구 검사 1개에서 실패했다(291 passed / 1 failed). 새 일반 기억 검사는 통과했으나, 재시작한 broker가 `engine_preparing` 대신 `engine_unavailable`을 반환했다. 이전 버전 검사에서도 이 경로의 1초 기동 시간 가정이 실패한 이력이 있다.

별도 Windows 재현 검사에서 status reader가 과거 `endpoint.json`을 연 동안 새 broker가 `endpoint.tmp`를 교체하면 `PermissionError: [WinError 5]`로 종료한다는 것을 확인했다. 수정 전 실제 프로세스 검사는 실패했고 `.t/endpoint-reader-before/test_recovery_waits_for_a_stat0/rt0/locks/shared/broker.stderr.log`에서 교체 실패 stack을 확인했다. 원 CI에는 내부 broker stderr가 없어 동일 원인인지는 추론이며, 이 재현으로 확인한 복구 결함과 구분한다.

`shared_engine.publish_endpoint`가 Windows 접근·공유 오류(5/32/33)만 최대 0.5초 동안 재시도하도록 수정했다. 기존 파일은 원자적 교체가 성공할 때까지 유지하고 영구 권한 오류는 한도 후 실패한다. 다른 종류의 권한 오류는 즉시 실패한다. 시간 기준을 완화하거나 오류 응답을 성공으로 취급하지 않았다.

수정 후 실제 열린 파일을 닫았을 때 새 broker와 worker가 복구하고 판단을 수행하는 검사, 재시도 한도/오류 구분 검사, 공유 엔진·MCP 회귀는 **18 passed**였다. 최종 `scripts/check.py`도 lint·format 및 **295 passed / 2 skipped**, 89.23초로 통과했다. 이 수치가 위 구조 정정 직후의 290개 통과 결과를 갱신한다. 수정 커밋의 원격 CI는 푸시 후 별도로 확인한다.

이어진 `68f758b`의 [원격 검사](https://github.com/tttaliesin/jev-context/actions/runs/36317306635)는 파일 교체/강제 종료 복구 검사를 통과했지만 정상 유휴 종료의 상태 조회 검사에서 실패했다(296 passed / 1 failed). 정상 종료가 `stopping=True`로 시작된 뒤 worker가 닫히는 순간, 아직 처리 중인 status 요청이 이를 `engine_worker_exited`로 표시했다. 상태 전이 검사에서 정상 종료만 수정 전 실패하며 비정상 종료는 기존 오류로 표시됨을 재현했다.

`Broker.snapshot`은 정상 종료가 이미 시작됐으면 `idle`로 표시하고, 예상하지 못한 worker 종료는 계속 `unavailable / engine_worker_exited`로 표시하도록 수정했다. 유휴 종료 조건·모델 기동·외부 MCP 계약은 변경하지 않는다.

이 상태 수정 후 공유 실행기·복구·MCP 검사 **20 passed**, 38.79초 및 Ruff lint·format 통과를 확인했다. 전체 회귀는 이 수정이 포함된 후속 커밋의 CI에서 다시 실행한다.
