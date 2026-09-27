# Workroom 연동 진행·결과

2026-09-27 · 제공자·앱 구현 및 격리 검증 완료.

- 원칙과 계약을 읽고 Jev 저장소에 같은 문서를 저장했다. wire 계약 변경 없음.
- 제공자 적용 계획: [workroom-bridge-plan.md](workroom-bridge-plan.md).
- 구현 범위: 3개 MCP 도구, origin/product 격리 reported 저장소, env 없는 실행 설명 생성, 앱 내보내기.
- 사용자 프로젝트 DB·Codex 설정은 검사용으로 변경하지 않는다. 모든 전송/검색 검사는 별도 임시 프로젝트를 사용한다.
- 제공자·CLI·앱 내보내기 구현과 전체 로컬 회귀 검사를 완료했다. Electron 앱 버전은 0.6.0이다.

## Workroom에서 사용할 격리 예제

Jev 저장소 루트에서 아래 명령을 실행한다. 기존 예제와 충돌하지 않도록 새 이름을 사용한다. Workroom에는 이 `project` 폴더를 제품 폴더로 등록하고 생성된 `workroom-jev.json`을 선택한다. 테스트용 별도 Workroom DB를 사용한다.

```powershell
$sample = Join-Path (Get-Location) '.local/workroom-bridge-example'
New-Item -ItemType Directory -Path (Join-Path $sample 'project') -ErrorAction Stop
.\.venv\Scripts\python.exe -X utf8 -m jev_context init --config "$sample/project.toml" --project-root "$sample/project" --data-root "$sample/data"
.\.venv\Scripts\python.exe -X utf8 -m jev_context bridge-config --config "$sample/project.toml" --output "$sample/workroom-jev.json"
```

새 구성 생성과 설명 내보내기는 기존 사용자 DB·Codex 설정을 수정하지 않는다. `--output`은 기존 파일을 덮어쓰지 않는다. 출력 옵션을 생략하면 stdout에 JSON만 출력한다. 실행 설명의 `command`, `args`, `cwd`를 그대로 사용하고 별도 env를 추가할 필요가 없다.

설명은 현재 Jev 설치의 절대 `bridge_entry.py`를 `-I -X utf8`로 실행한다. 현재 작업 폴더와 PYTHONPATH에 의존하지 않는다. 설치 위치·Python 환경을 옮기면 설명 파일을 다시 내보낸다. 이 진입점은 `serve --bridge-only`이며 모델 준비 옵션을 거절한다.

## 저장·검색의 구체 동작

- Jev `data_root/projects/<project_id>/workroom/workroom-bridge.sqlite`에만 파생 기억을 쓴다. 최초 publish 전 status/search는 저장소를 생성하지 않는다.
- provenance는 `reported`로 고정한다. UUID는 표준 소문자 형식으로 정규화하며 내용은 원문을 보존한다.
- title/summary/contribution/limitations의 검색 사본에 Unicode NFKC와 casefold를 적용한다. query를 공백으로 나눈 각 토큰이 포함된 결과를 반환한다(AND). SQL/FTS 구문으로 해석하지 않는다. 최신 변경 시각 내림차순, memoryId 순으로 limit+1을 읽어 truncated를 결정한다.
- 일반 Jev MCP 목록은 계약 1.0의 8개+bridge 3개, 계약 2.0의 10개+bridge 3개다. bridge 전용 실행은 3개만 노출한다. 기존 서비스 도구의 입력·응답 계약은 그대로다.
- 오류는 MCP `isError=true`와 단일 JSON text `{contract,error:{code,message}}`로 반환한다. 외부 호출의 revision·ID는 Jev 독립 작업의 revision·ID가 아니다.

## 검증 결과

새 검사는 모델 없이 실제 MCP 프로세스를 env 없는 실행 설명으로 두 번 시작해 publish/search/동일 ID 재전송을 확인한다. 검사용 모델 프로필을 존재하지 않는 경로로 설정해 bridge가 이를 읽거나 준비하지 않는지도 검사한다. 범위 격리, 동시 중복 전송, read-only, storage 오류, 기존 Jev 작업 DB 불변 검사를 포함한다.

| 검사 | 관찰 결과 |
|---|---|
| Python `scripts/check.py` | Ruff lint·format 통과, pytest 313 passed / 3 skipped |
| 렌더러 `scripts/test_desktop_states.cjs` | 51개 시나리오 통과 |
| 실제 Electron `scripts/test_bridge_export.cjs` | 한영 버튼, 취소, JSON 내보내기, 재덮어쓰기 거부, 모델·Codex·bridge DB 미변경 통과. 기본 저장창 선택은 테스트에서 대체 |
| 실제 Workroom 소비자 → Jev | 프로젝트 식별, created→unchanged→updated, 같은 memoryId, 최신 revision 검색, 내부 evidence 제외, 다른 제품 격리, 연결 해제 후 자체 기록·검색 유지 |

로컬 증거: `.local/bridge-export-validation/1790508042349/report.json`, `.local/bridge-validation/ui/fixture-state-report.json`. Workroom 왕복 검사는 그 저장소의 `scripts/check-jev-live.mjs`가 새 격리 프로젝트와 두 DB에서 실행했으며 `work/jev-live-H3pdof/result.json`을 읽어 위 5개 결과와 `modelsStarted: false`를 확인했다. 실행 담당 Workroom 대화는 별도 95개 단위·통합 및 15개 Electron 검사 통과를 보고했다. 이 수치는 Jev의 직접 실행 검사와 구분한다.

검증은 수동 전송·수동 검색 계약에 한정한다. 기존 에이전트 문맥 자동 공급, 일반 코딩 효율 개선, 실제 사용자의 Codex 연결 완료, 독립 설치 패키지 배포를 증명하지 않는다. 초기 v1에는 동기 삭제가 없으며 연결 해제만으로 이미 전송한 기억이 삭제되지 않는다.
