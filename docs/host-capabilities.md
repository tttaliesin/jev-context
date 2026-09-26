# 호스트 기능표 (V0 연결 검증)

[통합 설계 2.0](design/unified-design.md)의 V0 산출물
기능마다 `문서에 존재`·`현재 노출`·`시험 통과`를 따로 기록하고 설치만으로 시험 통과를 부여하지 않는 기준
관찰 호스트: Codex Desktop 26.917.71314, 내장 Codex 0.155.0-alpha.16.4, Windows 11, 2026-09-24 기준

## 기능별 상태

| 기능 | 문서에 존재 | 현재 노출 | 시험 통과 | 증거 |
| --- | --- | --- | --- | --- |
| 로컬 STDIO MCP `jev_context` | 예 | 예 | 예 | 2026-09-22 Desktop 대화의 도구 10종 직접 호출([기록](native-mcp-validation.md)), 2026-09-24 07:39Z 세션 로그의 `jev_context` 초기화 |
| Skill `jev-context` 단계적 로딩 | 예 | 예 | 예 | 2026-09-24 20:38 재개 요청에서 Codex가 `jev-context` Skill을 확인하고 SKILL.md를 읽은 뒤 그 절차대로 호출 |
| hooks 기능 | 예 | 예 | 예 | Desktop 로그 기능 목록의 `CodexHooks` |
| 프로젝트 hook 신뢰 절차 | 예 | 예 | 예 | 사용자 승인 뒤 `~/.codex/config.toml`에 `hooks.state` 항목별 `trusted_hash` 기록, 명령을 바꾸면 재승인 필요 |
| `UserPromptSubmit` 실행 | 예 | 예 | 예 | 2026-09-24 Desktop 세션 3회에서 hook 실행과 요청 문장 수신 |
| `UserPromptSubmit` 추가 문맥 전달 | 예 | 예 | 예 | 2026-09-24 20:31 Desktop 세션에서 사용자 메시지 뒤 `developer` 메시지로 변경 원문 3개 안내 전달 |
| `SessionStart` 실행·추가 문맥 | 예 | 예 | 예 | 같은 세션 시작 시 `developer` 메시지로 작업 ID와 복원 안내 전달, 재승인 전에는 실행되지 않음 |
| `PostToolUse` 실행·결과 교체 | 예 | 예 | 예 | 2026-09-24 Desktop 세션에서 셸 결과가 hook 요약으로 교체, 코드 모드에서는 `Script failed`로 표시. 설계 범위 밖 실험 |
| `codex exec`의 프로젝트 hook | 예 | 예 | 예 | `--dangerously-bypass-hook-trust` 실행 12회 준비·4회 완료에서 hook 기록 확인 |
| `PreCompact`·`PostCompact` | 예 | 미확인 | 미확인 | 압축 요약 지정 불가(문서), 등록하지 않음 |

## 연결에 영향을 주는 관찰

- Desktop은 hook 명령을 PowerShell로 실행, `"경로" -m ...`처럼 따옴표 친 실행 파일 뒤에 인수가 오면 구문 오류. 공백 없는 경로를 따옴표 없이 사용
- 코드 모드의 셸 도구는 모델에게 `exec`로 보이지만 `PostToolUse`에는 `tool_name: Bash`, `tool_input.command`, 문자열 `tool_response`로 전달
- hook 오류는 로그에 `hook/started`·`hook/completed`만 남고 실패 이유가 남지 않으므로 hook 쪽 자체 기록이 필요
- 시험 작업 공간처럼 저장소 밖으로 취급되는 폴더에는 프로젝트 `.codex/config.toml`의 MCP 설정이 적용되지 않음
- Codex 샌드박스에서 만든 파일에 샌드박스 전용 권한이 붙어 다른 프로세스가 지우지 못할 수 있음

## 설계 hook 구성

`.codex/hooks.json`에 `SessionStart`·`UserPromptSubmit` 두 이벤트만 등록, 구현은 [session_hooks.py](../src/jev_context/session_hooks.py)

| 계기 | 출력 | 제외 |
| --- | --- | --- |
| 세션 시작·재개·압축 후 | 저장소에서 확인한 최근 작업 ID 최대 3개와 고정 복원 안내 | 제목·목표·제약·원문·모델 판정 |
| 새 사용자 요청 | 등록 원문이 저장 revision과 달라진 경우에만 변경 개수와 `source_sync` 안내 | 파일 경로·원문, 같은 turn 중복 |

저장소는 읽기 전용으로 열고 MCP 준비를 가정하지 않으며 오류 시 아무것도 출력하지 않는 동작
실행 기록은 프로젝트 DB 폴더의 `hooks/events.jsonl`, 실제 저장소 대상 측정 125~139ms(hook 내부 16~46ms)
연결 누락 시 Skill의 명시 호출 경로 유지, 자동 연결 성공을 hook 실행만으로 주장하지 않는 기준

## Desktop 시연 기록

2026-09-24 20:31 `jev-for-agent` 새 대화, 요청은 `src`의 `def` 검색으로 이전 작업 재개가 아닌 독립 질문
- hook 재승인 뒤 `config.toml`에 `session_start`·`user_prompt_submit` 신뢰 기록 저장 확인
- 두 hook 모두 실행되고 안내가 `developer` 메시지로 들어간 것을 세션 기록에서 확인
- Codex는 재개 요청이 아니므로 `jev_context` 도구를 호출하지 않고 `rg`로 직접 답변, 답은 21개 함수 모두 정확
- 안내가 불필요한 작업에서 도구 호출을 유도하지 않은 사례이며 복원 흐름 자체의 시험은 아님

