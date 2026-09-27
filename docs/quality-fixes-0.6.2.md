# 0.6.2 품질검사 후속 수정

대상: [0.6.1 품질감사](quality-audit-2026-09-27.md)에서 재현한 결함. 기존 작업 DB와 사용자 설정을 보존하고 모델을 시작하지 않는 범위에서 수정했다.

## 적용 순서와 완료 기준

1. 프로젝트 선택·온보딩 연결 채택을 공통 함수로 묶는다. 후보 연결 검증 → 설정 파일 저장 → 현재 연결 교체 순서로 처리한다. 검증·쓰기·rename 실패 시 후보만 닫고 기존 설정/연결을 유지해야 한다.
2. Python bridge의 실패한 프로세스 참조를 해제한다. error·exit·close·stdin 오류·잘못된 응답은 해당 프로세스를 정리하고, 후속 요청은 새 프로세스로 시도한다. 이전 프로세스의 늦은 이벤트가 새 요청을 거절하거나 이전 응답을 전달해서는 안 된다. 실패한 작업을 자동 재실행하지 않는다.
3. Node 회귀를 CI에 포함하고 실제 Electron에서 실패→복구→재실행을 검사한다. 전체 Python·기존 언어/창 상태 검사도 수행한다.
4. 현재 호스트 MCP와 새 MCP 서버를 같은 work revision·검색 조건으로 비교한다. 새 프로세스 통과를 현재 호스트 갱신 완료로 보고하지 않는다.

## 구현 결과

- `desktop/main.cjs`의 `switchProject`를 두 전환 경로가 사용한다. 후보를 확인하는 동안 바뀐 언어 설정도 보존한다.
- `desktop/bridge-client.cjs`는 이벤트 발생 프로세스가 현재 프로세스인지 확인한다. 실패한 프로세스를 해제한 뒤 새 연결로 재시도하며, stderr도 새 연결마다 초기화한다.
- 앱 버전은 0.6.2다. 실제 `dist/JevContext` 빌드를 완료했다.
- `test_project_switch.cjs`와 `test_bridge_client.cjs`를 Windows CI에 추가했다. 별도 `test_desktop_recovery.cjs`는 격리된 실제 앱 검사다.

## 직접 확인한 검사

| 검사 | 결과 |
|---|---|
| Ruff lint·format / 전체 Python | 297 passed, 2 skipped, 89.69초. skip은 Windows symlink 권한 조건 |
| Node 창 상태·언어·전환·복구 | 26 passed. 신규 회귀 15개 포함 |
| 실제 Electron 프로젝트 선택 | 설정 쓰기 실패 시 디스크·backend·새로고침한 화면 모두 A 유지, 복구 후 B 전환, 재실행 후 B 유지 |
| 실제 Electron 온보딩 채택 | 같은 저장 실패·복구·재실행 검사 통과 |
| 실제 Python bridge | ENOENT 후 정상 실행기로 변경하면 같은 BridgeClient 인스턴스에서 overview 성공 |
| 실제 Electron 한영 회귀 | 온보딩 왕복·원문/nonce 보존·단축키·재실행 후 언어 유지, 페이지 오류 0 |

Electron 실패 검사는 실제 파일 쓰기 실패를 사용했다. OS 파일 선택 응답은 테스트 경로로 대체했고, 프로젝트 전환은 실제 preload IPC로 호출했다. rename 실패는 Node 검사에서 주입했다. 테스트 프로젝트의 모델은 비활성화되어 있다.

로컬 증거:

- `.local/recovery-validation/1790513561207/report.json`: 프로젝트 선택·온보딩·실제 Python 복구. `project-preserved.png`는 직접 열어 A 유지 화면을 확인했다.
- `.local/language-validation/1790513688904/report.json`: 기존 한영 기능 회귀.
- `.local/quality-062/native-mcp-summary.json`, `fresh-mcp-summary.json`: 현재/새 MCP 비교 집계.

## 남은 호스트 연결 갱신

현재 프로세스 명령줄은 이 저장소의 `.venv/Scripts/python.exe -m jev_context serve --config .local/project.toml --prepare-engine`에 해당했다. venv 실행기와 실제 Python 자식이 쌍으로 보였고, 실행 시작은 9월 26일 21:08 또는 9월 27일 10:12–10:24였다. `budget.py`의 이번 수정 시각인 9월 27일 17:27보다 이르다. 새 Python의 모듈 경로도 이 저장소 `src/jev_context/budget.py`로 확인했다. 오래 실행 중인 MCP가 이전 모듈을 유지하는 것이 응답 불일치와 부합한다. 여러 연결 중 현재 대화의 정확한 PID는 식별하지 않았다.

work revision 8에서 같은 질문·source 2개·예산 12,000바이트·judge off로 재검사했다.

| 관찰 | 현재 대화의 MCP | 새 MCP stdio 서버 |
|---|---|---|
| 후보 / 반환 근거 | 25 / 0 | 25 / 0 |
| outcome | `ok` | `partial` |
| retrieval_status | 없음 | `budget_limited` |
| budget_omitted_candidates | 없음 | 25 |

새 서버는 같은 DB를 읽기 전용으로 열었고 모델을 연결하지 않았다. 실제 MCP SDK의 stdio 통신과 text/structuredContent 일치까지 검증했다. 현재 대화의 MCP 갱신은 **미완료**다. 이 세션에는 호스트 MCP 재시작 도구가 제공되지 않아 여러 서버를 임의로 종료하지 않았다.

[공식 MCP 설정 안내](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)는 설정 화면의 MCP 서버에서 Restart를 안내한다. Codex에서 해당 재시작을 사용하거나 앱을 완전히 종료·재실행한 뒤, 현재 대화의 실제 `context_prepare` 응답에 `retrieval_status`와 `budget_omitted_candidates`가 포함되는지 확인해야 한다. 이번 수정은 이 재확인을 완료했다는 의미가 아니다.
