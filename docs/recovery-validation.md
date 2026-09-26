# 프로젝트 이동 복구와 실제 사용 검증

후속: 이 문서에서 확인한 모델 준비 경합은 [모델 수명 재설계](model-lifecycle-design.md)와 [적용 검증](model-lifecycle-validation.md)에서 다룬다. 아래의 최초 실측·문제 발견 기록은 보존한다.

2026-09-26, 현재 Windows 프로젝트에서 발생한 연결 장애를 복구하는 작업이다.
기존 구현 기록의 Modal 평가와 현재 SemIf OpenVINO 로컬 실행을 구분한다.
이 문서는 아래 실측 결과를 기록하며 일반 코딩 성능 향상이나 자동 선별 승격을 주장하지 않는다.

복구와 저장·복원은 검증했다. 실제 결함 수정 비교에서는 세 조건이 모두 같은 수정으로 성공했고, 모델의 추가 품질·효율 이익은 입증되지 않았다. 모델은 shadow를 유지한다.

## 복구한 경계

- 설정의 상대 `project_root`, `data_root`, `engine.profile_file`, `engine.evaluation_file`은 설정 파일이 있는 폴더를 기준으로 해석한다. 기존 절대 경로도 유지한다.
- 이동한 개발 환경의 editable `.pth`를 상대 `../../../src`로 수정해 다른 작업 폴더에서도 모듈을 불러온다.
- 프로젝트 MCP 명령, 두 hook 명령, 모델 파일·Python·잠금 경로를 현재 폴더에 맞췄다. 모델 가중치와 shadow 설정은 유지했다.
- 기존 DB는 project ID·이전 root·schema·수집 정책이 일치하는 경우에만 명시적으로 이전한다. 백업과 무결성 확인을 마친 다음 한 트랜잭션에서 root와 정책 revision을 변경한다.
- 이전은 ingestion/schema 잠금을 유지하며 작업·이벤트·원문 이력을 보존한다. 이전 문맥 packet과 mutation 응답은 무효화하여 현재 상태로 재조회한다.
- 세션 hook은 project ID뿐 아니라 DB의 프로젝트 root도 검사한다. 같은 ID라도 다른 root의 상태는 주입하지 않는다. 수정 전 새 회귀 검사 5개 중 3개가 실패했고 수정 후 모두 통과했다.

실제 이전 전 백업은 기존 DB와 같은 접근 제한 폴더의 `pre-relocation-20260926.sqlite`다.
복구 전 1개 work, 13개 event, 6개 evidence, 16개 source, 28개 source revision, 129개 chunk의 모든 기존 행이 동일하게 남아 있음을 확인했다.
현재 DB와 백업 모두 integrity_check=ok, foreign_key_check 오류 0건이다.

## 경로를 옮길 때

서버를 종료하고 기존 DB를 포함한 프로젝트를 옮긴다. 설정 파일이 `.local/project.toml`에 있을 때 다음 상대 경로를 사용할 수 있다.

```toml
project_root = ".."
data_root = "state"
# 기존 project_id, allowed_paths, contract_version은 유지한다.
# policy_revision은 기존 값보다 높게 지정한다.
[engine]
state = "shadow"
profile_file = "semif-ov-profile.json"
```

프로필 내부 모델·Python·lock 경로와 MCP/hook 실행 경로는 별도로 새 위치를 확인해야 한다.
기존 DB에 대해 원래 경로를 명시하여 실행하며, 존재하는 백업 파일은 덮어쓰지 않는다.

```powershell
.venv\Scripts\python.exe -m jev_context.relocation `
  --config .local\project.toml `
  --expected-old-root 'C:\previous\project' `
  --backup-path 'C:\current\project\.local\state\projects\project-ID\before-move.sqlite'
```

이전 기능은 allowlist 변경이나 schema 변경을 함께 허용하지 않는다. DB 접근 권한을 완화하지 않으므로 기존 DB 소유자의 실행 권한이 필요하다.

## 실제 검증 방법

`scripts/validate_recovery.py`는 공식 MCP stdio client로 기존 work를 복원하고, 복구 work와 실제 장애 원문을 기록한다.
동일 work·query·source revision·응답 예산으로 모델 off와 required 판단을 호출하고, 서버 종료·재시작 후 목표·제약·진행·원문 hash를 다시 확인한다.
모델 준비 시간, 각 호출 시간, 원문/프로필/구현 hash, observed·abstained를 원시 응답과 함께 저장한다.
모델을 사용할 수 없어도 저장·복원 검증은 계속하되 판단 성공으로 처리하지 않는다.

```powershell
.venv\Scripts\python.exe -X utf8 scripts\validate_recovery.py `
  --config .local\project.toml `
  --output .local\recovery-validation\report.json `
  --prior-work-id <기존-work-ID> `
  --source docs/recovery-case.md