2026-09-24 20:38 `jev-for-agent` 새 대화, 요청 "지난번에 하던 작업 이어서 할게. 목표랑 제약이 뭐였는지, 어디까지 했는지 알려줘"
- 두 hook 안내가 `developer` 메시지로 전달되고 Codex가 `jev-context` Skill을 확인
- `workspace_status` → 안내의 ID로 `work_open` 호출, `work-7024f3eb…` revision 13의 목표·제약·결정 복원
- 원문 변경 안내에 따라 변경 파일 3개를 `source_sync`로 재등록한 뒤 `context_prepare` 호출
- `context_prepare` 첫 호출은 필수 근거 최소 41,653바이트가 한도 26,000을 넘어 `insufficient`, 60,000으로 재호출해 59,130바이트 묶음 수신
- 최종 답변의 목표·제약·진행·다음 순서가 저장 기록과 일치
- 안내에 경로가 없어 Codex가 `session_hooks.py`를 읽고 같은 hash 비교를 직접 실행해 변경 파일을 찾음(첫 시도 실패, 약 31초 추가)

## V1 첫 시연 (2026-09-25 06:54)

로컬 SemIf OpenVINO 엔진(`semif_openvino`, Qwen3.5-4B int8·Arc GPU)으로 프로젝트 설정을 바꾼 뒤 재개 요청 "v3 후보 지시문이 운영에 반영되었다는 주장 확인"
- Codex는 Skill → `workspace_status` → `work_open` → `context_prepare`(claim 포함) → `source_sync` → 재조회 순서로 호출하고 "미반영"을 근거 행과 함께 정확히 답변
- 모델 판단은 한 번도 반영되지 않음. 원인 두 가지를 확인하고 수정
  1. Desktop이 같은 프로젝트에 MCP 서버를 6초 간격으로 두 개 시작, 먼저 뜬 보조 서버가 모델 잠금을 잡은 뒤 종료해 실제 대화 서버는 `busy`로 포기. 배경 준비가 `engine_busy`일 때 20초 간격으로 최대 30분 재시도하도록 수정
  2. 작업이 고정한 과거 revision의 필수 근거를 판단 후 현재 파일과 비교해 모든 판단을 `source_changed`로 폐기하고 해당 근거를 `unstable`로 오표시. 읽을 때 현재 파일과 일치를 확인한 근거만 이 검사에 포함하도록 수정
- 두 수정 모두 재현 테스트 추가, 수정 전 실패·수정 후 통과 확인

## V1 두 번째 시연과 처리 예산 조정 (2026-09-25 09:02)

실제 요청 입력이 512 토큰을 넘어 1,024 토큰 모델(한국어·영어 각 28/30, 심각 오류 0, 문항당 0.84~0.89초)로 교체한 뒤 같은 요청 재시연
- 판단을 켠 `context_prepare`가 Codex 도구 제한 10초를 넘겨 실패, Codex는 판단을 끄고 재호출해 "미반영"을 정확히 답변
- 원인: 2초 판단 예산 중 첫 후보(2문항)가 1.7초를 쓰고, 두 번째 후보를 남은 0.3초로 보내 시간 초과, 시간 초과가 모델 작업자를 종료해 이후 모든 판단 유보
- 수정: 작업자가 준비 시 문항당 시간을 보고하고, 남은 시간 안에 끝낼 수 없는 판단은 보내지 않고 `judgment_deadline`으로 유보. 작업자는 유지
- 수정 후 같은 호출 3회 재현에서 매회 1.8~1.9초, 작업자 유지, 첫 후보(“v3 후보는 운영에 반영하지 않고 v2-2 유지”) 반증 판정 `contradicts` 정답, 관련성 `irrelevant` 오답
- Codex에서만 관찰된 10초 초과는 재현되지 않아 다음 시연에서 재확인

설계의 처리 예산은 "실제 장비 결과에 따라 조정할 초기 시험값"이므로 이 장비 기준으로 조정
- `.local/project.toml`의 `timeout_seconds` 5→8초, 새 설정 `judgment_seconds` 2→6초(기본값 2초 유지)
- 6초면 주장이 있을 때 후보 3개, 없을 때 7개 판단, 전체 8초로 Codex 도구 제한 10초 안

## V0 판정과 남은 보완

설계 V0의 "동일 작업 복원·호출·전달을 Desktop에서 관찰" 조건은 위 시연으로 충족
보완 항목:
1. 완료(2026-09-24): `workspace_status`의 `stale_sources`에 변경·누락 원문의 source ID·상대 경로·사유를 최대 50개 반환하고, hook 안내는 경로 없이 이 조회를 가리키도록 변경. hook과 MCP가 같은 판정 함수를 사용. hook 명령은 그대로라 재승인 불필요
2. 변경 원문이 남아 있는 동안 매 요청 반복되는 안내의 방해 여부 관찰
3. `context_prepare`의 필수 근거 크기가 기본 한도를 넘는 문제는 V1 문맥 선택에서 다룸
