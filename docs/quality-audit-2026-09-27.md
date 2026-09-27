# Jev 0.6.1 품질검사

검사일: 2026-09-27. 대상: `fc21a6c443528e9bc5dc9ee994c1d5b878ef4ef0`, Windows x64, Python 3.12, Electron 앱 0.6.1.

후속: 제품 결함 2건의 수정·회귀 검증은 [0.6.2 수정 결과](quality-fixes-0.6.2.md)에 있다. 아래는 수정 전 감사 결과를 그대로 보존한 기록이다.

판정: 기본 동작과 전체 회귀는 통과했으나 **제품 결함 2건과 현재 MCP 실행 환경의 응답 불일치 1건**을 확인했다. 아래 P1을 먼저 해결해야 한다. 이번 작업은 감사이며 제품 코드는 수정하지 않았다.

## 확인한 결함

### P1 — 프로젝트 설정 저장 실패가 실제 연결을 되돌리지 못함

위치: `desktop/main.cjs:139–142`, 같은 순서의 온보딩 adopt 경로 `97–102`.

새 프로젝트의 조회 성공 후 기존 bridge를 닫고 메모리의 bridge/settings를 교체한 다음 설정을 저장한다. 저장이 실패하면 IPC는 실패를 반환하지만 실제 연결은 이미 새 프로젝트다. renderer는 성공 응답을 받지 못해 이전 프로젝트 표시를 유지하며 이후 새로고침에서야 새 프로젝트로 바뀐다. 디스크 설정은 이전 프로젝트라 재실행 시 선택도 달라질 수 있다. 이 사이 조회·모델 제어 요청이 화면과 다른 프로젝트로 전달될 수 있다.

실제 0.6.1 Electron 앱에서 임시 A/B 프로젝트로 재현했다. A에서 B로 전환할 때 테스트 userData의 `settings.json.pending`을 디렉터리로 만들어 저장을 실패시켰다. 즉시 캡처한 화면 상태는 A, 실제 `overview`의 project_root는 B, 저장된 settings.json은 변경되지 않았다. OS 파일 선택 응답만 테스트 B 경로로 대체했다. 실제 모델은 시작하지 않았다. 스크린샷은 다음 자동 조회가 반영된 뒤여서 B와 ‘프로젝트 열기 실패’가 함께 보인다.

수정 방향: 후보 bridge를 검증한 뒤 설정 저장까지 성공해야 연결과 메모리 상태를 전환한다. 실패하면 후보를 닫고 기존 연결/설정/화면을 유지한다. 프로젝트 선택과 온보딩 adopt를 함께 수정하고 파일 쓰기·rename 실패 각각의 회귀를 추가한다.

증거: `.local/quality-061/project-save-1790511708575/report.json`, `project-save-failed.png`. 재현 스크립트: `.local/quality-061/probe-project-save.cjs`.

### P2 — Python 시작 실패 후 재시도가 실패한 child를 재사용함

위치: `desktop/bridge-client.cjs:56–59`와 `start()`의 `if (this.child) return`.

spawn 실패의 `error` 이벤트는 현재 대기 요청만 거절한다. child 참조는 `exit`에서만 초기화한다. 실제 ENOENT 재현에서 `exit`로 초기화되지 않은 참조가 남아 다음 요청이 새 프로세스를 시작하지 않았다. 환경을 복구해도 같은 연결 인스턴스의 재시도는 실패한 stdin에 쓰고 제한시간까지 기다린다.

BridgeClient를 그대로 불러 누락 실행기 경로로 요청했고 ENOENT를 확인했다. 그 후 실행기 경로를 정상 Python으로 바꿨지만 같은 실패 child가 유지됐다. 두 번째 요청은 **12,001ms 후 시간 초과**했다. 이 재현은 bridge 클래스 수준이며 화면에서 실제 Python 파일을 교체한 시나리오까지 검사한 것은 아니다.

수정 방향: `error`/`close`에서 해당 child의 소유 여부를 확인해 참조와 스트림을 정리하고 새 요청이 다시 spawn하도록 한다. 오래된 child의 후속 이벤트가 새 요청을 거절하지 않게 분리한다.