```

MCP stdio 검증과 현재 Desktop 대화의 도구 노출은 별개다. 변경된 hook 명령에는 호스트의 신뢰 확인이 필요하며, 이 검증 도구는 신뢰 설정을 우회하지 않는다.

## 기록 위치

- `.local/recovery-20260926/relocation.json`: 실제 DB 이전 결과
- `.local/recovery-20260926/preservation.json`: 기존 행과 백업 무결성 대조
- `.local/recovery-20260926/live/report.json`: MCP·모델·재시작 실측
- `.local/recovery-20260926/hook-binding-baseline.json`: 실제 hook 결함의 수정 전 실패

## 로컬 MCP와 모델 실측

공식 MCP client에서 도구 10종의 schema를 확인하고, 기존 작업 revision 13 및 새 복구 작업을 조회했다.
원문 수집·고정 revision 읽기·진행 기록 후 서버를 종료하고 새 프로세스에서 목표·제약·진행·revision·원문 hash의 일치를 확인했다.

| 같은 질문·원문의 조건 | 호출 시간 | MCP 응답 바이트 | 판단 |
| --- | ---: | ---: | --- |
| 저장·검색, 모델 off | 0.062초 | 13,624 | skipped |
| 저장·검색 + 로컬 모델 required | 3.281초 | 20,874 | 4개 모두 observed |

네 근거 모두 relevant로 응답했고 잘린 입력·미판단 후보는 없었다. 관련 자료만 있는 이 사례로 무관 자료 배제 품질을 평가할 수는 없다.
shadow 유지 상태라 일반 근거 제외·축약은 적용되지 않았다. 이번 호출에서 모델 주석은 응답을 늘렸으며 정확도·효율 개선 근거로 해석하지 않는다.
모델의 새 준비 시작부터 ready 관측까지 238.359초, 준비와 일부 저장·검색을 병행한 전체 복원 검증은 243.156초였다.
ready 시간은 컴파일만의 측정값이 아니라 manifest 검사·프로세스·준비·관측 대기를 포함한다.

위 최초 실측에서는 Desktop 연결을 확인하지 않았다. 이후 사용자의 hook 신뢰 적용 뒤 아래와 같이 native 호출을 별도 검증했다.

## 실제 결함 수정의 세 조건 비교

기존 `session_hooks.open_store`의 root 검사 누락을 수정 전 상태로 복사했다. 결함을 새로 주입하지 않았다.
실행 전 소스·과제·실행기·숨은 검사·조건별 문맥의 SHA-256을 고정했다.
E0는 추가 문맥 없음, E1은 위 MCP off 응답, E2는 required 모델 응답을 그대로 전달했다.
각 조건은 별도 복사본에서 `codex exec --ephemeral --sandbox workspace-write`로 실행했고, 같은 CLI 기본설정을 사용했다.
모델·추론 설정 override는 없었지만 실행 로그에 실제 모델 ID가 없어 동일 모델임을 독립 확인하지는 못했다.

| 조건 | 공개 + 숨은 검사 | Codex 실행 시간 | 누적 입력 토큰 | 캐시 제외 입력 토큰 | 출력 토큰 |
| --- | ---: | ---: | ---: | ---: | ---: |
| E0 기본 Codex | 8 + 5 통과 | 141.984초 | 242,674 | 23,026 | 5,399 |
| E1 저장·검색 문맥 | 8 + 5 통과 | 151.125초 | 288,498 | 23,794 | 4,945 |
| E2 모델 판단 문맥 | 8 + 5 통과 | 145.984초 | 273,611 | 50,507 | 6,786 |

세 조건의 최종 수정 파일 hash가 같고 허용 범위 외 변경은 없었다.
토큰은 CLI가 반환한 누적 usage이며 단일 프롬프트 길이나 금액이 아니다. 캐시 제외 입력은 input_tokens에서 cached_input_tokens를 뺀 값이다.
E2의 캐시 제외 입력은 E0의 약 2.19배였다. E2에 사용한 문맥 생성 3.281초 및 모델 준비 238.359초는 표의 Codex 실행 시간에 포함되지 않는다.
준비 시간은 매 호출에 부과되는 값으로 가정하지 않으며, 장기 실행에서의 분산 비용은 이번에 측정하지 않았다.

조건별 1회, 고정 실행 순서 E1→E0→E2, 캐시 차이, 원인이 이미 특정된 작은 결함이라는 한계가 있다.
세 실행 모두 PATH에서 Python을 찾지 못해 자체 실행 검사를 하지 못했고 정적 확인 후 종료했다. 표의 시간에는 해당 탐색 실패가 포함된다.
외부 채점기는 프로젝트 Python으로 각 복사본을 실제 import한 경로를 확인한 뒤 공개 8개와 숨은 5개 검사를 실행했다.
숨은 검사는 과제 수행자에게 제공하지 않았지만 평가 설계는 agent 작성이며 사람의 독립 검토나 엄격한 OS 단위 격리는 아니다.
따라서 이 결과는 연결·문맥 전달·실제 결함 수정의 작동 확인이며 일반적인 코딩 성능의 인과 비교가 아니다.

원시 자료는 `.local/r3/manifest.freeze.json`, `results.json`, `runs/*/stdout.jsonl`, `receipts/*/tests.xml`에 있다.
재실행 방법과 동결 검증은 `scripts/compare_recovery.py`에 있다. 이 결과를 자동 선별 승격 근거로 사용하지 않는다.

## 전체 검사와 호스트 연결 상태

현재 소스의 Ruff·format 검사와 전체 pytest가 통과했다: **177 passed, 1 skipped**.
skip 1개는 Windows symlink 권한 조건이다. 부모 pytest의 임시 폴더 설정이 하위 코딩 채점에 전달되는 문제도 수정했다.
실패한 중간 시도의 로그는 보존했으며 최종 결과는 `.local/recovery-20260926/checks.log`와 `checks.json`이다.
검증 대상 소스 집합 hash는 `c7ba3a0ec4ba1ea4b5fc1d92c3d14804d1cd4a0c74b448abb40fc9e720da15a3`이다.

실제 사용자 계정에서 별도 Codex app-server의 `config/read(cwd=프로젝트)`로 수정한 MCP 명령과 enabled=true를 확인했다.
샌드박스 임시 계정의 조회는 다른 사용자 설정을 읽어 서버가 보이지 않았으며, 이를 실제 사용자 등록 실패로 판단하지 않는다.
최초 `hooks/list`는 SessionStart와 UserPromptSubmit 두 hook을 모두 **untrusted**로 반환했다.
당시 결과는 `.local/recovery-20260926/native-config-before-trust.json`에 보존했다. 최초 검증 프로세스는 종료했다.

사용자가 “신뢰했어”라고 알려온 뒤 실제 사용자 계정의 `hooks/list`에서 두 hook 모두 **trusted**임을 확인했다.
이 절차는 hook 정의별 신뢰를 요구하는 [공식 Codex hook 규칙](https://learn.chatgpt.com/docs/hooks)에 따른다.
현재 대화에 native MCP 도구 10종이 노출됐고 `workspace_status`, `work_open`, `source_sync`, `context_prepare`를 직접 호출해 모두 outcome=ok를 받았다.
`context_prepare`는 현재 파일 hash를 확인한 보고서와 기존 제약·반대 근거를 반환했다. 이 호출은 모델 off이며 모델 판단 성공으로 세지 않는다.

이번 UserPromptSubmit에서 변경 자료 2건의 안내가 실제 developer 문맥에 주입됐다. hook 실행 로그의 sync_hint 및 changed_sources=2와 일치한다.
안내된 README.md와 docs/implementation-v2.md를 동기화한 뒤 stale_sources=0을 확인했다.
SessionStart hook은 trusted인 것까지 확인했으며, 신뢰 적용 후 새 SessionStart 실행 자체는 이번 턴에서 관측하지 않았다.
신뢰 조회는 `native-config.json`, 실제 native 응답과 hook 관측은 `native-desktop-receipt.json`에 저장했다.

추가로 현재 MCP의 모델 상태는 preparing, 마지막 준비 오류는 engine_busy였다. 같은 프로필을 사용하는 복수 MCP 프로세스와 모델 worker가 실행 중이다.
다른 프로세스의 잠금은 해제하거나 삭제하지 않았다. 현재 세션의 모델 판단은 준비 완료가 확인되기 전까지 성공으로 간주하지 않는다.
서버 응답의 host_integration.not_observed/not_installed는 구현상 고정 문자열로, 이번 실제 호스트 관측 결과를 반영하는 상태 검사가 아니다.

복구 work `work-9fa8235122db454fad961db83520a9f5`에는 검사 evidence와 revision별 결과를 저장했다.
전체 검사는 passed이며 Desktop MCP 재노출·hook 신뢰 기준도 관측 근거로 passed 처리한다.
모델 준비 경합은 별도 미해결 항목으로 보존하여 전체 사용 경로의 완료를 과장하지 않는다.