증거: `.local/quality-061/bridge-retry.json`. 재현 스크립트: `.local/quality-061/probe-bridge-retry.cjs`.

## 현재 실행 환경에서 확인한 문제

### P1 — 연결된 MCP와 최신 코드의 문맥 누락 판정이 다름

같은 QA work revision 2, 같은 등록 source 2개, 같은 질문, 모델 off, 12,000바이트 예산으로 비교했다. 최신 코드는 별도 Python 프로세스에서 동일 DB를 `read_only=True`로 열어 검사했으며 DB·설정 수정과 모델 실행은 하지 않았다.

| 관찰 | 현재 대화의 MCP | 디스크의 최신 Service |
|---|---|---|
| 후보 수 | 25 | 25 |
| 실제 반환 근거 | 0 | 0 |
| 본문 예산으로 제외 | 22 | 22 |
| 전송 예산으로 추가 제외 | 3 | 3 |
| outcome | `ok` | `partial` |
| retrieval_status | 없음 | `budget_limited` |
| budget_omitted_candidates | 없음 | 25 |

현재 연결의 응답은 `src/jev_context/budget.py:70–86`에 있는 최신 누락 메타데이터와 판정을 반영하지 않는다. 오래 실행 중인 서버 또는 다른 설치 경로가 원인일 가능성이 있지만 해당 프로세스의 코드 로드 위치는 이 검사에서 확정하지 않았다. 새 프로세스의 read-only 차이로 judgment inspection 참조가 달라질 수 있으나, 위 후보·누락 수는 같고 누락 메타데이터와 partial 판정 자체는 현재 코드에 명시돼 있다.

조치: 실제 호스트의 서버 실행 경로를 확인하고 MCP 재연결 후 같은 요청에서 `partial / budget_limited`가 나오는지 확인해야 한다. 파일·CI 검사 성공을 현재 호스트에 최신 코드가 적용됐다는 증거로 사용하면 안 된다.

증거: `.local/quality-061/native-context-summary.json`, `.local/quality-061/fresh-context-summary.json`. 전체 원문은 복사하지 않고 비교에 필요한 집계만 저장했다.

## 통과한 검사

| 항목 | 이번 검사 결과 |
|---|---|
| Ruff lint·format 및 전체 Python | 297 passed / 2 skipped, 100.61초 |
| Node 언어·창 상태 | 11 passed |
| 실제 renderer fixture | 51개 시나리오 통과 |
| 실제 Electron 앱 | 온보딩 한영 전환·미리보기/nonce 보존·작업 원문 보존·명령/단축키·재실행 후 언어 유지 통과, 페이지 오류 0 |
| 배포 파일 | 앱 버전 0.6.1, 패키지 16개 파일이 현재 desktop 소스와 일치 |
| 모델·데이터 경계 | 실제 모델/원격 GPU 시작 없음. 기능 실패 재현은 격리 프로젝트/userData 사용. 현재 데이터의 비교 조회는 read-only |

전체 Python 명령: `PYTEST_ADDOPTS='--basetemp=.t/qa-061 -p no:cacheprovider'` 환경에서 `python -X utf8 scripts/check.py`. 로컬 skip 2개는 symlink 생성 권한 조건의 검사다. 이전 동일 커밋 [원격 CI](https://github.com/tttaliesin/jev-context/actions/runs/36317623601)는 299개 통과했으며 이번 로컬 결과와 구분한다.

UI 증거: `.local/quality-061/ui/fixture-state-report.json`, `.local/language-validation/1790511585038/report.json`. Electron 테스트는 사용자 실제 설정을 바꾸지 않고 격리 userData를 사용했다. 프로젝트 저장 오류 화면도 직접 열어 확인했다.

## 판정의 한계

자동 검사 통과는 위 실패 경로까지 보장하지 않았다. 실제 모델의 한국어 판단 정확도·코딩 효율, Claude Desktop에서의 실제 호출, 다른 PC의 독립 설치 경험은 이번 검사 대상이 아니다. 제품 소스 수정·커밋·푸시는 수행하지 않았다. 보고서와 로컬 재현 자료만 추가했다.
